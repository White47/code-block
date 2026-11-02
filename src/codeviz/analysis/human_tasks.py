"""Bounded AST-time task facts backed by existing symbols and flow nodes."""

from __future__ import annotations

import ast
import json
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256

from codeviz.core import Confidence, EdgeType, NodeKind, SourceAnchor

from .flow import FlowAnalysis
from .lexical_scopes import ImplicitScopeVisitor, ScopeLocations, definition_expressions
from .model import ModelAnalysis
from .python import ParsedFile
from .resolution import ResolutionResult
from .symbols import SymbolReference
from .training import TrainingAnalysis

MAX_HUMAN_TASK_FACTS = 512
MAX_FACT_REFERENCES = 8
MAX_ARTIFACT_KEY_BYTES = 160
_ID_PREFIX = "human_task_"


class StaticTaskKind(StrEnum):
    EVALUATION_HELPER = "evaluation_helper"
    LEARNING_RATE_UPDATE = "learning_rate_update"
    GRADIENT_ACCUMULATION = "gradient_accumulation"
    INPUT_ENCODING = "input_encoding"
    TOKEN_SAMPLING = "token_sampling"
    OUTPUT_DECODING = "output_decoding"
    RESIDUAL_MERGE = "residual_merge"
    LAST_TOKEN_SELECTION = "last_token_selection"


class ArtifactAccessKind(StrEnum):
    READ = "read"
    WRITE = "write"


class HumanEntryKind(StrEnum):
    DATA_PREPARATION = "data_preparation"
    GENERATION = "generation"


def _valid_id(value: object) -> bool:
    return bool(
        type(value) is str and value.startswith(_ID_PREFIX)
        and len(value) == len(_ID_PREFIX) + 64
        and all(char in "0123456789abcdef" for char in value[len(_ID_PREFIX):])
    )


def _valid_refs(values: object, *, required: bool = False) -> bool:
    return bool(
        type(values) is tuple
        and (not required or values)
        and len(values) <= MAX_FACT_REFERENCES
        and len(set(values)) == len(values)
        and all(type(value) is str and value for value in values)
    )


@dataclass(frozen=True, slots=True)
class StaticTaskFact:
    id: str
    owner_symbol_id: str
    kind: StaticTaskKind
    source_anchor: SourceAnchor
    control_node_id: str
    callsite_ids: tuple[str, ...]
    target_symbol_ids: tuple[str, ...]
    confidence: Confidence

    def __post_init__(self) -> None:
        if (not _valid_id(self.id)
                or type(self.owner_symbol_id) is not str or not self.owner_symbol_id
                or not isinstance(self.kind, StaticTaskKind)
                or not isinstance(self.source_anchor, SourceAnchor)
                or type(self.control_node_id) is not str or not self.control_node_id
                or not _valid_refs(self.callsite_ids)
                or not _valid_refs(self.target_symbol_ids)
                or self.confidence not in (Confidence.EXACT, Confidence.PROBABLE)):
            raise ValueError("invalid static task fact")


@dataclass(frozen=True, slots=True)
class ArtifactAccessFact:
    id: str
    owner_symbol_id: str
    kind: ArtifactAccessKind
    artifact_key: str
    source_anchor: SourceAnchor
    control_node_id: str
    callsite_id: str | None
    confidence: Confidence

    def __post_init__(self) -> None:
        if (not _valid_id(self.id)
                or type(self.owner_symbol_id) is not str or not self.owner_symbol_id
                or not isinstance(self.kind, ArtifactAccessKind)
                or type(self.artifact_key) is not str or not self.artifact_key
                or len(self.artifact_key.encode("utf-8")) > MAX_ARTIFACT_KEY_BYTES
                or any(ord(char) < 32 for char in self.artifact_key)
                or not isinstance(self.source_anchor, SourceAnchor)
                or type(self.control_node_id) is not str or not self.control_node_id
                or (self.callsite_id is not None
                    and (type(self.callsite_id) is not str or not self.callsite_id))
                or self.confidence not in (Confidence.EXACT, Confidence.PROBABLE)):
            raise ValueError("invalid artifact access fact")


@dataclass(frozen=True, slots=True)
class HumanTaskEntryFact:
    id: str
    root_symbol_id: str
    kind: HumanEntryKind
    source_anchor: SourceAnchor
    evidence_fact_ids: tuple[str, ...]
    confidence: Confidence

    def __post_init__(self) -> None:
        if (not _valid_id(self.id)
                or type(self.root_symbol_id) is not str or not self.root_symbol_id
                or not isinstance(self.kind, HumanEntryKind)
                or not isinstance(self.source_anchor, SourceAnchor)
                or not _valid_refs(self.evidence_fact_ids, required=True)
                or self.confidence not in (Confidence.EXACT, Confidence.PROBABLE)):
            raise ValueError("invalid human task entry fact")


