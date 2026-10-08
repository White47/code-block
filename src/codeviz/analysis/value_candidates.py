"""Bounded call-position values, separate from lexical definition ownership.

These are possible callable/instance values, not runtime types or an MRO.
Deferred bodies use isolated environments; nothing in the target is executed.
"""

import ast
from dataclasses import dataclass, replace

from codeviz.core import CodeGraph, EdgeType, NodeKind, SourceAnchor

from .lexical_scopes import (
    IMPLICIT_SCOPES,
    ScopeLocations,
    definition_expressions,
    implicit_parts,
)
from .python import AnalysisIssue, ParsedFile
from .relations import RelationTargets
from .resolution import ResolutionResult

MAX_CANDIDATES = 16
MAX_INHERIT_DEPTH = 16
MAX_LOOP_ITERATIONS = 8
MAX_METHOD_WORK = 4096
MAX_VALUE_FILE_WORK = 4096


@dataclass(frozen=True, slots=True)
class CandidateValue:
    targets: tuple[str, ...] = ()
    instances: tuple[str, ...] = ()
    unknown: bool = False
    may_unbound: bool = False
    truncated: bool = False
    indirect: bool = False


Environment = dict[tuple[str, str], CandidateValue]


class _ValueWorkExhausted(Exception):
    """Stop repeated evaluation; keep the last admitted environment for recovery."""

    def __init__(self, env: Environment) -> None:
        super().__init__("candidate file traversal work exhausted")
        self.env = dict(env)


