"""Collect lexical Python definitions and raw references from a parsed file.

Name resolution is deliberately deferred to the resolver phase. An indexed
reference is evidence of syntax, not a claim about its runtime target.
"""

import ast
from dataclasses import dataclass, field

from codeviz.core import Confidence, NodeKind, SourceAnchor, stable_node_id

from .lexical_scopes import (
    ImplicitScope,
    definition_expressions,
    implicit_parts,
    structural_locations,
)
from .python import ParsedFile


@dataclass(frozen=True, slots=True)
class SymbolIndexEntry:
    id: str
    name: str
    qualified_name: str
    kind: NodeKind
    scope: str
    file_path: str
    source_anchor: SourceAnchor
    parent_symbol: str | None
    definition: bool = True
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SymbolReference:
    name: str
    scope: str
    source_anchor: SourceAnchor
    qualifier: str | None = None
    resolved_definition_id: str | None = None
    scope_symbol_id: str | None = None
    possible_target_ids: tuple[str, ...] = ()
    confidence: Confidence = Confidence.UNRESOLVED
    unresolved_symbol_id: str | None = None
    reason: str | None = None


class SymbolIndex:
    def __init__(self) -> None:
        self.entries: dict[str, SymbolIndexEntry] = {}
        self.references: list[SymbolReference] = []
        self.scope_declarations: dict[str, dict[str, str]] = {}

    def add_entry(self, entry: SymbolIndexEntry) -> None:
        if entry.id in self.entries:
            raise ValueError(f"duplicate symbol ID: {entry.id}")
        self.entries[entry.id] = entry

    def merge(self, other: "SymbolIndex") -> None:
        for entry in other.entries.values():
            self.add_entry(entry)
        self.references.extend(other.references)
        self.scope_declarations.update(other.scope_declarations)

    def find_definitions(self, name: str) -> tuple[SymbolIndexEntry, ...]:
        return tuple(entry for entry in self.entries.values() if entry.definition and entry.name == name)

    def find_references(self, name: str) -> tuple[SymbolReference, ...]:
        return tuple(reference for reference in self.references if reference.name == name)


@dataclass(frozen=True, slots=True)
class _Scope:
    name: str
    owner_id: str
    kind: NodeKind
    class_name: str | None = None
    class_id: str | None = None