@dataclass(frozen=True, slots=True)
class HumanTaskAnalysis:
    entries: tuple[HumanTaskEntryFact, ...] = ()
    tasks: tuple[StaticTaskFact, ...] = ()
    artifacts: tuple[ArtifactAccessFact, ...] = ()
    omitted_fact_count: int = 0

    def __post_init__(self) -> None:
        all_facts = (*self.entries, *self.tasks, *self.artifacts)
        fact_ids = (
            *(item.id for item in self.entries),
            *(item.id for item in self.tasks),
            *(item.id for item in self.artifacts),
        )
        if (type(self.entries) is not tuple
                or any(not isinstance(item, HumanTaskEntryFact) for item in self.entries)
                or type(self.tasks) is not tuple
                or any(not isinstance(item, StaticTaskFact) for item in self.tasks)
                or type(self.artifacts) is not tuple
                or any(not isinstance(item, ArtifactAccessFact) for item in self.artifacts)
                or len(all_facts) > MAX_HUMAN_TASK_FACTS
                or len(set(fact_ids)) != len(all_facts)
                or type(self.omitted_fact_count) is not int or self.omitted_fact_count < 0):
            raise ValueError("invalid human task analysis")


def human_task_fact_id(
    fact_type: str,
    owner_id: str,
    kind: str,
    anchor: SourceAnchor,
    *evidence: object,
) -> str:
    payload = (
        "h3-human-task-v1", fact_type, owner_id, kind,
        anchor.file_path, anchor.start_line, anchor.start_column,
        anchor.end_line, anchor.end_column, *evidence,
    )
    digest = sha256(json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    return f"{_ID_PREFIX}{digest}"


def has_valid_human_task_fact_id(
    fact: StaticTaskFact | ArtifactAccessFact | HumanTaskEntryFact,
) -> bool:
    if isinstance(fact, StaticTaskFact):
        expected = human_task_fact_id(
            "task", fact.owner_symbol_id, fact.kind.value, fact.source_anchor,
            fact.control_node_id, fact.callsite_ids, fact.target_symbol_ids,
        )
    elif isinstance(fact, ArtifactAccessFact):
        expected = human_task_fact_id(
            "artifact", fact.owner_symbol_id, fact.kind.value, fact.source_anchor,
            fact.artifact_key, fact.control_node_id, fact.callsite_id,
        )
    else:
        expected = human_task_fact_id(
            "entry", fact.root_symbol_id, fact.kind.value, fact.source_anchor,
            fact.evidence_fact_ids,
        )
    return fact.id == expected


def _anchor(parsed: ParsedFile, node: ast.AST) -> SourceAnchor:
    return SourceAnchor(
        parsed.relative_path, getattr(node, "lineno", 1),
        start_column=getattr(node, "col_offset", None),
        end_line=getattr(node, "end_lineno", getattr(node, "lineno", 1)),
        end_column=getattr(node, "end_col_offset", None),
    )


def _call_name(call: ast.Call) -> str:
    return (call.func.id if isinstance(call.func, ast.Name)
            else call.func.attr if isinstance(call.func, ast.Attribute)
            else ast.unparse(call.func))


class _TaskVisitor(ImplicitScopeVisitor):
    def __init__(self, parsed: ParsedFile, resolution: ResolutionResult,
                 flow: FlowAnalysis, model_callsite_ids: set[str],
                 model_definition_ids: set[str]) -> None:
        assert parsed.tree is not None
        self.parsed = parsed
        self.index = resolution.symbol_index
        self.graph = flow.graph
        self.model_callsite_ids = model_callsite_ids
        self.model_definition_ids = model_definition_ids
        self.locations = ScopeLocations(parsed.tree, self.index, parsed.relative_path)
        self.scope_id = next(entry.id for entry in self.index.entries.values()
                             if entry.file_path == parsed.relative_path
                             and entry.kind is NodeKind.MODULE)
        self.tasks: list[StaticTaskFact] = []
        self.artifacts: list[ArtifactAccessFact] = []
        self.references: dict[tuple[str, int, int | None, str], list[SymbolReference]] = {}
        for reference in self.index.references:
            if reference.source_anchor.file_path == parsed.relative_path:
                key = (reference.scope_symbol_id or "", reference.source_anchor.start_line,
                       reference.source_anchor.start_column, reference.name)
                self.references.setdefault(key, []).append(reference)
        self.callsites: dict[tuple[str, int, int | None, int | None, int | None, str], str] = {}
        for node in self.graph.nodes.values():
            if (node.kind is NodeKind.CALL_SITE and node.file_path == parsed.relative_path
                    and len(node.source_anchors) == 1):
                anchor = node.source_anchors[0]
                self.callsites[(
                    str(node.metadata.get("scope_symbol_id", "")), anchor.start_line,
                    anchor.start_column, anchor.end_line, anchor.end_column, node.name,
                )] = node.id
        self.parents = {
            child: parent for parent in ast.walk(parsed.tree)
            for child in ast.iter_child_nodes(parent)
        }

    def visit_Module(self, node: ast.Module) -> None:
        self._generation_tasks(node.body)
        self._other_tasks(node.body)
        self.generic_visit(node)

    def _visit_definition(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
    ) -> None:
        for expression in definition_expressions(node):
            self.visit(expression)
        owner = self.locations.owners.get(node)
        if owner is None:
            return
        previous = self.scope_id
        self.scope_id = owner
        try:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._generation_tasks(node.body)
                self._other_tasks(node.body)
                self._evaluation_task(node)
            for statement in node.body:
                self.visit(statement)
        finally:
            self.scope_id = previous

    visit_FunctionDef = _visit_definition
    visit_AsyncFunctionDef = _visit_definition
    visit_ClassDef = _visit_definition

    def _binding(self, expression: ast.expr) -> str | None:
        if not isinstance(expression, ast.Name):
            return None
        matches = self.references.get((
            self.scope_id, expression.lineno, expression.col_offset, expression.id,
        ), ())
        if len(matches) != 1:
            return None
        reference = matches[0]
        if reference.resolved_definition_id is not None:
            return reference.resolved_definition_id
        return reference.possible_target_ids[0] if len(reference.possible_target_ids) == 1 else None

    def _callsite(self, call: ast.Call) -> str | None:
        anchor = _anchor(self.parsed, call)
        return self.callsites.get((
            self.scope_id, anchor.start_line, anchor.start_column,
            anchor.end_line, anchor.end_column, _call_name(call),
        ))

    def _targets(self, callsite_ids: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(dict.fromkeys(
            edge.target for edge in self.graph.edges
            if edge.source in callsite_ids and edge.type is EdgeType.CALLS
        ))[:MAX_FACT_REFERENCES]

    def _control(self, statement: ast.stmt) -> str | None:
        anchor = _anchor(self.parsed, statement)
        matches = [
            node.id for node in self.graph.nodes.values()
            if node.kind in (NodeKind.FLOW_STEP, NodeKind.CONTROL_BLOCK)
            and node.file_path == self.parsed.relative_path
            and node.source_anchors == (anchor,)
        ]
        return matches[0] if len(matches) == 1 else None

    def _statement(self, node: ast.AST) -> ast.stmt | None:
        current = node
        while current in self.parents:
            current = self.parents[current]
            if isinstance(current, ast.stmt):
                return current
        return None

    def _scope_nodes(self, statements: list[ast.stmt]) -> list[ast.AST]:
        result: list[ast.AST] = []

        def visit(node: ast.AST) -> None:
            result.append(node)
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef,
                                      ast.Lambda)):
                    continue
                visit(child)

        for statement in statements:
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            visit(statement)
        return result

    @staticmethod
    def _contains_name(node: ast.AST, name: str) -> bool:
        return any(isinstance(child, ast.Name) and child.id == name
                   for child in ast.walk(node))

    def _is_model_call(self, call: ast.Call) -> bool:
        callsite = self._callsite(call)
        if callsite in self.model_callsite_ids:
            return True
        owner = self.index.entries.get(self.scope_id)
        return bool(
            isinstance(call.func, ast.Name) and call.func.id == "self"
            and owner is not None and owner.parent_symbol in self.model_definition_ids
        )

    def _append_task(
        self,
        kind: StaticTaskKind,
        source: ast.AST,
        control_statement: ast.stmt,
        calls: tuple[ast.Call, ...],
    ) -> None:
        callsite_ids = tuple(dict.fromkeys(
            callsite for call in calls if (callsite := self._callsite(call)) is not None
        ))[:MAX_FACT_REFERENCES]
        control = self._control(control_statement)
        if (calls and not callsite_ids) or control is None:
            return
        targets = self._targets(callsite_ids)
        anchor = _anchor(self.parsed, source)
        self.tasks.append(StaticTaskFact(
            human_task_fact_id(
                "task", self.scope_id, kind.value, anchor,
                control, callsite_ids, targets,
            ),
            self.scope_id, kind, anchor, control, callsite_ids, targets,
            Confidence.PROBABLE,
        ))

    def _generation_tasks(self, statements: list[ast.stmt]) -> None:
        nodes = self._scope_nodes(statements)
        calls = [node for node in nodes if isinstance(node, ast.Call)]
        aliases: dict[str, str | None] = {}
        dependencies: dict[str, set[str]] = {}
        for statement in (node for node in nodes if isinstance(node, ast.Assign)):
            if not (isinstance(statement, ast.Assign) and len(statement.targets) == 1
                    and isinstance(statement.targets[0], ast.Name)):
                continue
            target_name = statement.targets[0].id
            if (isinstance(statement.value, ast.Lambda)
                    and isinstance(statement.value.body, ast.Call)):
                body_call = statement.value.body
                if isinstance(body_call.func, ast.Attribute) \
                        and body_call.func.attr in ("encode", "decode"):
                    previous = aliases.get(target_name, body_call.func.attr)
                    aliases[target_name] = (body_call.func.attr
                                             if previous == body_call.func.attr else None)
            dependencies[target_name] = {
                item.id for item in ast.walk(statement.value) if isinstance(item, ast.Name)
            }

        def call_kind(call: ast.Call, kind: str) -> bool:
            return bool(
                isinstance(call.func, ast.Attribute) and call.func.attr == kind
                or isinstance(call.func, ast.Name) and aliases.get(call.func.id) == kind
            )

        def reaches_call(name: str, call: ast.Call) -> bool:
            reachable = {name}
            changed = True
            while changed:
                changed = False
                for target, sources in dependencies.items():
                    if target not in reachable and sources & reachable:
                        reachable.add(target)
                        changed = True
            return any(isinstance(item, ast.Name) and item.id in reachable
                       for argument in (*call.args, *(item.value for item in call.keywords))
                       for item in ast.walk(argument))

        generate_calls = [call for call in calls
                          if isinstance(call.func, ast.Attribute)
                          and call.func.attr in ("generate", "sample")]
        generation_target: ast.expr
        for candidate_statement in statements:
            if isinstance(candidate_statement, ast.AnnAssign):
                generation_target = candidate_statement.target
            elif (isinstance(candidate_statement, ast.Assign)
                  and len(candidate_statement.targets) == 1):
                generation_target = candidate_statement.targets[0]
            else:
                continue
            if not isinstance(generation_target, ast.Name):
                continue
            value = candidate_statement.value
            if (value is not None and isinstance(value, ast.Call) and call_kind(value, "encode")
                    and any(call.lineno > value.lineno
                            and reaches_call(generation_target.id, call)
                            for call in generate_calls)):
                generate = next(call for call in generate_calls
                                if call.lineno > value.lineno
                                and reaches_call(generation_target.id, call))
                self._append_task(
                    StaticTaskKind.INPUT_ENCODING, value, candidate_statement, (value, generate),
                )
        for loop in (node for node in nodes if isinstance(node, (ast.For, ast.AsyncFor, ast.While))):
            loop_calls = [child for child in ast.walk(loop) if isinstance(child, ast.Call)]
            for loop_statement in loop.body:
                if not (isinstance(loop_statement, ast.Assign)
                        and len(loop_statement.targets) == 1
                        and isinstance(loop_statement.targets[0], ast.Name)
                        and isinstance(loop_statement.value, ast.Call)
                        and _call_name(loop_statement.value) in ("multinomial", "choice")):
                    continue
                sampled = loop_statement.targets[0].id
                uses = [call for call in loop_calls
                        if call.lineno >= loop_statement.lineno
                        and call is not loop_statement.value
                        and _call_name(call) in ("cat", "append")
                        and self._contains_name(call, sampled)]
                if uses:
                    self._append_task(
                        StaticTaskKind.TOKEN_SAMPLING, loop, loop,
                        (loop_statement.value, uses[0]),
                    )
        for outer in calls:
            if not (isinstance(outer.func, ast.Name) and outer.func.id == "print"):
                continue
            outer_callsite = self._callsite(outer)
            if outer_callsite is None or not any(
                edge.source == outer_callsite and edge.type is EdgeType.CALLS
                and self.graph.nodes.get(edge.target) is not None
                and self.graph.nodes[edge.target].qualified_name == "builtins.print"
                for edge in self.graph.edges
            ):
                continue
            decode = next((child for child in ast.walk(outer)
                           if isinstance(child, ast.Call) and call_kind(child, "decode")), None)
            control = self._statement(outer)
            if decode is not None and control is not None:
                self._append_task(
                    StaticTaskKind.OUTPUT_DECODING, decode, control, (decode, outer),
                )

    @staticmethod
    def _assigned_names(statement: ast.stmt) -> tuple[str, ...]:
        target: ast.expr | None = None
        if isinstance(statement, ast.Assign) and len(statement.targets) == 1:
            target = statement.targets[0]
        elif isinstance(statement, ast.AnnAssign):
            target = statement.target
        if isinstance(target, ast.Name):
            return (target.id,)
        if isinstance(target, (ast.Tuple, ast.List)):
            return tuple(item.id for item in target.elts if isinstance(item, ast.Name))
        return ()

    @staticmethod
    def _negative_one_slice(expression: ast.Subscript) -> bool:
        slices = expression.slice.elts if isinstance(expression.slice, ast.Tuple) else (
            expression.slice,
        )
        return any(
            isinstance(item, ast.UnaryOp) and isinstance(item.op, ast.USub)
            and isinstance(item.operand, ast.Constant) and item.operand.value == 1
            for item in slices
        )

    def _other_tasks(self, statements: list[ast.stmt]) -> None:
        nodes = self._scope_nodes(statements)
        model_outputs: dict[str, ast.Call] = {}
        for statement in (node for node in nodes if isinstance(node, ast.stmt)):
            value = (statement.value if isinstance(statement, (ast.Assign, ast.AnnAssign))
                     else None)
            calls = [node for node in ast.walk(value) if isinstance(node, ast.Call)] if value else []
            model_call = next((call for call in calls
                               if self._is_model_call(call)), None)
            if model_call is not None:
                for name in self._assigned_names(statement):
                    model_outputs[name] = model_call
            if isinstance(value, ast.BinOp) and isinstance(value.op, ast.Add):
                invocation = next((call for call in calls
                                   if self._is_model_call(call)), None)
                if invocation is not None:
                    names = {item.id for item in ast.walk(invocation)
                             if isinstance(item, ast.Name)}
                    other = value.right if invocation in tuple(ast.walk(value.left)) else value.left
                    if any(isinstance(item, ast.Name) and item.id in names
                           for item in ast.walk(other)):
                        self._append_task(
                            StaticTaskKind.RESIDUAL_MERGE, value, statement, (invocation,),
                        )
            subscript = next((item for item in ast.walk(value)
                              if isinstance(item, ast.Subscript)
                              and isinstance(item.value, ast.Name)
                              and item.value.id in model_outputs
                              and self._negative_one_slice(item)), None) if value else None
            if subscript is not None:
                if not isinstance(subscript.value, ast.Name):
                    continue
                self._append_task(
                    StaticTaskKind.LAST_TOKEN_SELECTION, subscript, statement,
                    (model_outputs[subscript.value.id],),
                )
        for loop in (node for node in nodes if isinstance(node, (ast.For, ast.AsyncFor))):
            if not (isinstance(loop.target, ast.Name)
                    and isinstance(loop.iter, ast.Attribute)
                    and loop.iter.attr == "param_groups"):
                continue
            update = next((statement for statement in loop.body
                           if isinstance(statement, ast.Assign)
                           and len(statement.targets) == 1
                           and isinstance(statement.targets[0], ast.Subscript)
                           and isinstance(statement.targets[0].value, ast.Name)
                           and statement.targets[0].value.id == loop.target.id
                           and isinstance(statement.targets[0].slice, ast.Constant)
                           and statement.targets[0].slice.value == "lr"), None)
            if update is not None:
                update_calls = tuple(item for item in ast.walk(update.value)
                                     if isinstance(item, ast.Call))
                self._append_task(
                    StaticTaskKind.LEARNING_RATE_UPDATE, update, loop, update_calls,
                )
        for call in (node for node in nodes if isinstance(node, ast.Call)):
            classification = self._artifact_kind(call)
            key = self._artifact_key(call)
            artifact_statement = self._statement(call)
            callsite = self._callsite(call)
            control = self._control(artifact_statement) if artifact_statement is not None else None
            if (classification is None or key is None or artifact_statement is None
                    or control is None or callsite is None):
                continue
            kind, identity_confidence = classification
            anchor = _anchor(self.parsed, call)
            key_confidence = (Confidence.PROBABLE if key.startswith("dynamic:")
                              else Confidence.EXACT)
            self.artifacts.append(ArtifactAccessFact(
                human_task_fact_id(
                    "artifact", self.scope_id, kind.value, anchor,
                    key, control, callsite,
                ),
                self.scope_id, kind, key, anchor, control, callsite,
                min((identity_confidence, key_confidence),
                    key=lambda value: 0 if value is Confidence.UNRESOLVED
                    else 1 if value is Confidence.PROBABLE else 2),
            ))

    def _evaluation_task(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        no_grad = any(
            (isinstance(decorator, ast.Call)
             and _call_name(decorator) == "no_grad")
            or (isinstance(decorator, ast.Attribute) and decorator.attr == "no_grad")
            for decorator in node.decorator_list
        )
        if not no_grad:
            return
        calls = [item for statement in node.body for item in ast.walk(statement)
                 if isinstance(item, ast.Call)]
        eval_call = next((call for call in calls if isinstance(call.func, ast.Attribute)
                          and call.func.attr == "eval"), None)
        train_call = next((call for call in calls if isinstance(call.func, ast.Attribute)
                           and call.func.attr == "train"), None)
        if (eval_call is None or train_call is None
                or not isinstance(eval_call.func, ast.Attribute)
                or not isinstance(train_call.func, ast.Attribute)
                or ast.dump(eval_call.func.value) != ast.dump(train_call.func.value)):
            return
        receiver = ast.dump(eval_call.func.value)
        model_call = next((call for call in calls
                           if ast.dump(call.func) == receiver), None)
        if model_call is None:
            return
        model_statement = self._statement(model_call)
        outputs = self._assigned_names(model_statement) if model_statement is not None else ()
        loss_call = next((call for call in calls if call is not model_call
                          and any(self._contains_name(call, name) for name in outputs)), None)
        control = self._statement(eval_call)
        if loss_call is not None and control is not None:
            self._append_task(
                StaticTaskKind.EVALUATION_HELPER, node, control,
                (eval_call, model_call, loss_call, train_call),
            )

    def _resolved_call(self, call: ast.Call) -> tuple[str, Confidence] | None:
        callsite = self._callsite(call)
        if callsite is None:
            return None
        edges = [edge for edge in self.graph.edges
                 if edge.source == callsite and edge.type is EdgeType.CALLS]
        if len(edges) != 1:
            return None
        target = self.graph.nodes.get(edges[0].target)
        if target is None:
            return None
        return target.qualified_name, edges[0].confidence

    def _tofile_producer_confidence(self, call: ast.Call) -> Confidence | None:
        if (not isinstance(call.func, ast.Attribute) or call.func.attr != "tofile"
                or not isinstance(call.func.value, ast.Name)):
            return None
        receiver = call.func.value.id
        current: ast.AST = call
        while current in self.parents and not isinstance(
            current, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef),
        ):
            current = self.parents[current]
        if not isinstance(current, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef)):
            return None
        call_statement = self._statement(call)
        if call_statement is None or call_statement not in current.body:
            return None
        assignments = [
            node for node in self._scope_nodes(current.body)
            if isinstance(node, (ast.Assign, ast.AnnAssign))
            and receiver in self._assigned_names(node)
        ]
        call_index = current.body.index(call_statement)
        direct_assignments = [
            statement for statement in current.body[:call_index]
            if isinstance(statement, (ast.Assign, ast.AnnAssign))
            and receiver in self._assigned_names(statement)
        ]
        if not direct_assignments:
            return None
        assignment = direct_assignments[-1]
        assignment_end = getattr(assignment, "end_lineno", assignment.lineno)
        if any(
            other is not assignment
            and getattr(other, "lineno", 0) > assignment_end
            and getattr(other, "lineno", 0) < call_statement.lineno
            for other in assignments
        ):
            return None
        producer = assignment.value
        if not isinstance(producer, ast.Call):
            return None
        resolved = self._resolved_call(producer)
        if resolved is None or resolved[0] != "numpy.array":
            return None
        return min((resolved[1], Confidence.PROBABLE),
                   key=lambda value: 0 if value is Confidence.UNRESOLVED
                   else 1 if value is Confidence.PROBABLE else 2)

    def _artifact_kind(
        self,
        call: ast.Call,
    ) -> tuple[ArtifactAccessKind, Confidence] | None:
        resolved = self._resolved_call(call)
        if resolved is not None and resolved[0] in (
            "pathlib.Path.read_text", "pathlib.Path.read_bytes",
        ):
            return ArtifactAccessKind.READ, resolved[1]
        if resolved is not None and resolved[0] in (
            "pathlib.Path.write_text", "pathlib.Path.write_bytes",
        ):
            return ArtifactAccessKind.WRITE, resolved[1]
        if isinstance(call.func, ast.Attribute) and call.func.attr == "tofile":
            confidence = self._tofile_producer_confidence(call)
            return ((ArtifactAccessKind.WRITE, confidence)
                    if confidence is not None else None)
        if resolved is None or resolved[0] not in ("builtins.open", "numpy.memmap"):
            return None
        mode = next((item.value for item in call.keywords if item.arg == "mode"), None)
        if isinstance(mode, ast.Constant) and isinstance(mode.value, str):
            kind = (ArtifactAccessKind.WRITE if any(char in mode.value for char in "wa+")
                    else ArtifactAccessKind.READ)
            return kind, resolved[1]
        if resolved[0] == "builtins.open":
            return ArtifactAccessKind.READ, resolved[1]
        return ArtifactAccessKind.READ, resolved[1]

    def _artifact_key(self, call: ast.Call) -> str | None:
        if not call.args:
            return None
        path = call.args[0]
        if isinstance(path, ast.Constant) and isinstance(path.value, str):
            normalized = path.value.replace("\\", "/").strip()
            return normalized if normalized and len(normalized.encode("utf-8")) <= 160 else None
        literals = [item.value for item in ast.walk(path)
                    if isinstance(item, ast.Constant) and isinstance(item.value, str)]
        if not literals:
            return None
        suffix = literals[-1].replace("\\", "/").strip("/")
        key = f"dynamic:{self.scope_id}:{suffix}"
        return key if suffix and len(key.encode("utf-8")) <= MAX_ARTIFACT_KEY_BYTES else None

    def visit_For(self, node: ast.For) -> None:
        self._gradient_accumulation(node)
        self.generic_visit(node)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self._gradient_accumulation(node)
        self.generic_visit(node)

    def _gradient_accumulation(self, node: ast.For | ast.AsyncFor) -> None:
        if not (isinstance(node.iter, ast.Call) and isinstance(node.iter.func, ast.Name)
                and node.iter.func.id == "range" and len(node.iter.args) == 1
                and not node.iter.keywords):
            return
        loop_binding = self._binding(node.iter.args[0])
        if loop_binding is None:
            return
        scaled_names: set[str] = set()
        for child in self._scope_nodes(node.body):
            if (isinstance(child, ast.Assign) and len(child.targets) == 1
                    and isinstance(child.targets[0], ast.Name)
                    and isinstance(child.value, ast.BinOp)
                    and isinstance(child.value.op, ast.Div)
                    and self._binding(child.value.right) == loop_binding):
                scaled_names.add(child.targets[0].id)
        backward_calls = [
            child for child in self._scope_nodes(node.body)
            if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute)
            and child.func.attr == "backward"
            and any(self._contains_name(child.func.value, name) for name in scaled_names)
        ]
        callsite_ids = tuple(callsite for call in backward_calls
                             if (callsite := self._callsite(call)) is not None)
        control = self._control(node)
        if not callsite_ids or control is None:
            return
        callsite_ids = tuple(dict.fromkeys(callsite_ids))[:MAX_FACT_REFERENCES]
        targets = self._targets(callsite_ids)
        anchor = _anchor(self.parsed, node)
        self.tasks.append(StaticTaskFact(
            human_task_fact_id(
                "task", self.scope_id, StaticTaskKind.GRADIENT_ACCUMULATION.value,
                anchor, control, callsite_ids, targets,
            ),
            self.scope_id, StaticTaskKind.GRADIENT_ACCUMULATION, anchor,
            control, callsite_ids, targets, Confidence.PROBABLE,
        ))