class ValueCandidates:
    def __init__(self, parsed: ParsedFile, resolution: ResolutionResult, graph: CodeGraph,
                 relations: RelationTargets, issues: list[AnalysisIssue]) -> None:
        assert parsed.tree is not None
        self.parsed = parsed
        self.index = resolution.symbol_index
        self.graph = graph
        self.relations = relations
        self.issues = issues
        self.locations = ScopeLocations(parsed.tree, self.index, parsed.relative_path)
        self.module_id = relations.catalog.by_file[parsed.relative_path].id
        self.calls: dict[ast.Call, CandidateValue] = {}
        self.super_receivers: dict[str, tuple[str, str]] = {}
        self.invalid_names: set[tuple[str, str]] = set()
        self.reported: set[tuple[str, int, int | None]] = set()
        self.method_work = 0
        self.value_work = 0
        self.work_anchor: ast.AST = parsed.tree
        self.method_cache: dict[tuple[str, str, int], CandidateValue] = {}
        self.method_partial_cache: dict[
            tuple[tuple[str, str, int], frozenset[str]], CandidateValue
        ] = {}
        self.comprehension_calls: list[set[ast.Call]] = []
        self.bases: dict[str, list[str]] = {}
        for edge in graph.edges:
            if edge.type == EdgeType.INHERITS:
                self.bases.setdefault(edge.source, []).append(edge.target)
        self.references = {(ref.scope_symbol_id, ref.source_anchor.start_line,
                            ref.source_anchor.start_column, ref.name, ref.qualifier): ref
                           for ref in self.index.references
                           if ref.source_anchor.file_path == parsed.relative_path}

    def _issue(self, code: str, node: ast.AST, message: str) -> None:
        key = (code, getattr(node, "lineno", 1), getattr(node, "col_offset", None))
        if key not in self.reported:
            self.reported.add(key)
            self.issues.append(AnalysisIssue(code, message, self.parsed.relative_path,
                                            SourceAnchor(self.parsed.relative_path, key[1],
                                                         start_column=key[2])))

    def _charge(self, node: ast.AST, env: Environment) -> None:
        """Count actual statement/expression admissions across all file scopes."""
        if hasattr(node, "lineno"):
            self.work_anchor = node
        if self.value_work >= MAX_VALUE_FILE_WORK:
            self._issue("VALUE_FILE_WORK_BUDGET_PARTIAL", self.work_anchor,
                        f"candidate statement/expression work exceeds {MAX_VALUE_FILE_WORK}; "
                        "remaining call-position facts are incomplete")
            raise _ValueWorkExhausted(env)
        self.value_work += 1

    def _incomplete_calls(self, env: Environment) -> None:
        """A single syntax pass prevents lexical Exact fallback after a cut.

        No expressions or deferred bodies are evaluated here. Already found
        candidates survive, but every remaining call is explicitly incomplete.
        """
        references = {(ref.source_anchor.start_line, ref.source_anchor.start_column,
                       ref.name, ref.qualifier): ref for ref in self.references.values()}
        assert self.parsed.tree is not None
        for node in ast.walk(self.parsed.tree):
            if not isinstance(node, ast.Call):
                continue
            value = self.calls.get(node, CandidateValue())
            if node not in self.calls and isinstance(node.func, ast.Name):
                ref = references.get((node.func.lineno, node.func.col_offset, node.func.id, None))
                if ref is not None and ref.scope_symbol_id is not None:
                    owner = self._binding_scope(node.func.id, ref.scope_symbol_id)
                    known = env.get((owner, node.func.id)) if owner is not None else None
                    if known is not None:
                        value = self._union(value, known, node)
            self.calls[node] = replace(value, unknown=True, truncated=True, indirect=True)
        self.graph.nodes[self.module_id].metadata.update(
            value_file_work_truncated=True, value_file_work_visits=self.value_work)

    def _bounded(self, value: CandidateValue, node: ast.AST) -> CandidateValue:
        ids = tuple(dict.fromkeys((*value.targets, *value.instances)))
        if len(ids) <= MAX_CANDIDATES:
            return value
        self._issue("VALUE_CANDIDATE_BUDGET", node, "value candidates exceed 16; unknown retained")
        allowed = set(ids[:MAX_CANDIDATES])
        return replace(value, targets=tuple(x for x in value.targets if x in allowed),
                       instances=tuple(x for x in value.instances if x in allowed),
                       unknown=True, truncated=True, indirect=True)

    def _union(self, left: CandidateValue, right: CandidateValue, node: ast.AST) -> CandidateValue:
        return self._bounded(CandidateValue(
            tuple(dict.fromkeys((*left.targets, *right.targets))),
            tuple(dict.fromkeys((*left.instances, *right.instances))),
            left.unknown or right.unknown, left.may_unbound or right.may_unbound,
            left.truncated or right.truncated, left.indirect or right.indirect or left != right,
        ), node)

    def _join(self, left: Environment, right: Environment, node: ast.AST) -> Environment:
        unbound = CandidateValue(may_unbound=True, indirect=True)
        return {key: self._union(left.get(key, unbound), right.get(key, unbound), node)
                for key in left.keys() | right.keys()}

    def _binding_scope(self, name: str, scope: str) -> str | None:
        return self.relations.query.binding_owner(name, scope, self.parsed.relative_path).owner_id

    def _name(self, node: ast.Name, scope: str, env: Environment) -> CandidateValue:
        owner = self._binding_scope(node.id, scope)
        if owner is not None:
            declaration = self.index.scope_declarations.get(owner, {}).get(node.id)
            if ((owner, node.id) in self.invalid_names
                    or declaration is not None and declaration.startswith("invalid")):
                return CandidateValue(unknown=True, indirect=True)
            return env.get((owner, node.id), CandidateValue(may_unbound=True, indirect=True))
        ref = self.references.get((scope, node.lineno, node.col_offset, node.id, None))
        ids = ((ref.resolved_definition_id,) if ref and ref.resolved_definition_id else
               ref.possible_target_ids if ref else ())
        return CandidateValue(ids, unknown=not bool(ids), indirect=len(ids) != 1)

    def _store(self, target: ast.AST, value: CandidateValue, scope: str,
               env: Environment) -> None:
        if isinstance(target, ast.Name):
            owner = self._binding_scope(target.id, scope) or scope
            env[(owner, target.id)] = replace(value, indirect=True)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for element in target.elts:
                self._store(element, CandidateValue(unknown=True, may_unbound=value.may_unbound,
                                                    truncated=value.truncated), scope, env)
        elif isinstance(target, ast.Starred):
            self._store(target.value, CandidateValue(unknown=True), scope, env)
        elif isinstance(target, ast.Attribute):
            receiver = self._expr(target.value, scope, env, [])
            for class_id in (*receiver.instances, *receiver.targets):
                env[(class_id, "$member:" + target.attr)] = CandidateValue(unknown=True)

    def _methods(self, class_id: str, name: str, node: ast.AST,
                 visited: frozenset[str] = frozenset()) -> CandidateValue:
        if class_id in visited or len(visited) >= MAX_INHERIT_DEPTH:
            self._issue("VALUE_INHERIT_BUDGET", node, "inheritance cycle/depth limit; unknown retained")
            return CandidateValue(unknown=True, truncated=True, indirect=True)
        cache_key = (class_id, name, MAX_INHERIT_DEPTH - len(visited))
        cached = self.method_cache.get(cache_key)
        if cached is not None:
            return cached
        own = self.relations.children.get((class_id, name), ())
        methods = tuple(entry.id for entry in own if entry.kind == NodeKind.METHOD)
        if methods:
            return self._bounded(CandidateValue(methods, unknown=len(methods) != len(own),
                                                 indirect=True), node)
        if own:
            # A nearer class binding shadows inherited methods even if its
            # callable value is opaque (for example ``Child.run = None``).
            return CandidateValue(unknown=True, indirect=True)
        if self.method_work >= MAX_METHOD_WORK:
            self._issue("VALUE_INHERIT_WORK_BUDGET_PARTIAL", node,
                        f"inheritance graph work exceeds {MAX_METHOD_WORK} expansions; unknown retained")
            return self.method_partial_cache.get(
                (cache_key, visited), CandidateValue(unknown=True, truncated=True, indirect=True))
        self.method_work += 1
        partial = bool(self.graph.nodes[class_id].metadata.get("inheritance_partial"))
        result = CandidateValue(unknown=partial, truncated=partial, indirect=True)
        for base in self.bases.get(class_id, ()):
            if self.graph.nodes[base].kind != NodeKind.CLASS:
                result = replace(result, unknown=True)
            else:
                result = self._union(result, self._methods(base, name, node, visited | {class_id}), node)
        if not result.targets:
            result = replace(result, unknown=True)
        if not result.truncated:
            # Depth is part of the context. Visiting/cyclic and truncated
            # results cannot masquerade as a completed DAG summary.
            self.method_cache[cache_key] = result
        elif self.method_work >= MAX_METHOD_WORK and result.targets:
            self.method_partial_cache[(cache_key, visited)] = result
        return result

    def _attribute(self, node: ast.Attribute, scope: str, env: Environment,
                   pending: list[ast.AST]) -> CandidateValue:
        receiver = self._expr(node.value, scope, env, pending)
        result = CandidateValue(unknown=receiver.unknown, may_unbound=receiver.may_unbound,
                                truncated=receiver.truncated, indirect=receiver.indirect)
        for class_id in receiver.instances:
            result = self._union(result, self._methods(class_id, node.attr, node), node)
            if (class_id, "$member:" + node.attr) in env:
                result = replace(result, unknown=True, indirect=True)
        for target in receiver.targets:
            kind = self.graph.nodes[target].kind
            if kind == NodeKind.CLASS:
                result = self._union(result, self._methods(target, node.attr, node), node)
                if (target, "$member:" + node.attr) in env:
                    result = replace(result, unknown=True, indirect=True)
            elif kind == NodeKind.MODULE:
                entries = self.relations.children.get((target, node.attr), ())
                ids: list[str] = []
                partial = False
                truncated = False
                for entry in entries:
                    if "imported_from" in entry.metadata:
                        fact = self.relations.import_fact(entry)
                        ids.extend(fact.ids)
                        partial = partial or bool(fact.issue_code) or not bool(fact.ids)
                        truncated = truncated or fact.truncated
                        if fact.issue_code == "IMPORT_WORK_BUDGET_PARTIAL":
                            self._issue(fact.issue_code, node, fact.reason or "import work truncated")
                    else:
                        ids.append(entry.id)
                # A plain dotted import binds the package but depends on its submodule.
                module = self.graph.nodes[target].qualified_name
                ids.extend(item.id for item in self.relations.catalog.candidates(module + "." + node.attr))
                result = self._union(result, CandidateValue(tuple(dict.fromkeys(ids)),
                                                            unknown=partial or not bool(ids),
                                                            truncated=truncated), node)
            else:
                ref = self.references.get((scope, node.lineno, node.col_offset,
                                           node.attr, ast.unparse(node.value)))
                referenced_ids = ((ref.resolved_definition_id,) if ref and ref.resolved_definition_id else
                                  ref.possible_target_ids if ref else ())
                external = tuple(x for x in referenced_ids
                                 if self.graph.nodes[x].kind == NodeKind.EXTERNAL_SYMBOL)
                result = self._union(result, CandidateValue(external, unknown=not bool(external)), node)
        return self._bounded(result, node)

    def _expr(self, node: ast.AST, scope: str, env: Environment,
              pending: list[ast.AST]) -> CandidateValue:
        self._charge(node, env)
        if isinstance(node, ast.Name):
            return self._bounded(self._name(node, scope, env), node)
        if isinstance(node, ast.Attribute):
            return self._attribute(node, scope, env, pending)
        if isinstance(node, ast.NamedExpr):
            value = self._expr(node.value, scope, env, pending)
            self._store(node.target, value, scope, env)
            return value
        if isinstance(node, ast.IfExp):
            self._expr(node.test, scope, env, pending)
            left, right = dict(env), dict(env)
            value = self._union(self._expr(node.body, scope, left, pending),
                                self._expr(node.orelse, scope, right, pending), node)
            env.update(self._join(left, right, node))
            return value
        if isinstance(node, ast.BoolOp):
            result = CandidateValue()
            for position, operand in enumerate(node.values):
                before = dict(env)
                value = self._expr(operand, scope, env, pending)
                result = self._union(result, value, node)
                if position:
                    # Both and/or can skip every operand after the first.
                    env.update(self._join(before, env, node))
            return result
        if isinstance(node, IMPLICIT_SCOPES):
            outer, inner = implicit_parts(node)
            for child in outer:
                self._expr(child, scope, env, pending)
            if isinstance(node, (ast.Lambda, ast.GeneratorExp)):
                pending.append(node)
            else:
                local = dict(env)
                implicit_scope = self.locations.owners[node]
                calls: set[ast.Call] = set()
                self.comprehension_calls.append(calls)
                try:
                    for child in inner:
                        if isinstance(getattr(child, "ctx", None), ast.Store):
                            self._store(child, CandidateValue(unknown=True), implicit_scope, local)
                        else:
                            self._expr(child, implicit_scope, local, pending)
                finally:
                    self.comprehension_calls.pop()
                # A comprehension can be empty. Only writes to enclosing
                # bindings escape; iteration variables stay in its own scope.
                for key, value in local.items():
                    if key[0] != implicit_scope and value != env.get(key):
                        self._issue("VALUE_COMPREHENSION_PARTIAL", node,
                                    "eager comprehension rebinding may affect later iterations; unknown retained")
                        for call in calls:
                            self.calls[call] = replace(self.calls[call], unknown=True, indirect=True)
                        env[key] = self._union(
                            env.get(key, CandidateValue(may_unbound=True, indirect=True)),
                            replace(value, unknown=True, indirect=True), node,
                        )
            return CandidateValue(unknown=True)
        if isinstance(node, ast.Call):
            for calls in self.comprehension_calls:
                calls.add(node)
            value = self._expr(node.func, scope, env, pending)
            previous = self.calls.get(node)
            self.calls[node] = self._union(previous, value, node) if previous else value
            for argument in (*node.args, *(keyword.value for keyword in node.keywords)):
                self._expr(argument, scope, env, pending)
            if (isinstance(node.func, ast.Name) and node.func.id == "super"
                    and not node.args and not node.keywords
                    and self._binding_scope("super", scope) is None):
                receiver = self.super_receivers.get(scope)
                class_id = receiver[0] if receiver else None
                receiver_value = env.get((scope, receiver[1])) if receiver else None
                if (receiver_value is None or receiver_value.unknown or receiver_value.may_unbound
                        or class_id not in (*receiver_value.instances, *receiver_value.targets)):
                    return CandidateValue(unknown=True, indirect=True)
                all_bases = self.bases.get(class_id or "", ())
                bases = tuple(base for base in all_bases
                              if self.graph.nodes[base].kind == NodeKind.CLASS)
                partial = bool(class_id and self.graph.nodes[class_id].metadata.get("inheritance_partial"))
                return self._bounded(CandidateValue(
                    instances=bases, unknown=partial or not bool(bases) or len(bases) != len(all_bases),
                    truncated=partial, indirect=True,
                ), node)
            classes = tuple(target for target in value.targets if self.graph.nodes[target].kind == NodeKind.CLASS)
            return CandidateValue(instances=classes, unknown=value.unknown or not bool(classes)
                                  or len(classes) != len(value.targets), may_unbound=value.may_unbound,
                                  truncated=value.truncated, indirect=True)
        for child in ast.iter_child_nodes(node):
            self._expr(child, scope, env, pending)
        return CandidateValue(unknown=True)

    def _definition(self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
                    scope: str, env: Environment, pending: list[ast.AST]) -> None:
        for expression in definition_expressions(node):
            self._expr(expression, scope, env, pending)
        owner = self.locations.owners.get(node)
        if owner is None:
            return
        binding_owner = self._binding_scope(node.name, scope) or scope
        key = (binding_owner, node.name)
        value = CandidateValue((owner,), unknown=bool(node.decorator_list),
                               indirect=bool(node.decorator_list) or key in env)
        # Executing a definition replaces that binding. Alternatives are
        # combined by branch joins, not by accumulating sequential definitions.
        env[key] = value
        if isinstance(node, ast.ClassDef):
            self._scope(node, owner, dict(env), defer_to=pending)
        else:
            pending.append(node)

    def _block(self, body: list[ast.stmt], scope: str, env: Environment,
               pending: list[ast.AST]) -> None:
        for node in body:
            self._charge(node, env)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                self._definition(node, scope, env, pending)
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                if node.value is not None:
                    value = self._expr(node.value, scope, env, pending)
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    for target in targets:
                        self._store(target, value, scope, env)
            elif isinstance(node, (ast.AugAssign, ast.Delete)):
                self._expr(node, scope, env, pending)
                targets = node.targets if isinstance(node, ast.Delete) else [node.target]
                for target in targets:
                    self._store(target, CandidateValue(unknown=not isinstance(node, ast.Delete),
                                                       may_unbound=isinstance(node, ast.Delete)), scope, env)
            elif isinstance(node, ast.If):
                self._expr(node.test, scope, env, pending)
                left, right = dict(env), dict(env)
                self._block(node.body, scope, left, pending)
                self._block(node.orelse, scope, right, pending)
                env.update(self._join(left, right, node))
            elif isinstance(node, (ast.For, ast.AsyncFor, ast.While)):
                self._loop(node, scope, env, pending)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    if alias.name == "*":
                        continue
                    name = alias.asname or (alias.name.split(".")[0]
                                           if isinstance(node, ast.Import) else alias.name)
                    owner = self._binding_scope(name, scope) or scope
                    raw = alias.name if isinstance(node, ast.Import) else (
                        "." * node.level + (node.module + "." if node.module else "") + alias.name)
                    entries = [entry for entry in self.relations.children.get((owner, name), ())
                               if entry.metadata.get("imported_from") == raw]
                    entries.sort(key=lambda entry: entry.source_anchor.start_line != node.lineno)
                    if entries:
                        fact = self.relations.import_fact(entries[0])
                        if fact.issue_code == "IMPORT_WORK_BUDGET_PARTIAL":
                            self._issue(fact.issue_code, node, fact.reason or "import work truncated")
                        env[(owner, name)] = self._bounded(CandidateValue(
                            fact.ids, unknown=not bool(fact.ids) or bool(fact.issue_code),
                            truncated=fact.truncated,
                            indirect=bool(fact.issue_code),
                        ), node)
            elif isinstance(node, (ast.With, ast.AsyncWith)):
                for item in node.items:
                    self._expr(item.context_expr, scope, env, pending)
                    if item.optional_vars is not None:
                        self._store(item.optional_vars, CandidateValue(unknown=True), scope, env)
                self._block(node.body, scope, env, pending)
            elif isinstance(node, (ast.Try, ast.TryStar)):
                before = dict(env)
                self._block(node.body, scope, env, pending)
                self._block(node.orelse, scope, env, pending)
                for handler in node.handlers:
                    branch = dict(before)
                    if handler.name:
                        branch[(scope, handler.name)] = CandidateValue(unknown=True)
                    self._block(handler.body, scope, branch, pending)
                    env.update(self._join(env, branch, node))
                self._block(node.finalbody, scope, env, pending)
            elif isinstance(node, ast.Match):
                self._expr(node.subject, scope, env, pending)
                before = dict(env)
                for case in node.cases:
                    branch = dict(before)
                    for pattern in ast.walk(case.pattern):
                        capture_name = (pattern.rest if isinstance(pattern, ast.MatchMapping)
                                        else getattr(pattern, "name", None))
                        if isinstance(capture_name, str):
                            owner = self._binding_scope(capture_name, scope) or scope
                            branch[(owner, capture_name)] = CandidateValue(unknown=True, indirect=True)
                    if case.guard:
                        self._expr(case.guard, scope, branch, pending)
                    self._block(case.body, scope, branch, pending)
                    env.update(self._join(env, branch, node))
            else:
                self._expr(node, scope, env, pending)

    def _loop(self, node: ast.For | ast.AsyncFor | ast.While, scope: str,
              env: Environment, pending: list[ast.AST]) -> None:
        before = dict(env)
        state = dict(env)
        for _ in range(MAX_LOOP_ITERATIONS):
            branch = dict(state)
            if isinstance(node, ast.While):
                self._expr(node.test, scope, branch, pending)
            else:
                self._expr(node.iter, scope, branch, pending)
                self._store(node.target, CandidateValue(unknown=True), scope, branch)
            self._block(node.body, scope, branch, pending)
            joined = self._join(before, branch, node)
            if joined == state:
                state = joined
                break
            state = joined
        else:
            self._issue("VALUE_LOOP_BUDGET", node, "loop did not converge in 8 iterations; unknown retained")
            state = {key: replace(value, unknown=True, truncated=True, indirect=True)
                     if value != before.get(key) else value for key, value in state.items()}
        env.update(state)
        self._block(node.orelse, scope, env, pending)

    def _scope(self, node: ast.AST, scope: str, env: Environment,
               defer_to: list[ast.AST] | None = None) -> None:
        pending: list[ast.AST] = []
        args = getattr(node, "args", None)
        if isinstance(args, ast.arguments):
            for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs,
                        *(item for item in (args.vararg, args.kwarg) if item is not None)):
                env[(scope, arg.arg)] = CandidateValue(unknown=True)
                declaration = self.index.scope_declarations.get(scope, {}).get(arg.arg)
                if declaration in ("global", "nonlocal"):
                    self.invalid_names.add((scope, arg.arg))
            owner = self.index.entries[scope]
            if owner.kind == NodeKind.METHOD and owner.parent_symbol is not None:
                positional = (*args.posonlyargs, *args.args)
                decorators = getattr(node, "decorator_list", ())
                static = any(isinstance(item, ast.Name) and item.id == "staticmethod" for item in decorators)
                if positional and not static:
                    receiver = positional[0].arg
                    if receiver == "self":
                        env[(scope, receiver)] = CandidateValue(instances=(owner.parent_symbol,), indirect=True)
                        self.super_receivers[scope] = (owner.parent_symbol, receiver)
                    elif receiver == "cls":
                        env[(scope, receiver)] = CandidateValue((owner.parent_symbol,), indirect=True)
                        self.super_receivers[scope] = (owner.parent_symbol, receiver)
        if isinstance(node, IMPLICIT_SCOPES):
            for child in implicit_parts(node)[1]:
                if isinstance(getattr(child, "ctx", None), ast.Store):
                    self._store(child, CandidateValue(unknown=True), scope, env)
                else:
                    self._expr(child, scope, env, pending)
        elif isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            self._block(node.body, scope, env, pending)
        if defer_to is not None:
            defer_to.extend(pending)
            return
        for deferred in dict.fromkeys(pending):
            owner_id = self.locations.owners.get(deferred)
            if owner_id is not None:
                self._scope(deferred, owner_id, dict(env))

    def analyze(self) -> dict[ast.Call, CandidateValue]:
        assert self.parsed.tree is not None
        try:
            self._scope(self.parsed.tree, self.module_id, {})
        except _ValueWorkExhausted as exhausted:
            self._incomplete_calls(exhausted.env)
        return self.calls
