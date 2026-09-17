"""Collect lexical Python definitions and raw references from a parsed file.

Name resolution is deliberately deferred to the resolver phase. An indexed
reference is evidence of syntax, not a claim about its runtime target.
"""

import ast
from dataclasses import dataclass, field

from codeviz.core import NodeKind, SourceAnchor, stable_node_id

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


class SymbolIndex:
    def __init__(self) -> None:
        self.entries: dict[str, SymbolIndexEntry] = {}
        self.references: list[SymbolReference] = []

    def add_entry(self, entry: SymbolIndexEntry) -> None:
        if entry.id in self.entries:
            raise ValueError(f"duplicate symbol ID: {entry.id}")
        self.entries[entry.id] = entry

    def merge(self, other: "SymbolIndex") -> None:
        for entry in other.entries.values():
            self.add_entry(entry)
        self.references.extend(other.references)

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

    @property
    def scope(self) -> _Scope:
        return self.scopes[-1]

    def _anchor(self, node: ast.AST) -> SourceAnchor:
        start = getattr(node, "lineno", 1)
        return SourceAnchor(self.parsed.relative_path, start, end_line=getattr(node, "end_lineno", start))

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
        owner = scope or self.scope
        key = (owner.name, kind, name)
        if reuse_binding and key in self.bindings:
            previous = self.bindings[key]
            # An import alias followed by another assignment (or import) is a
            # new binding; preserving the old import metadata would be false.
            if previous.metadata == (metadata or {}):
                return previous
        occurrence = self.occurrences.get(key, 0)
        self.occurrences[key] = occurrence + 1
        qualified_name = f"{owner.name}.{name}"
        entry = SymbolIndexEntry(
            id=stable_node_id(
                self.parsed.relative_path, qualified_name, kind, owner.name,
                occurrence_key=f"duplicate:{occurrence}" if occurrence else None,
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
        self.index.references.append(SymbolReference(name, self.scope.name, self._anchor(node), qualifier))

    def _bind_target(self, target: ast.AST, kind: NodeKind = NodeKind.VARIABLE) -> None:
        if isinstance(target, ast.Name):
            self._entry(target.id, kind, target, reuse_binding=True)
        elif isinstance(target, ast.Attribute):
            if isinstance(target.value, ast.Name) and target.value.id == "self" and self.scope.class_name:
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
        for decorator in node.decorator_list:
            self.visit(decorator)
        for expression in (*node.args.defaults, *(item for item in node.args.kw_defaults if item is not None)):
            self.visit(expression)
        for argument in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs):
            if argument.annotation:
                self.visit(argument.annotation)
        for argument in (node.args.vararg, node.args.kwarg):
            if argument is not None and argument.annotation:
                self.visit(argument.annotation)
        if node.returns:
            self.visit(node.returns)

        parent = self.scope
        kind = NodeKind.METHOD if parent.kind == NodeKind.CLASS else NodeKind.FUNCTION
        entry = self._entry(node.name, kind, node)
        self.scopes.append(_Scope(entry.qualified_name, entry.id, kind, parent.class_name, parent.class_id))
        for argument in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs):
            self._entry(argument.arg, NodeKind.PARAMETER, argument, reuse_binding=True)
        for argument in (node.args.vararg, node.args.kwarg):
            if argument is not None:
                self._entry(argument.arg, NodeKind.PARAMETER, argument, reuse_binding=True)
        for statement in node.body:
            self.visit(statement)
        self.scopes.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        for base in node.bases:
            self.visit(base)
        for keyword in node.keywords:
            self.visit(keyword.value)
        entry = self._entry(node.name, NodeKind.CLASS, node)
        self.scopes.append(_Scope(entry.qualified_name, entry.id, NodeKind.CLASS, entry.qualified_name, entry.id))
        for statement in node.body:
            self.visit(statement)
        self.scopes.pop()

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            bound = alias.asname or alias.name.split(".")[0]
            self._entry(bound, NodeKind.VARIABLE, node, reuse_binding=True,
                        metadata={"imported_from": alias.name})

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        prefix = "." * node.level + (node.module or "")
        for alias in node.names:
            if alias.name != "*":
                imported = f"{prefix}.{alias.name}" if node.module else f"{prefix}{alias.name}"
                self._entry(alias.asname or alias.name, NodeKind.VARIABLE, node,
                            reuse_binding=True, metadata={"imported_from": imported})

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
        self._bind_target(node.target)

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