def build_human_task_analysis(
    files: tuple[ParsedFile, ...],
    resolution: ResolutionResult,
    flow: FlowAnalysis,
    model: ModelAnalysis,
    training: TrainingAnalysis,
) -> HumanTaskAnalysis:
    """Build bounded task facts without retaining ASTs or source text."""
    del training
    tasks: list[StaticTaskFact] = []
    artifacts: list[ArtifactAccessFact] = []
    model_callsite_ids = {item.call_site_id for item in model.invocations}
    model_definition_ids = {item.symbol_id for item in model.definitions}
    indexed_module_files = {
        entry.file_path for entry in resolution.symbol_index.entries.values()
        if entry.kind is NodeKind.MODULE
    }
    for parsed in files:
        if (parsed.tree is None
                or parsed.relative_path not in indexed_module_files):
            continue
        visitor = _TaskVisitor(
            parsed, resolution, flow, model_callsite_ids, model_definition_ids,
        )
        visitor.visit(parsed.tree)
        tasks.extend(visitor.tasks)
        artifacts.extend(visitor.artifacts)
    tasks.sort(key=lambda fact: (
        fact.source_anchor.file_path, fact.source_anchor.start_line,
        fact.source_anchor.start_column if fact.source_anchor.start_column is not None else -1,
        fact.id,
    ))
    artifacts.sort(key=lambda fact: (
        fact.source_anchor.file_path, fact.source_anchor.start_line,
        fact.source_anchor.start_column if fact.source_anchor.start_column is not None else -1,
        fact.id,
    ))
    entries: list[HumanTaskEntryFact] = []
    by_owner: dict[str, list[StaticTaskFact]] = {}
    for task in tasks:
        by_owner.setdefault(task.owner_symbol_id, []).append(task)
    required_generation = {
        StaticTaskKind.INPUT_ENCODING, StaticTaskKind.OUTPUT_DECODING,
    }
    sampling_tasks = [task for task in tasks
                      if task.kind is StaticTaskKind.TOKEN_SAMPLING]
    for owner_id, owner_tasks in by_owner.items():
        if not required_generation <= {task.kind for task in owner_tasks}:
            continue
        generation_targets = {
            target for task in owner_tasks
            if task.kind is StaticTaskKind.INPUT_ENCODING
            for target in task.target_symbol_ids
        }
        linked_sampling = [task for task in sampling_tasks
                           if task.owner_symbol_id == owner_id
                           or task.owner_symbol_id in generation_targets]
        if not linked_sampling:
            continue
        owner = resolution.symbol_index.entries.get(owner_id)
        if owner is None:
            continue
        evidence = tuple([
            *(task.id for task in owner_tasks if task.kind in required_generation),
            linked_sampling[0].id,
        ])[:MAX_FACT_REFERENCES]
        entries.append(HumanTaskEntryFact(
            human_task_fact_id(
                "entry", owner_id, HumanEntryKind.GENERATION.value,
                owner.source_anchor, evidence,
            ),
            owner_id, HumanEntryKind.GENERATION, owner.source_anchor,
            evidence, Confidence.PROBABLE,
        ))
    artifacts_by_owner: dict[str, list[ArtifactAccessFact]] = {}
    for artifact in artifacts:
        artifacts_by_owner.setdefault(artifact.owner_symbol_id, []).append(artifact)
    for owner_id, owner_artifacts in artifacts_by_owner.items():
        owner_writes = [item for item in owner_artifacts
                        if item.kind is ArtifactAccessKind.WRITE]
        if not owner_writes:
            continue
        owner = resolution.symbol_index.entries.get(owner_id)
        if owner is None:
            continue
        evidence = tuple(item.id for item in owner_writes)[:MAX_FACT_REFERENCES]
        entries.append(HumanTaskEntryFact(
            human_task_fact_id(
                "entry", owner_id, HumanEntryKind.DATA_PREPARATION.value,
                owner.source_anchor, evidence,
            ),
            owner_id, HumanEntryKind.DATA_PREPARATION, owner.source_anchor,
            evidence, min((item.confidence for item in owner_writes),
                          key=lambda value: 0 if value is Confidence.PROBABLE else 1),
        ))
    candidates: list[StaticTaskFact | ArtifactAccessFact | HumanTaskEntryFact] = [
        *tasks, *artifacts, *entries,
    ]
    omitted = max(0, len(candidates) - MAX_HUMAN_TASK_FACTS)
    kept = candidates[:MAX_HUMAN_TASK_FACTS]
    kept_ids = {fact.id for fact in kept}
    kept_entries = tuple(fact for fact in kept if isinstance(fact, HumanTaskEntryFact)
                         and all(item in kept_ids for item in fact.evidence_fact_ids))
    omitted += len([fact for fact in kept if isinstance(fact, HumanTaskEntryFact)]) - len(
        kept_entries,
    )
    return HumanTaskAnalysis(
        entries=kept_entries,
        tasks=tuple(fact for fact in kept if isinstance(fact, StaticTaskFact)),
        artifacts=tuple(fact for fact in kept if isinstance(fact, ArtifactAccessFact)),
        omitted_fact_count=omitted,
    )
