"""Bounded, intraprocedural may def-use facts over resolved lexical bindings.

Binding proxies summarize static sites. Occurrence edges and READS metadata
retain reaching definitions; neither layer claims runtime value equivalence.
Heap writes and deferred bodies are deliberately outside this analysis.
Cross-function argument/return propagation is not modeled and reports Partial.
"""

import ast
from dataclasses import dataclass

from codeviz.core import (
    CodeEdge,
    CodeGraph,
    CodeNode,
    Confidence,
    DataRole,
    EdgeType,
    EntityRole,
    NodeKind,
    SourceAnchor,
    stable_node_id,
)

from .lexical_scopes import (
    IMPLICIT_SCOPES,
    ScopeLocations,
    definition_expressions,
    implicit_parts,
)
from .python import AnalysisIssue, ParsedFile
from .symbols import SymbolIndex

MAX_REACHING_DEFINITIONS = 32
MAX_SCOPE_WORK = 1024
MAX_FILE_WORK = 8192


class _WorkBudgetExceeded(Exception):
    def __init__(self, node: ast.AST, *, file_limit: bool = False) -> None:
        self.node = node
        self.file_limit = file_limit


@dataclass(frozen=True)
class _Definitions:
    ids: frozenset[str] = frozenset()
    may_unbound: bool = True
    unknown: bool = False
    truncated: bool = False


_Environment = dict[str, _Definitions]
_Reads = dict[str, _Definitions]


def _union(states: list[_Definitions]) -> _Definitions:
    ids = frozenset().union(*(state.ids for state in states))
    truncated = len(ids) > MAX_REACHING_DEFINITIONS or any(state.truncated for state in states)
    return _Definitions(frozenset(sorted(ids)[:MAX_REACHING_DEFINITIONS]),
                        any(state.may_unbound for state in states),
                        truncated or any(state.unknown for state in states), truncated)


def _merge(environments: list[_Environment]) -> _Environment | None:
    if not environments:
        return None
    keys = set().union(*(environment.keys() for environment in environments))
    return {key: _union([environment.get(key, _Definitions())
                         for environment in environments]) for key in keys}


