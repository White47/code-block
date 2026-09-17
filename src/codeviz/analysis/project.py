"""One-shot project analysis: scan, parse, and merge lexical symbol facts."""

from dataclasses import dataclass
from pathlib import Path

from codeviz.core import SourceAnchor

from .discovery import discover_python_files
from .python import AnalysisIssue, ParsedFile, parse_python_file
from .symbols import SymbolIndex, index_parsed_file


@dataclass(frozen=True, slots=True)
class ProjectAnalysis:
    files: tuple[ParsedFile, ...]
    symbol_index: SymbolIndex
    issues: tuple[AnalysisIssue, ...]


def analyze_project(project_root: Path, ignore_paths: tuple[str, ...] = ()) -> ProjectAnalysis:
    """Analyze each discovered Python file independently; preserve partial results."""
    files: list[ParsedFile] = []
    index = SymbolIndex()
    issues: list[AnalysisIssue] = []
    for relative_path in discover_python_files(project_root, ignore_paths):
        parsed = parse_python_file(project_root, relative_path)
        files.append(parsed)
        issues.extend(parsed.issues)
        if parsed.tree is not None:
            try:
                index.merge(index_parsed_file(parsed))
            except Exception as error:
                issues.append(AnalysisIssue(
                    "INDEX_ERROR", f"{type(error).__name__}: {error}", relative_path,
                    SourceAnchor(relative_path, 1),
                ))
    return ProjectAnalysis(tuple(files), index, tuple(issues))
