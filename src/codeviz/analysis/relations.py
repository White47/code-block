"""Explicit import dependencies and base syntax, without importing target code."""

import ast

from codeviz.core import (
    CodeEdge,
    CodeGraph,
    Confidence,
    EdgeType,
    NodeKind,
    NodeOrigin,
    SourceAnchor,
)

from .lexical_scopes import ImplicitScopeVisitor, ScopeLocations, definition_expressions
from .python import AnalysisIssue, ParsedFile
from .resolution import ImportTargetFact, ImportTargetQuery, ResolutionResult
from .symbols import SymbolIndexEntry, SymbolReference


class RelationTargets:
    """Resolve only indexed modules/import aliases; external code stays opaque."""

    def __init__(self, files: tuple[ParsedFile, ...], resolution: ResolutionResult,
                 graph: CodeGraph) -> None:
        self.index = resolution.symbol_index
        self.query = ImportTargetQuery(files, resolution)
        self.catalog = self.query.catalog
        self.graph = graph
        self.children = self.query.children

    def import_fact(self, entry: SymbolIndexEntry, *, dependency: bool = False,
                    anchor: SourceAnchor | None = None) -> ImportTargetFact:
        result = self.query.targets(entry, dependency=dependency, anchor=anchor)
        for external in self.query.external_symbols:
            if external.id not in self.graph.nodes:
                self.graph.add_node(external)
        return result

    def import_binding(self, entry: SymbolIndexEntry) -> tuple[str, ...]:
        return self.import_fact(entry).ids

    def reference_fact(self, reference: SymbolReference) -> ImportTargetFact:
        """Retain partial facts through the shared Name/Attribute query."""
        return self.query.reference_targets(reference)


