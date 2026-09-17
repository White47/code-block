"""Source-backed PyTorch model structure, independent of Python parsing."""

import ast
from collections.abc import Iterator
from dataclasses import dataclass

from codeviz.core import Confidence, EntityRole, NodeKind, SourceAnchor, stable_node_id
from codeviz.semantic import ModelRule, PyTorchModelRule

from .flow import FlowAnalysis
from .imports import ModuleCatalog
from .python import AnalysisIssue, ParsedFile
from .resolution import ResolutionResult
from .symbols import SymbolIndexEntry


@dataclass(frozen=True, slots=True)
class ModelDefinition:
    symbol_id: str
    qualified_name: str
    source_anchor: SourceAnchor
    base_names: tuple[str, ...]
    forward_symbol_id: str | None
    confidence: Confidence
    evidence: tuple[str, ...]
    role: EntityRole = EntityRole.DEFINITION


@dataclass(frozen=True, slots=True)
class ModuleInstance:
    id: str
    owner_definition_id: str
    name: str
    qualified_name: str
    source_anchor: SourceAnchor
    layer_type: str
    target_definition_id: str | None
    container_kind: str | None
    member_instance_ids: tuple[str | None, ...]
    repeat_count: int | None
    shared_with_id: str | None
    confidence: Confidence
    evidence: tuple[str, ...]
    role: EntityRole = EntityRole.INSTANCE


@dataclass(frozen=True, slots=True)
class ModuleInvocation:
    id: str
    instance_id: str
    call_site_id: str
    source_anchor: SourceAnchor
    confidence: Confidence
    evidence: tuple[str, ...]
    possible_instance_ids: tuple[str, ...] = ()
    role: EntityRole = EntityRole.INVOCATION


@dataclass(frozen=True, slots=True)
class ModelAnalysis:
    definitions: tuple[ModelDefinition, ...] = ()
    instances: tuple[ModuleInstance, ...] = ()
    invocations: tuple[ModuleInvocation, ...] = ()
    issues: tuple[AnalysisIssue, ...] = ()


def _anchor(parsed: ParsedFile, node: ast.AST) -> SourceAnchor:
    return SourceAnchor(
        parsed.relative_path, getattr(node, "lineno", 1),
        start_column=getattr(node, "col_offset", None),
        end_line=getattr(node, "end_lineno", getattr(node, "lineno", 1)),
        end_column=getattr(node, "end_col_offset", None),
    )


def _reference_target(expression: ast.expr, parsed: ParsedFile,
                      references: dict[tuple[str, int, int | None], str]) -> str | None:
    if not isinstance(expression, (ast.Name, ast.Attribute)):
        return None
    return references.get((parsed.relative_path, expression.lineno, expression.col_offset))


def _static_range_count(expression: ast.expr) -> int | None:
    if not (isinstance(expression, ast.Call) and isinstance(expression.func, ast.Name)
            and expression.func.id == "range" and len(expression.args) == 1
            and not expression.keywords):
        return None
    arg = expression.args[0]
    if isinstance(arg, ast.Constant) and type(arg.value) is int and 0 <= arg.value <= 10000:
        return arg.value
    return None


