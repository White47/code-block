"""Shared structural locations and evaluation boundaries for implicit scopes."""

import ast
from collections.abc import Iterator
from typing import TYPE_CHECKING

from codeviz.core import NodeKind

if TYPE_CHECKING:
    from .symbols import SymbolIndex

ImplicitScope = ast.Lambda | ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp
IMPLICIT_SCOPES = (ast.Lambda, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)


def structural_locations(tree: ast.AST) -> dict[ast.AST, str]:
    """Field/index paths distinguish same-line occurrences without using lines."""
    locations: dict[ast.AST, str] = {}

    def walk(node: ast.AST, path: str) -> None:
        locations[node] = path
        for field, value in ast.iter_fields(node):
            if isinstance(value, ast.AST):
                walk(value, f"{path}.{field}")
            elif isinstance(value, list):
                for index, child in enumerate(value):
                    if isinstance(child, ast.AST):
                        walk(child, f"{path}.{field}[{index}]")

    walk(tree, "module")
    return locations


def implicit_parts(node: ImplicitScope) -> tuple[tuple[ast.AST, ...], tuple[ast.AST, ...]]:
    """Return creation expressions and expressions evaluated in the new scope."""
    if isinstance(node, ast.Lambda):
        return (tuple((*node.args.defaults,
                       *(value for value in node.args.kw_defaults if value is not None))),
                (node.body,))
    inner: list[ast.AST] = []
    for index, generator in enumerate(node.generators):
        if index:
            inner.append(generator.iter)
        inner.append(generator.target)
        inner.extend(generator.ifs)
    if isinstance(node, ast.DictComp):
        inner.extend((node.key, node.value))
    else:
        inner.append(node.elt)
    return (node.generators[0].iter,), tuple(inner)


def definition_expressions(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> tuple[ast.expr, ...]:
    """Expressions evaluated in the parent when a named definition is created."""
    expressions: list[ast.expr] = list(node.decorator_list)
    if isinstance(node, ast.ClassDef):
        expressions.extend(node.bases)
        expressions.extend(keyword.value for keyword in node.keywords)
    else:
        expressions.extend(node.args.defaults)
        expressions.extend(value for value in node.args.kw_defaults if value is not None)
        for argument in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs,
                         *(arg for arg in (node.args.vararg, node.args.kwarg) if arg is not None)):
            if argument.annotation is not None:
                expressions.append(argument.annotation)
        if node.returns is not None:
            expressions.append(node.returns)
    return tuple(expressions)


class ScopeLocations:
    """Locate indexed owners consistently for symbol, call and data visitors."""

    def __init__(self, tree: ast.AST, index: "SymbolIndex", file_path: str) -> None:
        self.paths = structural_locations(tree)
        implicit = {entry.metadata["structural_key"]: entry.id
                    for entry in index.entries.values()
                    if entry.file_path == file_path and entry.kind == NodeKind.LEXICAL_SCOPE}
        named = {(entry.source_anchor.start_line, entry.source_anchor.start_column,
                  entry.name): entry.id
                 for entry in index.entries.values()
                 if entry.file_path == file_path
                 and entry.kind in (NodeKind.FUNCTION, NodeKind.METHOD, NodeKind.CLASS)}
        self.owners: dict[ast.AST, str] = {}
        for node, path in self.paths.items():
            owner = None
            if isinstance(node, IMPLICIT_SCOPES):
                owner = implicit.get(path)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                owner = named.get((node.lineno, node.col_offset, node.name))
            if owner is not None:
                self.owners[node] = owner

    def expression_nodes(self, node: ast.AST, scope_id: str,
                         *, immediate: bool = True) -> Iterator[tuple[ast.AST, str]]:
        """Walk reads with lexical ownership, optionally excluding deferred bodies."""
        yield node, scope_id
        if isinstance(node, IMPLICIT_SCOPES):
            outer, inner = implicit_parts(node)
            for child in outer:
                yield from self.expression_nodes(child, scope_id, immediate=immediate)
            if immediate and isinstance(node, (ast.Lambda, ast.GeneratorExp)):
                return
            owner = self.owners[node]
            for child in inner:
                yield from self.expression_nodes(child, owner, immediate=immediate)
        else:
            for child in ast.iter_child_nodes(node):
                yield from self.expression_nodes(child, scope_id, immediate=immediate)


class ImplicitScopeVisitor(ast.NodeVisitor):
    """Common traversal for consumers of the symbol collector's implicit owners."""

    scope_id: str
    locations: ScopeLocations

    def _visit_implicit_body(self, node: ImplicitScope, body: tuple[ast.AST, ...]) -> None:
        previous = self.scope_id
        self.scope_id = self.locations.owners[node]
        try:
            for child in body:
                self.visit(child)
        finally:
            self.scope_id = previous

    def _visit_implicit(self, node: ImplicitScope) -> None:
        outer, inner = implicit_parts(node)
        for child in outer:
            self.visit(child)
        self._visit_implicit_body(node, inner)

    visit_Lambda = _visit_implicit
    visit_ListComp = _visit_implicit
    visit_SetComp = _visit_implicit
    visit_DictComp = _visit_implicit
    visit_GeneratorExp = _visit_implicit