class _RelationVisitor(ImplicitScopeVisitor):
    def __init__(self, parsed: ParsedFile, resolution: ResolutionResult,
                 targets: RelationTargets, issues: list[AnalysisIssue]) -> None:
        assert parsed.tree is not None
        self.parsed = parsed
        self.index = resolution.symbol_index
        self.graph = targets.graph
        self.targets = targets
        self.issues = issues
        self.locations = ScopeLocations(parsed.tree, self.index, parsed.relative_path)
        self.scope_id = targets.catalog.by_file[parsed.relative_path].id
        self.references = {
            (ref.scope_symbol_id, ref.source_anchor.start_line,
             ref.source_anchor.start_column, ref.name, ref.qualifier): ref
            for ref in self.index.references if ref.source_anchor.file_path == parsed.relative_path
        }

    def _anchor(self, node: ast.AST) -> SourceAnchor:
        return SourceAnchor(self.parsed.relative_path, getattr(node, "lineno", 1),
                            start_column=getattr(node, "col_offset", None),
                            end_line=getattr(node, "end_lineno", None),
                            end_column=getattr(node, "end_col_offset", None))

    def _imports(self, node: ast.Import | ast.ImportFrom) -> None:
        for alias in node.names:
            if alias.name == "*":
                continue
            name = alias.asname or (alias.name.split(".")[0]
                                   if isinstance(node, ast.Import) else alias.name)
            raw = alias.name if isinstance(node, ast.Import) else (
                "." * node.level + (node.module + "." if node.module else "") + alias.name)
            anchor = self._anchor(alias)
            binding_owner = (self.targets.query.binding_owner(
                name, self.scope_id, self.parsed.relative_path).owner_id or self.scope_id)
            matches = [entry for entry in self.index.entries.values()
                       if entry.file_path == self.parsed.relative_path and entry.name == name
                       and entry.metadata.get("imported_from") == raw
                       and entry.parent_symbol == binding_owner]
            matches.sort(key=lambda entry: entry.source_anchor.start_line != node.lineno)
            entry = matches[0] if matches else None
            fact = self.targets.import_fact(entry, dependency=True, anchor=anchor) if entry else None
            target_ids = fact.ids if fact else ()
            truncated = len(target_ids) > 16 or bool(fact and fact.truncated)
            partial = bool(fact and fact.issue_code)
            if len(target_ids) > 16:
                self.issues.append(AnalysisIssue(
                    "RELATION_CANDIDATE_BUDGET", "import relation exceeds 16 targets; omitted targets unknown",
                    self.parsed.relative_path, anchor,
                ))
            if not target_ids:
                self.issues.append(AnalysisIssue(
                    fact.issue_code if fact and fact.issue_code else "RELATION_IMPORT_UNRESOLVED",
                    fact.reason if fact and fact.reason else "import dependency has no bounded indexed target",
                    self.parsed.relative_path, anchor,
                ))
            elif fact and fact.issue_code:
                self.issues.append(AnalysisIssue(
                    fact.issue_code, fact.reason or "some import alternatives are unresolved",
                    self.parsed.relative_path, anchor,
                ))
            for target in target_ids[:16]:
                exact = (not truncated and len(target_ids) == 1 and fact is not None
                         and fact.confidence == Confidence.EXACT
                         and self.graph.nodes[target].origin == NodeOrigin.SOURCE)
                self.graph.add_edge(CodeEdge(
                    self.scope_id, target, EdgeType.IMPORTS,
                    Confidence.EXACT if exact else Confidence.PROBABLE, source_anchor=anchor,
                    metadata={"alias": name, "raw_path": raw,
                              "binding_id": entry.id if entry else None,
                              "truncated": truncated, "unknown": truncated or partial},
                ))

    visit_Import = _imports
    visit_ImportFrom = _imports

    def _definition(self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> None:
        owner_id = self.locations.owners.get(node)
        if owner_id is None:
            return
        if isinstance(node, ast.ClassDef):
            for base in node.bases:
                name = base.id if isinstance(base, ast.Name) else (
                    base.attr if isinstance(base, ast.Attribute) else "")
                qualifier = ast.unparse(base.value) if isinstance(base, ast.Attribute) else None
                ref = self.references.get((self.scope_id, base.lineno, base.col_offset,
                                           name, qualifier))
                ids = ((ref.resolved_definition_id,) if ref and ref.resolved_definition_id else
                       ref.possible_target_ids if ref else ())
                valid = tuple(target for target in ids if self.graph.nodes[target].kind
                              in (NodeKind.CLASS, NodeKind.EXTERNAL_SYMBOL))
                truncated = len(valid) > 16
                fact = self.targets.reference_fact(ref) if ref is not None else None
                truncated = truncated or bool(fact and fact.truncated)
                partial = bool(fact and fact.issue_code)
                if partial and fact is not None:
                    self.graph.nodes[owner_id].metadata["inheritance_partial"] = True
                    self.issues.append(AnalysisIssue(
                        fact.issue_code or "DYNAMIC_BASE_UNRESOLVED",
                        fact.reason or "some base alternatives are unresolved",
                        self.parsed.relative_path, self._anchor(base),
                    ))
                if len(valid) > 16:
                    self.graph.nodes[owner_id].metadata["inheritance_partial"] = True
                    self.issues.append(AnalysisIssue(
                        "RELATION_CANDIDATE_BUDGET", "inheritance relation exceeds 16 targets; omitted bases unknown",
                        self.parsed.relative_path, self._anchor(base),
                    ))
                if not valid or len(valid) != len(ids):
                    self.graph.nodes[owner_id].metadata["inheritance_partial"] = True
                    self.issues.append(AnalysisIssue(
                        "DYNAMIC_BASE_UNRESOLVED", "base expression is not a bounded class definition",
                        self.parsed.relative_path, self._anchor(base),
                    ))
                for target in valid[:16]:
                    exact = (not truncated and not partial and len(ids) == 1 and ref is not None
                             and ref.confidence == Confidence.EXACT)
                    self.graph.add_edge(CodeEdge(
                        owner_id, target, EdgeType.INHERITS,
                        Confidence.EXACT if exact else Confidence.PROBABLE,
                        source_anchor=self._anchor(base),
                        metadata={"syntax_only": True, "truncated": truncated,
                                  "unknown": truncated or partial},
                    ))
        for expression in definition_expressions(node):
            self.visit(expression)
        previous = self.scope_id
        self.scope_id = owner_id
        try:
            for statement in node.body:
                self.visit(statement)
        finally:
            self.scope_id = previous

    visit_FunctionDef = _definition
    visit_AsyncFunctionDef = _definition
    visit_ClassDef = _definition


def add_relations(files: tuple[ParsedFile, ...], resolution: ResolutionResult,
                  graph: CodeGraph, issues: list[AnalysisIssue]) -> RelationTargets:
    targets = RelationTargets(files, resolution, graph)
    for parsed in files:
        if parsed.tree is not None and parsed.relative_path in targets.catalog.by_file:
            _RelationVisitor(parsed, resolution, targets, issues).visit(parsed.tree)
    return targets