def _local_bindings(function: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    names = {arg.arg for arg in (*function.args.posonlyargs, *function.args.args,
             *function.args.kwonlyargs)}
    if function.args.vararg:
        names.add(function.args.vararg.arg)
    if function.args.kwarg:
        names.add(function.args.kwarg.arg)
    for node in ast.walk(function):
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            names.add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Import):
            names.update(alias.asname or alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.update(alias.asname or alias.name for alias in node.names)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
    return names


def _assignments(statements: list[ast.stmt], guarded: bool = False) -> Iterator[tuple[ast.stmt, bool]]:
    for statement in statements:
        if isinstance(statement, (ast.Assign, ast.AnnAssign)):
            yield statement, guarded
        elif isinstance(statement, (ast.If, ast.For, ast.AsyncFor, ast.While,
                                    ast.Try, ast.TryStar, ast.With, ast.AsyncWith)):
            yield from _assignments(statement.body, True)
            if hasattr(statement, "orelse"):
                yield from _assignments(statement.orelse, True)
            if isinstance(statement, (ast.Try, ast.TryStar)):
                for handler in statement.handlers:
                    yield from _assignments(handler.body, True)
                yield from _assignments(statement.finalbody, True)
        elif isinstance(statement, ast.Match):
            for case in statement.cases:
                yield from _assignments(case.body, True)


class _ModelBuilder:
    def __init__(self, files: tuple[ParsedFile, ...], resolution: ResolutionResult,
                 flow: FlowAnalysis, rule: ModelRule) -> None:
        self.files = files
        self.index = resolution.symbol_index
        self.flow = flow
        self.rule = rule
        self.references = {
            (reference.source_anchor.file_path, reference.source_anchor.start_line,
             reference.source_anchor.start_column): reference.resolved_definition_id
            for reference in self.index.references if reference.resolved_definition_id is not None
        }
        self.catalog = ModuleCatalog(files, self.index)
        self.definitions: list[ModelDefinition] = []
        self.instances: list[ModuleInstance] = []
        self.invocations: list[ModuleInvocation] = []
        self.issues: list[AnalysisIssue] = []
        self.classes: dict[str, tuple[ParsedFile, ast.ClassDef, SymbolIndexEntry]] = {}
        self.bindings: dict[str, dict[str, str]] = {}
        self.model_ids: set[str] = set()
        self.instance_by_owner: dict[str, dict[str, ModuleInstance]] = {}
        self.occurrences: dict[tuple[str, str], int] = {}
        self.callsites = {
            (node.file_path, node.source_anchors[0].start_line,
             node.source_anchors[0].start_column, node.name,
             node.metadata.get("scope_symbol_id")): node.id
            for node in flow.graph.nodes.values() if node.kind == NodeKind.CALL_SITE
        }

    def _model_error(self, parsed: ParsedFile, error: Exception) -> None:
        self.issues.append(AnalysisIssue(
            "MODEL_ERROR", f"{type(error).__name__}: {error}",
            parsed.relative_path, SourceAnchor(parsed.relative_path, 1),
        ))

    def _collect_classes(self) -> None:
        entries = {
            (entry.file_path, entry.source_anchor.start_line, entry.name): entry
            for entry in self.index.entries.values() if entry.kind == NodeKind.CLASS
        }
        for parsed in self.files:
            if parsed.tree is None:
                continue
            try:
                bindings = self.rule.import_bindings(parsed.tree)
            except Exception as error:
                self._model_error(parsed, error)
                continue
            if self.catalog.is_project_path("torch"):
                bindings = {name: path for name, path in bindings.items()
                            if not path.startswith("torch")}
            self.bindings[parsed.relative_path] = bindings
            for node in ast.walk(parsed.tree):
                if isinstance(node, ast.ClassDef):
                    entry = entries.get((parsed.relative_path, node.lineno, node.name))
                    if entry is not None:
                        self.classes[entry.id] = (parsed, node, entry)

    def _base_id(self, parsed: ParsedFile, expression: ast.expr) -> str | None:
        target = _reference_target(expression, parsed, self.references)
        return target if target in self.classes else None

    def _collect_definitions(self) -> None:
        self._collect_classes()
        direct: dict[str, str] = {}
        for class_id, (parsed, node, _) in self.classes.items():
            bindings = self.bindings[parsed.relative_path]
            for base in node.bases:
                try:
                    recognized = self.rule.is_module_base(base, bindings)
                except Exception as error:
                    self._model_error(parsed, error)
                    break
                if recognized:
                    direct[class_id] = ast.unparse(base)
                    self.model_ids.add(class_id)
                    break
        changed = True
        while changed:
            changed = False
            for class_id, (parsed, node, _) in self.classes.items():
                if class_id not in self.model_ids and any(
                    self._base_id(parsed, base) in self.model_ids for base in node.bases
                ):
                    self.model_ids.add(class_id)
                    changed = True
        for class_id, (parsed, node, entry) in self.classes.items():
            if class_id not in self.model_ids:
                continue
            forward = next((item for item in self.index.entries.values()
                            if item.parent_symbol == class_id and item.name == "forward"
                            and item.kind == NodeKind.METHOD), None)
            evidence = (f"inherits {direct[class_id]} (torch.nn.Module)",) if class_id in direct else (
                "inherits a project-local recognized torch.nn.Module subclass",)
            self.definitions.append(ModelDefinition(
                class_id, entry.qualified_name, _anchor(parsed, node),
                tuple(ast.unparse(base) for base in node.bases),
                forward.id if forward else None, Confidence.EXACT, evidence,
            ))

    def _instance(self, parsed: ParsedFile, owner: SymbolIndexEntry, name: str,
                  expression: ast.expr, bindings: dict[str, str],
                  by_name: dict[str, ModuleInstance], *, repetition: int = 1,
                  shared_source: ModuleInstance | None = None, guarded: bool = False,
                  candidate: bool = False, range_available: bool = True) -> ModuleInstance | None:
        shared = shared_source
        if (shared is None and isinstance(expression, ast.Attribute)
                and isinstance(expression.value, ast.Name)
                and expression.value.id == "self"):
            shared = by_name.get(expression.attr)
        match = (self.rule.match_constructor(expression.func, bindings)
                 if shared is None and isinstance(expression, ast.Call) else None)
        target_id = (_reference_target(expression.func, parsed, self.references)
                     if shared is None and isinstance(expression, ast.Call) else None)
        custom = self.classes.get(target_id) if target_id in self.model_ids else None
        unknown = match is None and custom is None and shared is None
        if unknown and not (candidate and isinstance(expression, ast.Call)):
            return None
        if match is not None:
            layer_type = match.canonical_name
        elif custom is not None:
            layer_type = custom[2].qualified_name
        elif shared is not None:
            layer_type = shared.layer_type
        else:
            assert isinstance(expression, ast.Call)
            layer_type = ast.unparse(expression.func)
        container_kind = match.container_kind if match else None
        count_key = (owner.id, name)
        occurrence = self.occurrences.get(count_key, 0)
        self.occurrences[count_key] = occurrence + 1
        qualified_name = f"{owner.qualified_name}.{name}"
        instance_id = stable_node_id(
            parsed.relative_path, qualified_name, NodeKind.ATTRIBUTE,
            owner.qualified_name, EntityRole.INSTANCE,
            occurrence_key=f"owner:{owner.id}:binding:{name}:occurrence:{occurrence}",
        )
        child_ids: list[str | None] = []
        children: list[ModuleInstance] = []
        repeat_count: int | None = repetition
        comprehension = False
        list_members = False
        partial_members = False
        if container_kind is not None and isinstance(expression, ast.Call):
            members: list[tuple[str, ast.expr]] = []
            if container_kind == "Sequential":
                members = [(f"{name}[{i}]", arg) for i, arg in enumerate(expression.args)]
            elif container_kind == "ModuleList" and expression.args:
                source = expression.args[0]
                if isinstance(source, (ast.List, ast.Tuple)):
                    members = [(f"{name}[{i}]", arg) for i, arg in enumerate(source.elts)]
                    list_members = True
                elif (isinstance(source, ast.ListComp) and len(source.generators) == 1
                      and not source.generators[0].ifs and not source.generators[0].is_async):
                    count = (_static_range_count(source.generators[0].iter)
                             if range_available else None)
                    if count is not None:
                        members = [(f"{name}[*]", source.elt)] if count > 0 else []
                        repeat_count = count
                        comprehension = True
                    else:
                        repeat_count = None
                else:
                    repeat_count = None
            elif container_kind == "ModuleList":
                repeat_count = None
            elif container_kind == "ModuleDict" and expression.args:
                source = expression.args[0]
                if isinstance(source, ast.Dict):
                    members = [(f"{name}[{key.value}]", value)
                               for key, value in zip(source.keys, source.values, strict=True)
                               if isinstance(key, ast.Constant) and isinstance(key.value, str)]
                    partial_members = len(members) != len(source.keys)
                else:
                    partial_members = True
            child_start = len(self.instances)
            issue_start = len(self.issues)
            previous_bindings = {child_name: by_name.get(child_name)
                                 for child_name, _ in members}
            try:
                for child_name, child_expression in members:
                    is_shared_template = (child_name.endswith("[*]")
                                          and isinstance(child_expression, ast.Attribute)
                                          and isinstance(child_expression.value, ast.Name)
                                          and child_expression.value.id == "self")
                    child = self._instance(
                        parsed, owner, child_name, child_expression, bindings, by_name,
                        repetition=(repeat_count or 1) if child_name.endswith("[*]")
                        and not is_shared_template else 1,
                        guarded=guarded, range_available=range_available,
                    )
                    child_ids.append(child.id if child is not None else None)
                    if child is not None:
                        children.append(child)
                    else:
                        self.issues.append(AnalysisIssue(
                            "MODEL_CONTAINER_MEMBER_UNRESOLVED",
                            "container member is not a recognized module constructor",
                            parsed.relative_path, _anchor(parsed, child_expression),
                        ))
            except Exception:
                del self.instances[child_start:]
                del self.issues[issue_start:]
                for child_name, previous in previous_bindings.items():
                    if previous is None:
                        by_name.pop(child_name, None)
                    else:
                        by_name[child_name] = previous
                raise
            if (not members and expression.args and repeat_count != 0) or partial_members:
                self.issues.append(AnalysisIssue(
                    "MODEL_CONTAINER_DYNAMIC", "container members or count are not statically known",
                    parsed.relative_path, _anchor(parsed, expression),
                ))
            if list_members:
                repeat_count = (len(children) if len(children) == len(members)
                                and len({item.layer_type for item in children}) == 1
                                and len({item.shared_with_id or item.id for item in children}) == len(children)
                                else 1)
            if (comprehension and repeat_count != 0
                    and (len(children) != 1 or children[0].shared_with_id is not None)):
                repeat_count = 1 if children and children[0].shared_with_id is not None else None
        evidence: tuple[str, ...]
        if shared is not None:
            evidence = (f"same constructor object as {shared.qualified_name}",)
        elif unknown:
            assert isinstance(expression, ast.Call)
            evidence = (f"unresolved constructor {ast.unparse(expression.func)} used in forward",)
        else:
            assert isinstance(expression, ast.Call)
            evidence = (f"constructor {ast.unparse(expression.func)} resolved as {layer_type}",)
        if repetition > 1:
            evidence += (f"static comprehension template repeated {repetition} times",)
        if guarded:
            evidence += ("construction is inside conditional or repeated control flow",)
        instance = ModuleInstance(
            instance_id, owner.id, name, qualified_name, _anchor(parsed, expression),
            layer_type, shared.target_definition_id if shared else target_id if custom else None,
            container_kind, tuple(child_ids), repeat_count,
            (shared.shared_with_id or shared.id) if shared else None,
            (Confidence.UNRESOLVED if unknown else Confidence.PROBABLE
             if repetition > 1 or comprehension or guarded or partial_members else Confidence.EXACT),
            evidence,
        )
        self.instances.append(instance)
        by_name[name] = instance
        return instance

    def _collect_instances(self) -> None:
        for definition in self.definitions:
            parsed, node, owner = self.classes[definition.symbol_id]
            by_name: dict[str, ModuleInstance] = {}
            self.instance_by_owner[owner.id] = by_name
            bindings = self.bindings[parsed.relative_path]
            init = next((item for item in node.body
                         if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                         and item.name == "__init__"), None)
            if init is None:
                continue
            local_names = _local_bindings(init)
            bindings = {name: path for name, path in bindings.items() if name not in local_names}
            range_available = "range" not in local_names and not any(
                entry.name == "range" and entry.file_path == parsed.relative_path
                and entry.scope == parsed.module_name for entry in self.index.entries.values()
            )
            forward = next((item for item in node.body
                            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                            and item.name == "forward"), None)
            candidate_names = {
                call.func.attr for call in ast.walk(forward) if isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and isinstance(call.func.value, ast.Name) and call.func.value.id == "self"
            } if forward is not None else set()
            for statement, guarded in _assignments(init.body):
                if isinstance(statement, ast.Assign):
                    first: ModuleInstance | None = None
                    for target in statement.targets:
                        if (isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name)
                                and target.value.id == "self"):
                            try:
                                created = self._instance(
                                    parsed, owner, target.attr, statement.value, bindings,
                                    by_name, shared_source=first, guarded=guarded,
                                    candidate=target.attr in candidate_names,
                                    range_available=range_available,
                                )
                                first = created or first
                                if guarded or created is None:
                                    by_name.pop(target.attr, None)
                            except Exception as error:
                                self._model_error(parsed, error)
                                by_name.pop(target.attr, None)
                elif (isinstance(statement, ast.AnnAssign) and statement.value is not None
                      and isinstance(statement.target, ast.Attribute)
                      and isinstance(statement.target.value, ast.Name)
                      and statement.target.value.id == "self"):
                    try:
                        created = self._instance(
                            parsed, owner, statement.target.attr, statement.value,
                            bindings, by_name, guarded=guarded,
                            candidate=statement.target.attr in candidate_names,
                            range_available=range_available,
                        )
                        if guarded or created is None:
                            by_name.pop(statement.target.attr, None)
                    except Exception as error:
                        self._model_error(parsed, error)
                        by_name.pop(statement.target.attr, None)

    def _collect_invocations(self) -> None:
        for definition in self.definitions:
            parsed, node, owner = self.classes[definition.symbol_id]
            forward = next((item for item in node.body
                            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                            and item.name == "forward"), None)
            if forward is None or definition.forward_symbol_id is None:
                continue
            by_name = self.instance_by_owner[owner.id]
            loop_members: dict[int, ModuleInstance] = {}
            for loop in ast.walk(forward):
                if not (isinstance(loop, (ast.For, ast.AsyncFor))
                        and isinstance(loop.target, ast.Name)
                        and isinstance(loop.iter, ast.Attribute)
                        and isinstance(loop.iter.value, ast.Name)
                        and loop.iter.value.id == "self"):
                    continue
                container = by_name.get(loop.iter.attr)
                if container is None or container.container_kind not in ("ModuleList", "Sequential"):
                    continue
                for statement in loop.body:
                    for nested in ast.walk(statement):
                        if (isinstance(nested, ast.Call) and isinstance(nested.func, ast.Name)
                                and nested.func.id == loop.target.id):
                            loop_members[id(nested)] = container
            for call in ast.walk(forward):
                if not isinstance(call, ast.Call):
                    continue
                try:
                    expression = call.func
                    instance: ModuleInstance | None = None
                    possible: tuple[str, ...] = ()
                    unknown_selection = False
                    if (isinstance(expression, ast.Attribute)
                            and isinstance(expression.value, ast.Name)
                            and expression.value.id == "self"):
                        instance = by_name.get(expression.attr)
                        if instance is not None and instance.container_kind in ("ModuleList", "ModuleDict"):
                            continue  # These containers do not implement forward chaining.
                        call_name = expression.attr
                    elif (isinstance(expression, ast.Subscript)
                          and isinstance(expression.value, ast.Attribute)
                          and isinstance(expression.value.value, ast.Name)
                          and expression.value.value.id == "self"):
                        container = by_name.get(expression.value.attr)
                        if container is None or container.container_kind not in ("ModuleList", "ModuleDict"):
                            continue
                        call_name = ast.unparse(expression)
                        member_ids = container.member_instance_ids
                        if (container.container_kind == "ModuleList"
                                and isinstance(expression.slice, ast.Constant)
                                and type(expression.slice.value) is int
                                and 0 <= expression.slice.value < len(member_ids)):
                            member_id = member_ids[expression.slice.value]
                            if member_id is not None:
                                instance = next(item for item in self.instances if item.id == member_id)
                            else:
                                unknown_selection = True
                        elif (container.container_kind == "ModuleDict"
                              and isinstance(expression.slice, ast.Constant)
                              and isinstance(expression.slice.value, str)):
                            key = f"{expression.value.attr}[{expression.slice.value}]"
                            instance = by_name.get(key)
                        if instance is None:
                            instance = container
                            possible = (() if unknown_selection else
                                        tuple(item for item in member_ids if item is not None))
                    elif isinstance(expression, ast.Name):
                        call_name = expression.id
                        container = loop_members.get(id(call))
                        if container is not None:
                            instance = container
                            possible = tuple(item for item in container.member_instance_ids
                                             if item is not None)
                    else:
                        continue
                    if instance is None:
                        continue
                    callsite = self.callsites.get((parsed.relative_path, call.lineno,
                                                   call.col_offset, call_name,
                                                   definition.forward_symbol_id))
                    if callsite is None:
                        continue
                    confidence = (Confidence.UNRESOLVED if unknown_selection
                                  or instance.confidence == Confidence.UNRESOLVED
                                  or (instance.container_kind in ("ModuleList", "ModuleDict")
                                      and (not possible or None in instance.member_instance_ids))
                                  else Confidence.PROBABLE)
                    self.invocations.append(ModuleInvocation(
                        callsite, instance.id, callsite, _anchor(parsed, call), confidence,
                        (f"forward invokes {ast.unparse(expression)}; runtime target may change",),
                        possible,
                    ))
                except Exception as error:
                    self._model_error(parsed, error)

    def run(self) -> ModelAnalysis:
        self._collect_definitions()
        self._collect_instances()
        self._collect_invocations()
        return ModelAnalysis(tuple(self.definitions), tuple(self.instances),
                             tuple(self.invocations), tuple(self.issues))


def build_project_model(files: tuple[ParsedFile, ...], resolution: ResolutionResult,
                        flow: FlowAnalysis, rule: ModelRule | None = None) -> ModelAnalysis:
    """Recognize static module structure with a replaceable framework rule."""
    return _ModelBuilder(files, resolution, flow, rule or PyTorchModelRule()).run()
