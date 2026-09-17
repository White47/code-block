"""Parse Python syntax without importing or executing the target project."""

import ast
import tokenize
from dataclasses import dataclass
from pathlib import Path

from codeviz.core import SourceAnchor


@dataclass(frozen=True, slots=True)
class AnalysisIssue:
    code: str
    message: str
    file_path: str
    source_anchor: SourceAnchor | None = None


@dataclass(frozen=True, slots=True)
class ParsedFile:
    relative_path: str
    module_name: str
    tree: ast.Module | None
    line_count: int
    issues: tuple[AnalysisIssue, ...] = ()


def _module_name(relative_path: str) -> str:
    parts = list(Path(relative_path).with_suffix("").parts)
    if parts[-1] == "__init__" and len(parts) > 1:
        parts.pop()
    return ".".join(parts)


def _failed(relative_path: str, module_name: str, code: str, message: str, line: int = 1) -> ParsedFile:
    issue = AnalysisIssue(code, message, relative_path, SourceAnchor(relative_path, line))
    return ParsedFile(relative_path, module_name, None, 0, (issue,))


def parse_python_file(
    project_root: Path,
    relative_path: str,
    *,
    max_file_bytes: int = 5_000_000,
) -> ParsedFile:
    if max_file_bytes < 1:
        raise ValueError("max_file_bytes must be positive")
    relative = SourceAnchor(relative_path, 1).file_path
    if not relative.casefold().endswith(".py"):
        raise ValueError("relative_path must point to a Python file")
    root = Path(project_root).resolve()
    if not root.is_dir():
        raise ValueError("project_root must be an existing directory")
    module_name = _module_name(relative)
    source_path = root.joinpath(*relative.split("/"))
    if not source_path.resolve().is_relative_to(root):
        raise ValueError("source file resolves outside project_root")

    current = root
    for segment in relative.split("/"):
        current = current / segment
        if current.is_symlink():
            return _failed(relative, module_name, "SYMLINK_UNSUPPORTED", "linked source files are not scanned")

    try:
        if source_path.stat().st_size > max_file_bytes:
            return _failed(relative, module_name, "FILE_TOO_LARGE", "source file exceeds size limit")
        with tokenize.open(source_path) as stream:
            source = stream.read()
    except (SyntaxError, UnicodeError, LookupError) as error:
        return _failed(relative, module_name, "DECODE_ERROR", str(error))
    except OSError as error:
        return _failed(relative, module_name, "READ_ERROR", str(error))

    line_count = max(1, len(source.splitlines()))
    try:
        tree = ast.parse(source, filename=relative, type_comments=True)
    except RecursionError:
        return _failed(relative, module_name, "PARSE_DEPTH_EXCEEDED", "Python syntax tree is too deep")
    except SyntaxError as error:
        issue = AnalysisIssue(
            "SYNTAX_ERROR", error.msg, relative,
            SourceAnchor(relative, error.lineno or 1),
        )
        return ParsedFile(relative, module_name, None, line_count, (issue,))
    return ParsedFile(relative, module_name, tree, line_count)