class _SymbolCollector(ast.NodeVisitor):
    def __init__(self, parsed: ParsedFile, index: SymbolIndex, module_id: str) -> None:
        self.parsed = parsed
        self.index = index
        self.scopes = [_Scope(parsed.module_name, module_id, NodeKind.MODULE)]
        self.bindings: dict[tuple[str, NodeKind, str], SymbolIndexEntry] = {}
        self.occurrences: dict[tuple[str, NodeKind, str], int] = {}
        assert parsed.tree is not None
        self.paths = structural_locations(parsed.tree)
        self.invalid_iterable_walruses = {
            child for expression in ast.walk(parsed.tree)
            if isinstance(expression, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp))
            for generator in expression.generators for child in ast.walk(generator.iter)
            if isinstance(child, ast.NamedExpr)}
        self.local_names: dict[str, set[str]] = {}
        self.comprehension_targets: dict[str, set[str]] = {}
        self._prepare_scope(parsed.tree)

    @property
    def scope(self) -> _Scope:
        return self.scopes[-1]

    def _anchor(self, node: ast.AST) -> SourceAnchor:
        start = getattr(node, "lineno", 1)
        return SourceAnchor(self.parsed.relative_path, start,
                            start_column=getattr(node, "col_offset", None),
                            end_line=getattr(node, "end_lineno", start),
                            end_column=getattr(node, "end_col_offset", None))

    def _binding_owner(self, name: str, start: _Scope | None = None) -> _Scope:
        owner = start or self.scope
        declaration = self.index.scope_declarations.get(owner.owner_id, {}).get(name)
        if declaration == "global":
            return self.scopes[0]
        if declaration == "nonlocal":
            position = self.scopes.index(owner)
            enclosing = [frame for frame in reversed(self.scopes[:position])
                         if frame.kind in (NodeKind.FUNCTION, NodeKind.METHOD, NodeKind.LEXICAL_SCOPE)
                         and frame.owner_id not in self.comprehension_targets]
            for frame in enclosing:
                if name in self.local_names.get(frame.owner_id, set()):
                    return frame
        return owner

    def _entry(
        self,
        name: str,
        kind: NodeKind,
        node: ast.AST,
        *,
        scope: _Scope | None = None,
        reuse_binding: bool = False,
        metadata: dict[str, str] | None = None,
    ) -> SymbolIndexEntry:
        owner = scope or self._binding_owner(name)
        key = (owner.owner_id, kind, name)
        if reuse_binding and kind == NodeKind.VARIABLE and not metadata:
            parameter = self.bindings.get((owner.owner_id, NodeKind.PARAMETER, name))
            if parameter is not None:
                return parameter
        if reuse_binding and key in self.bindings:
            previous = self.bindings[key]
            # An import alias followed by another assignment (or import) is a
            # new binding; preserving the old import metadata would be false.
            if previous.metadata == (metadata or {}):
                return previous
        occurrence = self.occurrences.get(key, 0)
        self.occurrences[key] = occurrence + 1
        qualified_name = f"{owner.name}.{name}"
        occurrence_key = (
            f"parent:{owner.owner_id}:duplicate:{occurrence}" if occurrence else f"parent:{owner.owner_id}"
        ) if owner.kind != NodeKind.MODULE else (f"duplicate:{occurrence}" if occurrence else None)
        entry = SymbolIndexEntry(
            id=stable_node_id(
                self.parsed.relative_path, qualified_name, kind, owner.name,
                occurrence_key=occurrence_key,
            ),
            name=name,
            qualified_name=qualified_name,
            kind=kind,
            scope=owner.name,
            file_path=self.parsed.relative_path,
            source_anchor=self._anchor(node),
            parent_symbol=owner.owner_id,
            metadata=metadata or {},
        )
        self.index.add_entry(entry)
        if reuse_binding:
            self.bindings[key] = entry
        return entry

    def _reference(self, name: str, node: ast.AST, qualifier: str | None = None) -> None:
        self.index.references.append(SymbolReference(
            name, self.scope.name, self._anchor(node), qualifier,
            scope_symbol_id=self.scope.owner_id,
        ))

    def visit_Global(self, node: ast.Global) -> None:
        declarations = self.index.scope_declarations.setdefault(self.scope.owner_id, {})
        for name in node.names:
            if declarations.get(name) != "invalid_declaration":
                declarations[name] = "global"

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        declarations = self.index.scope_declarations.setdefault(self.scope.owner_id, {})
        for name in node.names:
            if declarations.get(name) != "invalid_declaration":
                declarations[name] = "nonlocal"

    def _bind_target(self, target: ast.AST, kind: NodeKind = NodeKind.VARIABLE) -> None:
        if isinstance(target, ast.Name):
            self._entry(target.id, kind, target, reuse_binding=True)
        elif isinstance(target, ast.Attribute):
            if isinstance(target.value, ast.Name) and target.value.id == "self" and self.scope.class_name:
                assert self.scope.class_id is not None
                class_scope = _Scope(self.scope.class_name, self.scope.class_id, NodeKind.CLASS)
                self._entry(target.attr, NodeKind.ATTRIBUTE, target, scope=class_scope, reuse_binding=True)
            self.visit(target.value)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for element in target.elts:
                self._bind_target(element, kind)
        elif isinstance(target, ast.Starred):
            self._bind_target(target.value, kind)
        else:
            self.visit(target)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        for expression in definition_expressions(node):
            self.visit(expression)

        parent = self.scope
        kind = NodeKind.METHOD if parent.kind == NodeKind.CLASS else NodeKind.FUNCTION
        entry = self._entry(node.name, kind, node)
        self.scopes.append(_Scope(entry.qualified_name, entry.id, kind, parent.class_name, parent.class_id))
        self._prepare_scope(node)
        for argument in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs):
            self._entry(argument.arg, NodeKind.PARAMETER, argument,
                        scope=self.scope, reuse_binding=True)
        for optional_argument in (node.args.vararg, node.args.kwarg):
            if optional_argument is not None:
                self._entry(optional_argument.arg, NodeKind.PARAMETER, optional_argument,
                            scope=self.scope, reuse_binding=True)
        for statement in node.body:
            self.visit(statement)
        self.scopes.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        for expression in definition_expressions(node):
            self.visit(expression)
        entry = self._entry(node.name, NodeKind.CLASS, node)
        self.scopes.append(_Scope(entry.qualified_name, entry.id, NodeKind.CLASS, entry.qualified_name, entry.id))
        self._prepare_scope(node)
        for statement in node.body:
            self.visit(statement)
        self.scopes.pop()

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            bound = alias.asname or alias.name.split(".")[0]
            self._entry(bound, NodeKind.VARIABLE, node, reuse_binding=True,
                        metadata={"imported_from": alias.name, "import_form": "import",
                                  "binds_root": "true" if alias.asname is None else "false"})

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        prefix = "." * node.level + (node.module or "")
        for alias in node.names:
            if alias.name != "*":
                imported = f"{prefix}.{alias.name}" if node.module else f"{prefix}{alias.name}"
                self._entry(alias.asname or alias.name, NodeKind.VARIABLE, node,
                            reuse_binding=True, metadata={"imported_from": imported,
                                                          "import_form": "from"})

    def visit_Assign(self, node: ast.Assign) -> None:
        self.visit(node.value)
        for target in node.targets:
            self._bind_target(target)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self.visit(node.annotation)
        if node.value is not None:
            self.visit(node.value)
        self._bind_target(node.target)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self.visit(node.target)
        self.visit(node.value)
        self._bind_target(node.target)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        self.visit(node.value)
        if not isinstance(node.target, ast.Name):
            return
        owner = next(frame for frame in reversed(self.scopes)
                     if frame.owner_id not in self.comprehension_targets)
        name = node.target.id
        in_comprehension = self.scope.owner_id in self.comprehension_targets
        invalid = node in self.invalid_iterable_walruses or in_comprehension and (owner.kind == NodeKind.CLASS or any(
            name in self.comprehension_targets.get(frame.owner_id, set())
            for frame in self.scopes[self.scopes.index(owner) + 1:]))
        if invalid:
            self.index.scope_declarations.setdefault(self.scope.owner_id, {})[name] = "invalid_walrus"
            self.index.scope_declarations.setdefault(owner.owner_id, {})[name] = "invalid_walrus"
            return
        self._entry(name, NodeKind.VARIABLE, node.target,
                    scope=self._binding_owner(name, owner), reuse_binding=True)

    def _prepare_scope(self, node: ast.AST) -> None:
        """Predeclare locals so nonlocal ownership does not depend on visit order."""
        names: set[str] = set()
        declarations: dict[str, str] = {}
        parameters: set[str] = set()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            parameters = {argument.arg for argument in (
                *node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs,
                *(arg for arg in (node.args.vararg, node.args.kwarg) if arg is not None))}
        invalid: dict[str, SourceAnchor] = {}

        def scan(child: ast.AST) -> None:
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(child.name)
                for creation_expression in definition_expressions(child):
                    scan(creation_expression)
                return
            if isinstance(child, ast.Lambda):
                for expression in implicit_parts(child)[0]:
                    scan(expression)
                return
            if isinstance(child, (ast.Global, ast.Nonlocal)):
                declaration = "global" if isinstance(child, ast.Global) else "nonlocal"
                for name in child.names:
                    previous = declarations.get(name)
                    if (name in parameters or previous == "invalid_declaration"
                            or previous is not None and previous != declaration):
                        declarations[name] = "invalid_declaration"
                        invalid.setdefault(name, self._anchor(child))
                    else:
                        declarations[name] = declaration
            if isinstance(child, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
                # Only named expressions escape a comprehension, not its targets.
                for nested in ast.iter_child_nodes(child):
                    scan_comprehension(nested)
                return
            if isinstance(child, ast.Name) and isinstance(child.ctx, (ast.Store, ast.Del)):
                names.add(child.id)
            elif isinstance(child, ast.arg):
                names.add(child.arg)
            elif isinstance(child, (ast.MatchAs, ast.MatchStar)) and child.name:
                names.add(child.name)
            elif isinstance(child, ast.MatchMapping) and child.rest:
                names.add(child.rest)
            elif isinstance(child, ast.ExceptHandler) and child.name:
                names.add(child.name)
            elif isinstance(child, (ast.Import, ast.ImportFrom)):
                names.update(alias.asname or (alias.name.split(".")[0]
                             if isinstance(child, ast.Import) else alias.name)
                             for alias in child.names if alias.name != "*")
            for nested in ast.iter_child_nodes(child):
                scan(nested)

        def scan_comprehension(child: ast.AST) -> None:
            if isinstance(child, ast.Lambda):
                for default in implicit_parts(child)[0]:
                    scan_comprehension(default)
                return
            if isinstance(child, ast.NamedExpr) and isinstance(child.target, ast.Name):
                names.add(child.target.id)
            for nested in ast.iter_child_nodes(child):
                scan_comprehension(nested)

        names.update(parameters)
        body = (node.body,) if isinstance(node, ast.Lambda) else getattr(node, "body", ())
        for statement in body:
            scan(statement)
        self.local_names[self.scope.owner_id] = names - {
            name for name, declaration in declarations.items()
            if declaration in ("global", "nonlocal")}
        self.index.scope_declarations[self.scope.owner_id] = declarations
        owner = self.index.entries[self.scope.owner_id]
        for name, anchor in invalid.items():
            owner.metadata[f"invalid_declaration:{name}"] = (
                f"{anchor.start_line}:{anchor.start_column or 0}")
        if invalid:
            owner.metadata["partial"] = "invalid scope declarations"

    def _visit_implicit(self, node: ImplicitScope) -> None:
        outer, inner = implicit_parts(node)
        for expression in outer:
            self.visit(expression)
        parent = self.scope
        path = self.paths[node]
        name = f"<{type(node).__name__}:{path}>"
        qualified_name = f"{parent.name}.{name}"
        entry = SymbolIndexEntry(
            stable_node_id(self.parsed.relative_path, qualified_name, NodeKind.LEXICAL_SCOPE,
                           parent.name, occurrence_key=f"parent:{parent.owner_id}:{path}"),
            name, qualified_name, NodeKind.LEXICAL_SCOPE, parent.name,
            self.parsed.relative_path, self._anchor(node), parent.owner_id,
            definition=False, metadata={"structural_key": path, "scope_type": type(node).__name__,
                                        "execution": "deferred" if isinstance(node, (ast.Lambda, ast.GeneratorExp))
                                        else "eager"},
        )
        self.index.add_entry(entry)
        self.scopes.append(_Scope(entry.qualified_name, entry.id, NodeKind.LEXICAL_SCOPE,
                                  parent.class_name, parent.class_id))
        if isinstance(node, ast.Lambda):
            self._prepare_scope(node)
            for argument in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs,
                             *(arg for arg in (node.args.vararg, node.args.kwarg) if arg is not None)):
                self._entry(argument.arg, NodeKind.PARAMETER, argument,
                            scope=self.scope, reuse_binding=True)
        else:
            self.comprehension_targets[entry.id] = {
                target.id for generator in node.generators for target in ast.walk(generator.target)
                if isinstance(target, ast.Name) and isinstance(target.ctx, ast.Store)}
            self.local_names[entry.id] = self.comprehension_targets[entry.id]
        for expression in inner:
            if isinstance(expression, ast.Name) and isinstance(expression.ctx, ast.Store):
                self._bind_target(expression)
            elif isinstance(expression, (ast.Tuple, ast.List, ast.Starred)) and isinstance(
                    getattr(expression, "ctx", None), ast.Store):
                self._bind_target(expression)
            else:
                self.visit(expression)
        self.scopes.pop()

    visit_Lambda = _visit_implicit
    visit_ListComp = _visit_implicit
    visit_SetComp = _visit_implicit
    visit_DictComp = _visit_implicit
    visit_GeneratorExp = _visit_implicit

    def visit_MatchAs(self, node: ast.MatchAs) -> None:
        if node.pattern is not None:
            self.visit(node.pattern)
        if node.name and node.name != "_":
            self._entry(node.name, NodeKind.VARIABLE, node, reuse_binding=True)

    def visit_MatchStar(self, node: ast.MatchStar) -> None:
        if node.name and node.name != "_":
            self._entry(node.name, NodeKind.VARIABLE, node, reuse_binding=True)

    def visit_MatchMapping(self, node: ast.MatchMapping) -> None:
        for key in node.keys:
            self.visit(key)
        for pattern in node.patterns:
            self.visit(pattern)
        if node.rest and node.rest != "_":
            self._entry(node.rest, NodeKind.VARIABLE, node, reuse_binding=True)

    def visit_For(self, node: ast.For | ast.AsyncFor) -> None:
        self.visit(node.iter)
        self._bind_target(node.target)
        for statement in (*node.body, *node.orelse):
            self.visit(statement)

    visit_AsyncFor = visit_For

    def visit_With(self, node: ast.With | ast.AsyncWith) -> None:
        for item in node.items:
            self.visit(item.context_expr)
            if item.optional_vars is not None:
                self._bind_target(item.optional_vars)
        for statement in node.body:
            self.visit(statement)

    visit_AsyncWith = visit_With

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.type is not None:
            self.visit(node.type)
        if node.name:
            self._entry(node.name, NodeKind.VARIABLE, node, reuse_binding=True)
        for statement in node.body:
            self.visit(statement)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load):
            self._reference(node.id, node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if isinstance(node.ctx, ast.Load):
            qualifier = ast.unparse(node.value)
            self._reference(node.attr, node, qualifier)
        self.visit(node.value)


def index_parsed_file(parsed: ParsedFile) -> SymbolIndex:
    """Index lexical symbols; leave every reference unresolved for Phase 3."""
    index = SymbolIndex()
    if parsed.tree is None:
        return index
    file_anchor = SourceAnchor(parsed.relative_path, 1, end_line=parsed.line_count)
    file_id = stable_node_id(parsed.relative_path, parsed.relative_path, NodeKind.FILE, "project")
    index.add_entry(SymbolIndexEntry(file_id, parsed.relative_path.rsplit("/", 1)[-1],
                                     parsed.relative_path, NodeKind.FILE, "project",
                                     parsed.relative_path, file_anchor, None))
    module_id = stable_node_id(parsed.relative_path, parsed.module_name, NodeKind.MODULE, "project")
    index.add_entry(SymbolIndexEntry(module_id, parsed.module_name, parsed.module_name,
                                     NodeKind.MODULE, "project", parsed.relative_path,
                                     file_anchor, file_id))
    _SymbolCollector(parsed, index, module_id).visit(parsed.tree)
    return index
