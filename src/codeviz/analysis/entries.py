"""Source-backed entry candidates; structural evidence never proves execution."""

import ast
from dataclasses import dataclass
from pathlib import PurePosixPath

from codeviz.core import Confidence, NodeKind, SourceAnchor

from .python import ParsedFile
from .symbols import SymbolIndex


@dataclass(frozen=True, slots=True)
class GenericEntryCandidate:
    root_symbol_id: str
    detection_kind: str
    confidence: Confidence
    evidence: tuple[str, ...]
    source_anchor: SourceAnchor

    @property
    def id(self) -> str:
        """The candidate identifies its code root, independently of the view kind."""
        return self.root_symbol_id


def _main_guard(test: ast.expr) -> bool:
    if not (isinstance(test, ast.Compare) and len(test.ops) == 1
            and isinstance(test.ops[0], ast.Eq) and len(test.comparators) == 1):
        return False
    left, right = test.left, test.comparators[0]
    return any(isinstance(name, ast.Name) and name.id == "__name__"
               and isinstance(value, ast.Constant) and value.value == "__main__"
               for name, value in ((left, right), (right, left)))


def detect_generic_entries(files: tuple[ParsedFile, ...],
                           index: SymbolIndex) -> tuple[GenericEntryCandidate, ...]:
    candidates: dict[str, GenericEntryCandidate] = {}
    modules = {item.file_path: item for item in index.entries.values()
               if item.kind == NodeKind.MODULE}
    for item in index.entries.values():
        parent = index.entries.get(item.parent_symbol or "")
        if (item.kind == NodeKind.FUNCTION and item.name == "main"
                and parent is not None and parent.kind == NodeKind.MODULE):
            candidates[item.id] = GenericEntryCandidate(
                item.id, "MainName", Confidence.PROBABLE,
                ("module-level main name suggests an entry; execution is unknown",),
                item.source_anchor,
            )
    references = {(ref.source_anchor.file_path, ref.source_anchor.start_line,
                   ref.source_anchor.start_column): ref for ref in index.references
                  if ref.qualifier is None}
    for parsed in files:
        module = modules.get(parsed.relative_path)
        if module is None:
            continue
        if PurePosixPath(parsed.relative_path).name == "__main__.py":
            candidates[module.id] = GenericEntryCandidate(
                module.id, "MainModule", Confidence.EXACT,
                ("__main__.py module structure; execution is not guaranteed",),
                module.source_anchor,
            )
        if parsed.tree is None:
            continue
        for statement in parsed.tree.body:
            if not isinstance(statement, ast.If) or not _main_guard(statement.test):
                continue
            anchor = SourceAnchor(parsed.relative_path, statement.lineno,
                                  start_column=statement.col_offset,
                                  end_line=statement.end_lineno,
                                  end_column=statement.end_col_offset)
            evidence = ("__name__ == '__main__' guard structure; execution is not guaranteed",)
            targets: list[str] = []
            # Only direct call expressions in the guarded suite are proven syntax.
            for child in statement.body:
                if not (isinstance(child, ast.Expr) and isinstance(child.value, ast.Call)
                        and isinstance(child.value.func, ast.Name)):
                    continue
                call = child.value.func
                ref = references.get((parsed.relative_path, call.lineno, call.col_offset))
                target = index.entries.get(ref.resolved_definition_id or "") if ref else None
                if target is not None and target.kind in (NodeKind.FUNCTION, NodeKind.METHOD):
                    targets.append(target.id)
            for root_id in targets or [module.id]:
                candidates[root_id] = GenericEntryCandidate(
                    root_id, "MainGuard", Confidence.EXACT, evidence, anchor,
                )
    return tuple(candidates.values())