class _DefUse:
    def __init__(self, parsed: ParsedFile, index: SymbolIndex, graph: CodeGraph,
                 proxies: dict[str, str], issues: list[AnalysisIssue]) -> None:
        assert parsed.tree is not None
        self.parsed, self.index, self.graph, self.proxies = parsed, index, graph, proxies
        self.issues = issues
        self.reported: set[tuple[str, SourceAnchor]] = set()
        self.unknown_expressions: set[ast.AST] = set()
        self.locations = ScopeLocations(parsed.tree, index, parsed.relative_path)
        self.refs = {(ref.scope_symbol_id, ref.source_anchor.start_line,
                      ref.source_anchor.start_column, ref.name): ref.resolved_definition_id
                     for ref in index.references
                     if ref.source_anchor.file_path == parsed.relative_path and ref.qualifier is None}
        self.children = {(entry.parent_symbol, entry.name): entry.id
                         for entry in index.entries.values() if entry.id in proxies}
        self.module = next(entry.id for entry in index.entries.values()
                           if entry.file_path == parsed.relative_path and entry.kind == NodeKind.MODULE)
        self.steps = {(node.source_anchors[0].start_line, node.source_anchors[0].start_column,
                       node.metadata.get("statement_type")): node.id
                      for node in graph.nodes.values()
                      if node.file_path == parsed.relative_path
                      and node.kind in (NodeKind.FLOW_STEP, NodeKind.CONTROL_BLOCK)}
        # Def-use runs before CALLS edges are built. Use resolved lexical facts,
        # not Task3 value candidates or a not-yet-populated call graph.
        self.project_calls = {
            (reference.scope_symbol_id, reference.source_anchor.start_line,
             reference.source_anchor.start_column, reference.name, reference.qualifier)
            for reference in index.references
            if reference.source_anchor.file_path == parsed.relative_path
            and any(target in graph.nodes and
                    (graph.nodes[target].kind in
                     (NodeKind.FUNCTION, NodeKind.METHOD, NodeKind.CLASS)
                     or reference.qualifier is None and
                     graph.nodes[target].kind in (NodeKind.PARAMETER, NodeKind.VARIABLE))
                    for target in (*reference.possible_target_ids,
                                   *((reference.resolved_definition_id,)
                                     if reference.resolved_definition_id is not None else ())))
        }
        # Enrich existing critical read/write sites instead of duplicating them.
        self.edge_slots: dict[tuple[str, str, EdgeType, SourceAnchor], int] = {
            (edge.source, edge.target, edge.type, edge.source_anchor): position
            for position, edge in enumerate(graph.edges)
            if edge.type in (EdgeType.READS, EdgeType.WRITES)
            and edge.source_anchor is not None
            and edge.source_anchor.file_path == parsed.relative_path
        }
        self.breaks: list[list[_Environment]] = []
        self.continues: list[list[_Environment]] = []
        self.exception_states: list[tuple[str, list[_Environment]]] = []
        self.active_scope: str | None = None
        self.analyzed_scopes: set[str] = set()
        self.work_used: dict[str, int] = {}
        self.file_work_used = 0
        self.scope_edge_slots: dict[str, set[int]] = {}
        self.scope_node_ids: dict[str, set[str]] = {}
        for node in graph.nodes.values():
            owner = node.metadata.get("owner_scope_id")
            if (node.file_path == parsed.relative_path and node.metadata.get("generic_data")
                    and isinstance(owner, str)):
                self.scope_node_ids.setdefault(owner, set()).add(node.id)

    def consume_work(self, node: ast.AST) -> None:
        assert self.active_scope is not None
        if self.file_work_used >= MAX_FILE_WORK:
            raise _WorkBudgetExceeded(node, file_limit=True)
        used = self.work_used.get(self.active_scope, 0)
        if used >= MAX_SCOPE_WORK:
            raise _WorkBudgetExceeded(node)
        self.work_used[self.active_scope] = used + 1
        self.file_work_used += 1

    def analyze_scope(self, body: list[ast.stmt], scope: str) -> None:
        # Deferred bodies are analyzed once with independent work and exit state.
        if scope in self.analyzed_scopes:
            return
        self.analyzed_scopes.add(scope)
        previous_scope = self.active_scope
        previous_exits = self.breaks, self.continues, self.exception_states
        self.active_scope = scope
        self.breaks, self.continues, self.exception_states = [], [], []
        try:
            if body and self.file_work_used >= MAX_FILE_WORK:
                # Diagnose omission without starting another statement traversal.
                raise _WorkBudgetExceeded(body[0], file_limit=True)
            self.block(body, scope, self.initial(scope))
        except _WorkBudgetExceeded as error:
            budget = MAX_FILE_WORK if error.file_limit else MAX_SCOPE_WORK
            code = ("GENERIC_DATA_FILE_WORK_BUDGET_PARTIAL" if error.file_limit
                    else "GENERIC_DATA_WORK_BUDGET_PARTIAL")
            self.partial(code, f"Def-use {'file' if error.file_limit else 'scope'} work truncated "
                         f"at {budget} visits; scope {self.index.entries[scope].qualified_name} "
                         "is incomplete and remaining statements/origins are unknown", error.node)
            # Keep discovered may-origins; do not present an aborted scope as complete.
            for slot in self.scope_edge_slots.get(scope, ()):
                edge = self.graph.edges[slot]
                edge.metadata.update(unknown=True, truncated=True, work_budget=budget)
            for node_id in self.scope_node_ids.get(scope, ()):
                self.graph.nodes[node_id].metadata.update(unknown=True, truncated=True, work_budget=budget)
            self.graph.nodes[scope].metadata["generic_def_use_work_truncated"] = True
            if error.file_limit:
                self.graph.nodes[self.module].metadata["generic_def_use_file_work_truncated"] = True
                self.graph.nodes[scope].metadata["generic_def_use_omitted"] = self.work_used.get(scope, 0) == 0
        finally:
            self.graph.nodes[scope].metadata.update(
                generic_def_use_work_visits=self.work_used.get(scope, 0),
                generic_def_use_work_budget=MAX_SCOPE_WORK)
            self.active_scope = previous_scope
            self.breaks, self.continues, self.exception_states = previous_exits

    def anchor(self, node: ast.AST) -> SourceAnchor:
        return SourceAnchor(self.parsed.relative_path, getattr(node, "lineno", 1),
                            getattr(node, "col_offset", None), getattr(node, "end_lineno", None),
                            getattr(node, "end_col_offset", None))

    def partial(self, code: str, message: str, node: ast.AST) -> None:
        anchor = self.anchor(node)
        if (code, anchor) not in self.reported:
            self.reported.add((code, anchor))
            self.issues.append(AnalysisIssue(code, message, self.parsed.relative_path, anchor))

    def edge(self, source: str, target: str, kind: EdgeType, node: ast.AST,
             state: _Definitions | None = None, *,
             definition_id: str | None = None, projection: str = "occurrence") -> None:
        anchor = self.anchor(node)
        key = source, target, kind, anchor
        metadata: dict[str, object] = {"generic_data": True, "basis": "static lexical def-use",
                                       "def_use_projection": projection}
        if definition_id is not None:
            metadata["definition_id"] = definition_id
        if state is not None:
            metadata.update(reaching_definition_ids=sorted(state.ids),
                            may_unbound=state.may_unbound, unknown=state.unknown,
                            truncated=state.truncated, definition_budget=MAX_REACHING_DEFINITIONS)
        slot = self.edge_slots.get(key)
        assert self.active_scope is not None
        self.scope_edge_slots.setdefault(self.active_scope, set()).add(
            slot if slot is not None else len(self.graph.edges))
        if slot is not None:
            if state is not None:
                old = self.graph.edges[slot].metadata
                previous = old.get("reaching_definition_ids", [])
                assert isinstance(previous, list)
                combined = _union([state, _Definitions(frozenset(previous),
                                   bool(old.get("may_unbound")), bool(old.get("unknown")),
                                   bool(old.get("truncated")))])
                metadata.update(reaching_definition_ids=sorted(combined.ids),
                                may_unbound=combined.may_unbound, unknown=combined.unknown,
                                truncated=combined.truncated)
        if metadata.get("truncated"):
            self.partial("GENERIC_DATA_BUDGET_PARTIAL",
                         f"Reaching definitions truncated at {MAX_REACHING_DEFINITIONS}; "
                         "additional possible origins remain unknown", node)
        if slot is not None:
            if state is None and self.graph.edges[slot].metadata.get("generic_data"):
                if (projection == "binding_summary" or
                        self.graph.edges[slot].metadata.get("def_use_projection") == "occurrence"):
                    return
            self.graph.edges[slot] = CodeEdge(source, target, kind, Confidence.PROBABLE,
                                              source_anchor=anchor, metadata=metadata)
        else:
            self.edge_slots[key] = len(self.graph.edges)
            self.graph.add_edge(CodeEdge(source, target, kind, Confidence.PROBABLE,
                                         source_anchor=anchor, metadata=metadata))

    def occurrence(self, node: ast.AST, scope: str, kind: str,
                   binding: str | None = None) -> str:
        owner = self.index.entries[scope]
        name = f"{self.index.entries[binding].name} ({kind})" if binding else kind
        path = self.locations.paths[node]
        node_id = stable_node_id(self.parsed.relative_path, f"{owner.qualified_name}.{name}",
                                 NodeKind.DATA, owner.qualified_name, EntityRole.INSTANCE,
                                 f"generic:{kind}:{path}:{binding or scope}")
        if node_id not in self.graph.nodes:
            self.graph.add_node(CodeNode(
                node_id, NodeKind.DATA, name, f"{owner.qualified_name}.{name}",
                owner.qualified_name, "python", (self.anchor(node),),
                role=EntityRole.INSTANCE, confidence=Confidence.PROBABLE,
                metadata={"generic_data": True, "data_role": DataRole.OTHER.value,
                          "owner_scope_id": scope, "def_use_kind": kind,
                          "binding_id": binding, "structural_key": path},
            ))
        assert self.active_scope is not None
        self.scope_node_ids.setdefault(self.active_scope, set()).add(node_id)
        return node_id

    def site(self, node: ast.AST, scope: str) -> str:
        return self.steps.get((getattr(node, "lineno", 0), getattr(node, "col_offset", 0),
                               type(node).__name__)) or self.occurrence(node, scope, "expression")

    def initial(self, scope: str) -> _Environment:
        return {entry.id: _Definitions(frozenset({self.proxies[entry.id]}), False)
                for entry in self.index.entries.values()
                if entry.parent_symbol == scope and entry.kind == NodeKind.PARAMETER
                and entry.id in self.proxies}

    def state(self, binding: str, scope: str, env: _Environment) -> _Definitions:
        if binding in env:
            return env[binding]
        entry = self.index.entries[binding]
        if entry.parent_symbol != scope:
            return _Definitions(frozenset({self.proxies[binding]}), True, True)
        return _Definitions()

    def binding(self, node: ast.Name, scope: str) -> str | None:
        # Store references are not necessarily indexed. Honor shared ownership
        # and the collector's global/nonlocal declarations without value lookup.
        current: str | None = scope
        skip_class = False
        nonlocal_search = False
        while current is not None:
            owner = self.index.entries[current]
            if nonlocal_search and owner.kind == NodeKind.MODULE:
                return None
            declaration = self.index.scope_declarations.get(current, {}).get(node.id)
            if declaration == "invalid_walrus":
                return None
            if declaration == "global" and current != self.module:
                if nonlocal_search:
                    current = owner.parent_symbol
                    continue
                current = self.module
                skip_class = False
                continue
            if declaration == "nonlocal":
                nonlocal_search = True
            if declaration != "nonlocal" and not (skip_class and owner.kind == NodeKind.CLASS):
                found = self.children.get((current, node.id))
                if found is not None:
                    return found
            if owner.kind in (NodeKind.FUNCTION, NodeKind.METHOD, NodeKind.LEXICAL_SCOPE):
                skip_class = True
            current = owner.parent_symbol
            if current is not None and self.index.entries[current].kind == NodeKind.FILE:
                return None
        return None

    def reads(self, expression: ast.AST | None, scope: str, env: _Environment,
              site: ast.AST) -> _Reads:
        found: _Reads = {}
        if expression is None:
            return found
        self.consume_work(expression)

        def combine(extra: _Reads) -> None:
            for binding, state in extra.items():
                found[binding] = _union([found[binding], state]) if binding in found else state

        if isinstance(expression, ast.Name) and isinstance(expression.ctx, ast.Load):
            binding = self.refs.get((scope, expression.lineno, expression.col_offset, expression.id))
            if binding in self.proxies:
                assert binding is not None
                found[binding] = self.state(binding, scope, env)
        elif isinstance(expression, ast.NamedExpr):
            combine(self.reads(expression.value, scope, env, expression))
            if expression in self.unknown_expressions:
                self.unknown_expressions.add(site)
            self.write(expression.target, expression, scope, env, found)
        elif isinstance(expression, ast.Call):
            for child in ast.iter_child_nodes(expression):
                combine(self.reads(child, scope, env, site))
            self.unknown_expressions.add(site)
            callee = expression.func
            name = callee.id if isinstance(callee, ast.Name) else (
                callee.attr if isinstance(callee, ast.Attribute) else None)
            qualifier = ast.unparse(callee.value) if isinstance(callee, ast.Attribute) else None
            if (scope, callee.lineno, callee.col_offset, name, qualifier) in self.project_calls:
                self.partial("GENERIC_DATA_CALL_PARTIAL",
                             "Def-use is intraprocedural only; interprocedural argument/parameter "
                             "and return propagation is not modeled", expression)
        elif isinstance(expression, ast.IfExp):
            combine(self.reads(expression.test, scope, env, site))
            left, right = dict(env), dict(env)
            combine(self.reads(expression.body, scope, left, site))
            combine(self.reads(expression.orelse, scope, right, site))
            merged = _merge([left, right])
            assert merged is not None
            env.clear()
            env.update(merged)
        elif isinstance(expression, ast.BoolOp):
            for position, operand in enumerate(expression.values):
                before = dict(env)
                combine(self.reads(operand, scope, env, site))
                if position:
                    merged = _merge([before, env])
                    assert merged is not None
                    env.clear()
                    env.update(merged)
        elif isinstance(expression, IMPLICIT_SCOPES):
            outer, inner = implicit_parts(expression)
            for child in outer:
                combine(self.reads(child, scope, env, site))
            if isinstance(expression, (ast.Lambda, ast.GeneratorExp)):
                return found
            implicit_scope = self.locations.owners[expression]
            local = dict(env)
            for child in inner:
                if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store):
                    self.write(child, expression, implicit_scope, local, found)
                elif isinstance(child, (ast.Tuple, ast.List)) and isinstance(child.ctx, ast.Store):
                    self.write(child, expression, implicit_scope, local, found)
                else:
                    combine(self.reads(child, implicit_scope, local, site))
            # Only walrus bindings in the enclosing scope can escape an eager comprehension.
            for binding, state in local.items():
                if self.index.entries[binding].parent_symbol != implicit_scope:
                    env[binding] = _union([env.get(binding, _Definitions()), state])
        else:
            for child in ast.iter_child_nodes(expression):
                combine(self.reads(child, scope, env, site))
        return found

    def record(self, node: ast.AST, scope: str, reads: _Reads) -> None:
        if not reads:
            return
        site = self.site(node, scope)
        for binding, state in reads.items():
            self.edge(site, self.proxies[binding], EdgeType.READS, node, state)

    def write(self, target: ast.AST, node: ast.AST, scope: str,
              env: _Environment, reads: _Reads) -> None:
        self.consume_work(node)
        if isinstance(target, (ast.Tuple, ast.List)):
            for child in target.elts:
                self.write(child, node, scope, env, reads)
            return
        if isinstance(target, ast.Starred):
            self.write(target.value, node, scope, env, reads)
            return
        if not isinstance(target, ast.Name):
            return
        binding = self.binding(target, scope)
        if binding is None:
            return
        occurrence = self.occurrence(node, scope, "assignment", binding)
        self.edge(self.site(node, scope), self.proxies[binding], EdgeType.WRITES, node,
                  definition_id=occurrence)
        for source, definitions in reads.items():
            if source != binding:
                self.edge(self.proxies[source], self.proxies[binding], EdgeType.DATA_FLOW, node,
                          projection="binding_summary")
            for origin in definitions.ids:
                self.edge(origin, occurrence, EdgeType.DATA_FLOW, node)
        env[binding] = _Definitions(frozenset({occurrence}), False,
                                    node in self.unknown_expressions or
                                    any(state.unknown or state.may_unbound for state in reads.values()))

    def delete(self, target: ast.AST, scope: str, env: _Environment) -> None:
        if isinstance(target, (ast.Tuple, ast.List)):
            for child in target.elts:
                self.delete(child, scope, env)
        elif isinstance(target, ast.Starred):
            self.delete(target.value, scope, env)
        elif isinstance(target, ast.Name):
            binding = self.binding(target, scope)
            if binding is not None:
                env[binding] = _Definitions()

    def unsupported_bindings(self, names: set[str], node: ast.AST,
                             scope: str, env: _Environment) -> None:
        if not names:
            return
        self.partial("GENERIC_DATA_BINDING_PARTIAL",
                     "Pattern/exception capture definition propagation is not modeled: "
                     + ", ".join(sorted(names)), node)
        for name in names:
            binding = self.children.get((scope, name))
            if binding is not None:
                env[binding] = _Definitions(unknown=True)

    def block(self, body: list[ast.stmt], scope: str,
              env: _Environment) -> _Environment | None:
        current: _Environment | None = dict(env)
        for node in body:
            if current is None:
                break
            statement_env = current
            for owner, states in self.exception_states:
                if owner == scope:
                    states.append(dict(statement_env))
            current = self.statement(node, scope, statement_env)
            if current is not None and any(state.truncated for state in current.values()):
                self.partial("GENERIC_DATA_BUDGET_PARTIAL",
                             f"Reaching definitions truncated at {MAX_REACHING_DEFINITIONS}; "
                             "additional possible origins remain unknown", node)
            for owner, states in self.exception_states:
                if owner == scope:
                    states.append(dict(statement_env))
        return current

    def statement(self, node: ast.stmt, scope: str,
                  env: _Environment) -> _Environment | None:
        self.consume_work(node)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            reads: _Reads = {}
            for expression in definition_expressions(node):
                reads.update(self.reads(expression, scope, env, node))
            self.record(node, scope, reads)
            owner = self.locations.owners.get(node)
            if owner is not None:
                self.analyze_scope(node.body, owner)
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            if isinstance(node, ast.AnnAssign) and node.value is None:
                return env
            reads = self.reads(node.value, scope, env, node)
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name):
                binding = self.binding(node.target, scope)
                if binding is not None:
                    reads[binding] = self.state(binding, scope, env)
            for target in targets:
                if isinstance(target, (ast.Attribute, ast.Subscript)):
                    reads.update(self.reads(target, scope, env, node))
            self.record(node, scope, reads)
            for target in targets:
                self.write(target, node, scope, env, reads)
        elif isinstance(node, ast.If):
            self.record(node, scope, self.reads(node.test, scope, env, node))
            branches = [self.block(node.body, scope, env), self.block(node.orelse, scope, env)]
            return _merge([branch for branch in branches if branch is not None])
        elif isinstance(node, (ast.For, ast.AsyncFor, ast.While)):
            # The first iterator/condition is evaluated even when no body runs.
            initial_expression = node.test if isinstance(node, ast.While) else node.iter
            initial_reads = self.reads(initial_expression, scope, env, node)
            self.record(node, scope, initial_reads)
            before = dict(env)
            head = dict(env)
            breaks: list[_Environment] = []
            continues: list[_Environment] = []
            self.breaks.append(breaks)
            self.continues.append(continues)
            # Two may iterations retain zero-iteration origins. The scope work
            # budget also bounds their multiplicative cost in nested loops.
            # A capped result is explicitly marked unknown, never exact.
            try:
                for iteration_index in range(2):
                    iteration = dict(head)
                    reads = (self.reads(node.test, scope, iteration, node)
                             if isinstance(node, ast.While) and iteration_index else initial_reads)
                    self.record(node, scope, reads)
                    if not isinstance(node, ast.While):
                        self.write(node.target, node, scope, iteration, reads)
                    normal = self.block(node.body, scope, iteration)
                    head = _merge([before, *continues,
                                   *([normal] if normal is not None else [])]) or before
            finally:
                self.breaks.pop()
                self.continues.pop()
            head = {key: _Definitions(state.ids, state.may_unbound, True, state.truncated)
                    for key, state in head.items()}
            if isinstance(node, ast.While):
                self.record(node, scope, self.reads(node.test, scope, head, node))
            exhausted = self.block(node.orelse, scope, head)
            return _merge([*breaks, *([exhausted] if exhausted is not None else [])])
        elif isinstance(node, (ast.Return, ast.Raise)):
            returned_expression = node.value if isinstance(node, ast.Return) else node.exc
            reads = self.reads(returned_expression, scope, env, node)
            if isinstance(node, ast.Raise):
                reads.update(self.reads(node.cause, scope, env, node))
            self.record(node, scope, reads)
            if isinstance(node, ast.Return):
                returned = self.occurrence(node, scope, "return")
                for binding, definitions in reads.items():
                    self.edge(self.proxies[binding], returned, EdgeType.DATA_FLOW, node,
                              projection="binding_summary")
                    for origin in definitions.ids:
                        self.edge(origin, returned, EdgeType.DATA_FLOW, node)
            return None
        elif isinstance(node, (ast.Break, ast.Continue)):
            stack = self.breaks if isinstance(node, ast.Break) else self.continues
            if stack:
                stack[-1].append(dict(env))
            return None
        elif isinstance(node, ast.Delete):
            for target in node.targets:
                self.delete(target, scope, env)
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                reads = self.reads(item.context_expr, scope, env, node)
                self.record(node, scope, reads)
                if item.optional_vars is not None:
                    self.write(item.optional_vars, node, scope, env, reads)
            return self.block(node.body, scope, env)
        elif isinstance(node, (ast.Try, ast.TryStar)):
            # Exception paths may arise at any site, including before a write.
            loop_exits = [(exits, len(exits)) for stack in (self.breaks, self.continues)
                          for exits in stack[-1:]]
            snapshots: list[_Environment] = [dict(env)]
            self.exception_states.append((scope, snapshots))
            try:
                attempted = self.block(node.body, scope, env)
                exceptional = _merge(snapshots) or env
                branches = []
                for handler in node.handlers:
                    handler_env = dict(exceptional)
                    self.unsupported_bindings({handler.name} if handler.name else set(),
                                              handler, scope, handler_env)
                    branch = self.block(handler.body, scope, handler_env)
                    if branch is not None and handler.name is not None:
                        binding = self.children.get((scope, handler.name))
                        if binding is not None:
                            branch[binding] = _Definitions()
                    branches.append(branch)
                if attempted is not None:
                    branches.append(self.block(node.orelse, scope, attempted))
            finally:
                self.exception_states.pop()
            normal = _merge([branch for branch in branches if branch is not None])
            pending_exits = [(exits, list(exits[start:])) for exits, start in loop_exits]
            for exits, start in loop_exits:
                del exits[start:]
            # Finally is observed on both normal and terminating paths. Only
            # the normal environment may continue to the statement after try.
            observed = _merge(snapshots) or env
            exit_contexts = [exit_env for _, pending in pending_exits for exit_env in pending]
            contexts = [*exit_contexts, *([normal] if normal is not None else [])]
            approximate_finally = (
                any(context != observed for context in contexts) and
                any(isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store)
                    for statement in node.finalbody for child in ast.walk(statement)))
            if approximate_finally:
                self.partial("GENERIC_DATA_FINALLY_PARTIAL",
                             "Finally assignment occurrences merge normal and exiting contexts; "
                             "downstream origins are approximate and remain unknown", node.finalbody[0])
                observed = {key: _Definitions(state.ids, state.may_unbound, True, state.truncated)
                            for key, state in observed.items()}
                if normal is not None:
                    normal = {key: _Definitions(state.ids, state.may_unbound, True, state.truncated)
                              for key, state in normal.items()}
            previous_breaks, previous_continues = self.breaks, self.continues
            self.breaks, self.continues = [], []
            try:
                self.block(node.finalbody, scope, observed)
            finally:
                self.breaks, self.continues = previous_breaks, previous_continues
            for exits, pending in pending_exits:
                for exit_env in pending:
                    if approximate_finally:
                        exit_env = {key: _Definitions(state.ids, state.may_unbound, True, state.truncated)
                                    for key, state in exit_env.items()}
                    final_exit = self.block(node.finalbody, scope, exit_env)
                    if final_exit is not None:
                        exits.append(final_exit)
            return self.block(node.finalbody, scope, normal) if normal is not None else None
        elif isinstance(node, ast.Match):
            self.record(node, scope, self.reads(node.subject, scope, env, node))
            match_branches = [env]  # unmatched path is conservatively possible
            for case in node.cases:
                branch_env = dict(env)
                captured = {name for pattern in ast.walk(case.pattern)
                            for name in (getattr(pattern, "name", None),
                                         getattr(pattern, "rest", None)) if isinstance(name, str)}
                self.unsupported_bindings(captured, case.pattern, scope, branch_env)
                self.record(node, scope, self.reads(case.guard, scope, branch_env, node))
                branch = self.block(case.body, scope, branch_env)
                if branch is not None:
                    match_branches.append(branch)
            return _merge(match_branches)
        else:
            reads = {}
            for child in ast.iter_child_nodes(node):
                if isinstance(child, ast.expr):
                    reads.update(self.reads(child, scope, env, node))
            self.record(node, scope, reads)
        return env


