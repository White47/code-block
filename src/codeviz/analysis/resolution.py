"""Conservative lexical and project import resolution over indexed syntax facts."""

import ast
import builtins
from collections import defaultdict
from dataclasses import dataclass, replace

from codeviz.core import (
    CodeNode,
    Confidence,
    NodeKind,
    NodeOrigin,
    SourceAnchor,
    stable_node_id,
)

from .imports import ModuleCatalog
from .python import AnalysisIssue, ParsedFile
from .symbols import SymbolIndex, SymbolIndexEntry, SymbolReference


@dataclass(frozen=True, slots=True)
class ResolutionResult:
    symbol_index: SymbolIndex
    external_symbols: tuple[CodeNode, ...]
    unresolved_symbols: tuple[CodeNode, ...]
    issues: tuple[AnalysisIssue, ...]


@dataclass(frozen=True, slots=True)
class _Targets:
    ids: tuple[str, ...] = ()
    confidence: Confidence = Confidence.UNRESOLVED
    reason: str | None = None
    issue_code: str | None = None


class _Resolver:
    def __init__(self, files: tuple[ParsedFile, ...], index: SymbolIndex) -> None:
        self.index = index
        self.catalog = ModuleCatalog(files, index)
        self.children: dict[tuple[str, str], list[SymbolIndexEntry]] = defaultdict(list)
        for entry in index.entries.values():
            if entry.parent_symbol is not None:
                self.children[(entry.parent_symbol, entry.name)].append(entry)
        self.external: dict[str, CodeNode] = {}
        self.unresolved: dict[str, CodeNode] = {}
        self.issues: list[AnalysisIssue] = []
        self.unresolved_counts: dict[tuple[str, str, str, str | None], int] = defaultdict(int)
        for parsed in files:
            if parsed.tree is None:
                continue
            for node in ast.walk(parsed.tree):
                if isinstance(node, ast.ImportFrom) and any(alias.name == "*" for alias in node.names):
                    anchor = SourceAnchor(parsed.relative_path, node.lineno)
                    self.issues.append(AnalysisIssue(
                        "STAR_IMPORT_UNRESOLVED", "wildcard import bindings are not inferred",
                        parsed.relative_path, anchor,
                    ))

    def _external(self, qualified_name: str, anchor: SourceAnchor) -> str:
        node_id = stable_node_id(anchor.file_path, qualified_name, NodeKind.EXTERNAL_SYMBOL, "external")
        if node_id not in self.external:
            self.external[node_id] = CodeNode(
                id=node_id, kind=NodeKind.EXTERNAL_SYMBOL,
                name=qualified_name.rsplit(".", 1)[-1], qualified_name=qualified_name,
                scope="external", language="python", source_anchors=(anchor,),
                origin=NodeOrigin.EXTERNAL, confidence=Confidence.PROBABLE,
                metadata={"reason": "external symbol is not inspected"},
            )
        return node_id

    def _unresolved(self, reference: SymbolReference, reason: str) -> str:
        key = (reference.source_anchor.file_path, reference.scope, reference.name, reference.qualifier)
        occurrence = self.unresolved_counts[key]
        self.unresolved_counts[key] += 1
        qualified_name = f"{reference.scope}.{reference.qualifier + '.' if reference.qualifier else ''}{reference.name}"
        node_id = stable_node_id(reference.source_anchor.file_path, qualified_name,
                                 NodeKind.UNRESOLVED_SYMBOL, reference.scope,
                                 occurrence_key=f"reference:{occurrence}")
        self.unresolved[node_id] = CodeNode(
            id=node_id, kind=NodeKind.UNRESOLVED_SYMBOL, name=reference.name,
            qualified_name=qualified_name, scope=reference.scope, language="python",
            source_anchors=(reference.source_anchor,), origin=NodeOrigin.SYNTHETIC,
            confidence=Confidence.UNRESOLVED, metadata={"reason": reason},
        )
        return node_id

    def _module_id(self, file_path: str) -> str | None:
        module = self.catalog.by_file.get(file_path)
        return module.id if module is not None else None

    def _nearest_function(self, scope_id: str | None) -> str | None:
        while scope_id is not None:
            entry = self.index.entries.get(scope_id)
            if entry is None:
                return None
            if entry.kind in (NodeKind.FUNCTION, NodeKind.METHOD):
                return scope_id
            scope_id = entry.parent_symbol
        return None

    def _entry_targets(self, entries: list[SymbolIndexEntry] | tuple[SymbolIndexEntry, ...],
                       anchor: SourceAnchor, visited: frozenset[str] = frozenset()) -> _Targets:
        if not entries:
            return _Targets(reason="no matching definition")
        results = [self._entry_target(entry, anchor, visited) for entry in entries]
        ids = tuple(dict.fromkeys(target for result in results for target in result.ids))
        if not ids:
            cycle = any(result.issue_code == "IMPORT_CYCLE" for result in results)
            return _Targets(reason="circular import alias" if cycle else results[0].reason,
                            issue_code="IMPORT_CYCLE" if cycle else results[0].issue_code)
        if len(entries) == 1 and len(ids) == 1 and results[0].confidence == Confidence.EXACT:
            return _Targets(ids, Confidence.EXACT)
        return _Targets(ids, Confidence.PROBABLE, "multiple or unverified targets")

    def _entry_target(self, entry: SymbolIndexEntry, anchor: SourceAnchor,
                      visited: frozenset[str]) -> _Targets:
        if "imported_from" in entry.metadata:
            return self._import_target(entry, anchor, visited)
        return _Targets((entry.id,), Confidence.EXACT)

    def _import_target(self, entry: SymbolIndexEntry, anchor: SourceAnchor,
                       visited: frozenset[str]) -> _Targets:
        if entry.id in visited:
            return _Targets(reason="circular import alias", issue_code="IMPORT_CYCLE")
        path = self.catalog.absolute_import(entry)
        if path is None:
            return _Targets(reason="invalid relative import", issue_code="IMPORT_UNRESOLVED")
        visited = visited | {entry.id}
        candidates: list[SymbolIndexEntry] = list(self.catalog.candidates(path))
        parent_path, _, member = path.rpartition(".")
        if parent_path:
            for module in self.catalog.candidates(parent_path):
                candidates.extend(self.children.get((module.id, member), ()))
        if candidates:
            return self._entry_targets(candidates, anchor, visited)
        if self.catalog.is_project_path(path):
            return _Targets(reason=f"project import {path!r} has no indexed target",
                            issue_code="IMPORT_UNRESOLVED")
        external_id = self._external(path, anchor)
        return _Targets((external_id,), Confidence.PROBABLE, "external import not inspected")

    def _name(self, name: str, scope_id: str | None, file_path: str,
              anchor: SourceAnchor) -> _Targets:
        current = scope_id or self._module_id(file_path)
        seen_function = False
        while current is not None:
            owner = self.index.entries.get(current)
            if owner is None or owner.kind == NodeKind.FILE:
                break
            declaration = self.index.scope_declarations.get(current, {}).get(name)
            if declaration == "global":
                current = self._module_id(file_path)
                seen_function = False
                if current == owner.id:
                    break
                continue
            if declaration == "nonlocal":
                current = self._nearest_function(owner.parent_symbol)
                seen_function = True
                continue
            if not (seen_function and owner.kind == NodeKind.CLASS):
                candidates = self.children.get((current, name), ())
                if candidates:
                    return self._entry_targets(candidates, anchor)
            if owner.kind in (NodeKind.FUNCTION, NodeKind.METHOD):
                seen_function = True
            current = owner.parent_symbol
        if hasattr(builtins, name):
            external_id = self._external(f"builtins.{name}", anchor)
            return _Targets((external_id,), Confidence.PROBABLE, "host builtin, not project code")
        return _Targets(reason="name has no indexed lexical binding", issue_code="UNRESOLVED_SYMBOL")

    def _class_for_self(self, scope_id: str | None) -> str | None:
        seen_function = False
        while scope_id is not None:
            owner = self.index.entries.get(scope_id)
            if owner is None:
                return None
            if owner.kind in (NodeKind.FUNCTION, NodeKind.METHOD):
                seen_function = True
            if owner.kind == NodeKind.CLASS and seen_function:
                return owner.id
            scope_id = owner.parent_symbol
        return None

    def _member(self, base: _Targets, name: str, anchor: SourceAnchor) -> _Targets:
        candidates: list[SymbolIndexEntry] = []
        target_ids: list[str] = []
        has_external_member = False
        for target_id in base.ids:
            entry = self.index.entries.get(target_id)
            if entry is not None and entry.kind in (NodeKind.MODULE, NodeKind.CLASS):
                candidates.extend(self.children.get((entry.id, name), ()))
                if entry.kind == NodeKind.MODULE:
                    module_names = ModuleCatalog._names(entry.qualified_name, entry.file_path)
                    for module_name in module_names:
                        target_ids.extend(item.id for item in self.catalog.candidates(f"{module_name}.{name}"))
            elif target_id in self.external:
                external = self.external[target_id]
                target_ids.append(self._external(f"{external.qualified_name}.{name}", anchor))
                has_external_member = True
        from_entries = self._entry_targets(candidates, anchor) if candidates else _Targets()
        target_ids.extend(from_entries.ids)
        unique = tuple(dict.fromkeys(target_ids))
        if not unique:
            if from_entries.issue_code:
                return from_entries
            return _Targets(reason="dynamic or unknown attribute target",
                            issue_code="DYNAMIC_ATTRIBUTE_UNRESOLVED")
        if (base.confidence == Confidence.EXACT and len(unique) == 1
                and not has_external_member and not from_entries.issue_code
                and (not from_entries.ids or from_entries.confidence == Confidence.EXACT)):
            return _Targets(unique, Confidence.EXACT)
        return _Targets(unique, Confidence.PROBABLE, "member target is not uniquely verified")

    def _qualified(self, reference: SymbolReference) -> _Targets:
        assert reference.qualifier is not None
        parts = reference.qualifier.split(".")
        if not all(part.isidentifier() for part in parts):
            return _Targets(reason="dynamic qualifier expression",
                            issue_code="DYNAMIC_ATTRIBUTE_UNRESOLVED")
        if parts[0] == "self":
            class_id = self._class_for_self(reference.scope_symbol_id)
            if class_id is None:
                return _Targets(reason="self has no indexed class owner",
                                issue_code="DYNAMIC_ATTRIBUTE_UNRESOLVED")
            base = _Targets((class_id,), Confidence.EXACT)
        else:
            base = self._name(parts[0], reference.scope_symbol_id,
                              reference.source_anchor.file_path, reference.source_anchor)
        for member in (*parts[1:], reference.name):
            base = self._member(base, member, reference.source_anchor)
            if not base.ids:
                break
        return base

    def run(self) -> ResolutionResult:
        resolved = SymbolIndex()
        resolved.entries = dict(self.index.entries)
        resolved.scope_declarations = {key: dict(value) for key, value in self.index.scope_declarations.items()}
        for entry in self.index.entries.values():
            if "imported_from" not in entry.metadata:
                continue
            targets = self._import_target(entry, entry.source_anchor, frozenset())
            full_path = entry.metadata["imported_from"]
            missing_local_submodule = (
                entry.metadata.get("import_form") == "import"
                and entry.metadata.get("binds_root") == "true"
                and "." in full_path
                and self.catalog.is_project_path(full_path)
                and not self.catalog.candidates(full_path)
            )
            if missing_local_submodule:
                self.issues.append(AnalysisIssue(
                    "IMPORT_UNRESOLVED", f"project module {full_path!r} has no indexed target",
                    entry.file_path, entry.source_anchor,
                ))
            if not targets.ids:
                self.issues.append(AnalysisIssue(
                    targets.issue_code or "IMPORT_UNRESOLVED",
                    targets.reason or "import has no indexed target",
                    entry.file_path, entry.source_anchor,
                ))
        for raw in self.index.references:
            targets = (self._qualified(raw) if raw.qualifier is not None else
                       self._name(raw.name, raw.scope_symbol_id,
                                  raw.source_anchor.file_path, raw.source_anchor))
            if targets.confidence == Confidence.EXACT and len(targets.ids) == 1:
                reference = replace(raw, resolved_definition_id=targets.ids[0],
                                    confidence=Confidence.EXACT)
            elif targets.ids:
                reference = replace(raw, possible_target_ids=targets.ids,
                                    confidence=Confidence.PROBABLE, reason=targets.reason)
                if len(targets.ids) > 1:
                    self.issues.append(AnalysisIssue(
                        "AMBIGUOUS_SYMBOL", targets.reason or "multiple possible targets",
                        raw.source_anchor.file_path, raw.source_anchor,
                    ))
            else:
                reason = targets.reason or "unresolved reference"
                unresolved_id = self._unresolved(raw, reason)
                reference = replace(raw, unresolved_symbol_id=unresolved_id, reason=reason)
                self.issues.append(AnalysisIssue(
                    targets.issue_code or "UNRESOLVED_SYMBOL", reason,
                    raw.source_anchor.file_path, raw.source_anchor,
                ))
            resolved.references.append(reference)
        return ResolutionResult(resolved, tuple(self.external.values()),
                                tuple(self.unresolved.values()), tuple(self.issues))


def resolve_project_symbols(files: tuple[ParsedFile, ...], index: SymbolIndex) -> ResolutionResult:
    """Return resolved references without mutating the syntax-only index."""
    return _Resolver(files, index).run()