def add_generic_def_use(files: tuple[ParsedFile, ...], index: SymbolIndex,
                        graph: CodeGraph, issues: list[AnalysisIssue]) -> None:
    """Enrich the full graph without changing the critical ML DataNode list."""
    proxies = {symbol: node.id for node in graph.nodes.values()
               if node.kind == NodeKind.DATA and isinstance(symbol := node.metadata.get("symbol_id"), str)}
    for entry in index.entries.values():
        if (entry.kind not in (NodeKind.PARAMETER, NodeKind.VARIABLE)
                or not entry.definition or "imported_from" in entry.metadata or entry.id in proxies):
            continue
        node_id = stable_node_id(entry.file_path, entry.qualified_name, NodeKind.DATA,
                                 entry.scope, occurrence_key=f"generic-symbol:{entry.id}")
        proxies[entry.id] = node_id
        graph.add_node(CodeNode(
            node_id, NodeKind.DATA, entry.name, entry.qualified_name, entry.scope, "python",
            (entry.source_anchor,), confidence=Confidence.PROBABLE,
            metadata={"generic_data": True, "data_role": DataRole.OTHER.value,
                      "owner_scope_id": entry.parent_symbol, "symbol_id": entry.id,
                      "def_use_kind": "binding", "basis": "lexical binding"},
        ))
        graph.add_edge(CodeEdge(entry.id, node_id, EdgeType.CONTAINS,
                                source_anchor=entry.source_anchor, metadata={"generic_data": True}))
    indexed_modules = {entry.file_path for entry in index.entries.values()
                       if entry.kind == NodeKind.MODULE}
    for parsed in files:
        if not isinstance(parsed.tree, ast.Module) or parsed.relative_path not in indexed_modules:
            continue
        try:
            visitor = _DefUse(parsed, index, graph, proxies, issues)
            visitor.analyze_scope(parsed.tree.body, visitor.module)
            # An exhausted enclosing scope must not suppress deferred sibling bodies.
            for node in ast.walk(parsed.tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    owner = visitor.locations.owners.get(node)
                    if owner is not None:
                        visitor.analyze_scope(node.body, owner)
            graph.nodes[visitor.module].metadata.update(
                generic_def_use_file_work_visits=visitor.file_work_used,
                generic_def_use_file_work_budget=MAX_FILE_WORK)
        except Exception as error:
            issues.append(AnalysisIssue("GENERIC_DATA_FLOW_ERROR", f"{type(error).__name__}: {error}",
                                        parsed.relative_path, SourceAnchor(parsed.relative_path, 1)))
