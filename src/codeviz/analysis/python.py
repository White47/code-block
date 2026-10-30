"""Parse Python syntax without importing or executing the target project."""

import ast
import tokenize
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from codeviz.core import SourceAnchor
from codeviz.source_handles import SourceReadError, read_source_bytes


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
    parsed_success: bool | None = None
    content_hash: str | None = None

    @property
    def was_parsed(self) -> bool:
        """Cache snapshots retain success metadata without retaining an AST."""
        return self.parsed_success if self.parsed_success is not None else self.tree is not None


def _module_name(relative_path: str) -> str:
    parts = list(Path(relative_path).with_suffix("").parts)
    if parts[-1] == "__init__" and len(parts) > 1:
        parts.pop()
    return ".".join(parts)


def _failed(relative_path: str, module_name: str, code: str, message: str, line: int = 1,
            *, content_hash: str | None = None) -> ParsedFile:
    issue = AnalysisIssue(code, message, relative_path, SourceAnchor(relative_path, line))
    return ParsedFile(relative_path, module_name, None, 0, (issue,), content_hash=content_hash)


def parse_python_file(
    project_root: Path,
    relative_path: str,
    *,
    max_file_bytes: int = 5_000_000,
) -> ParsedFile:
    if type(max_file_bytes) is not int or max_file_bytes < 1:
        raise ValueError("max_file_bytes must be a positive integer")
    relative = SourceAnchor(relative_path, 1).file_path
    if not relative.casefold().endswith(".py"):
        raise ValueError("relative_path must point to a Python file")
    root = Path(project_root).resolve()
    if not root.is_dir():
        raise ValueError("project_root must be an existing directory")
    module_name = _module_name(relative)
    try:
        verified = read_source_bytes(root, relative, max_bytes=max_file_bytes)
    except SourceReadError as error:
        return _failed(relative, module_name, error.code, error.message)

    try:
        encoding, _ = tokenize.detect_encoding(BytesIO(verified.raw_bytes).readline)
        source = verified.raw_bytes.decode(encoding, errors="strict")
    except (SyntaxError, UnicodeError, LookupError) as error:
        return _failed(relative, module_name, "DECODE_ERROR", str(error),
                       content_hash=verified.content_hash)

    source = source.replace("\r\n", "\n").replace("\r", "\n")
    line_count = max(1, source.count("\n") + (not source.endswith("\n")))
    try:
        tree = ast.parse(source, filename=relative, type_comments=True)
    except RecursionError:
        return _failed(relative, module_name, "PARSE_DEPTH_EXCEEDED", "Python syntax tree is too deep",
                       content_hash=verified.content_hash)
    except UnicodeError as error:
        return _failed(relative, module_name, "DECODE_ERROR", str(error),
                       content_hash=verified.content_hash)
    except SyntaxError as error:
        issue = AnalysisIssue(
            "SYNTAX_ERROR", error.msg, relative,
            SourceAnchor(relative, error.lineno or 1),
        )
        return ParsedFile(relative, module_name, None, line_count, (issue,),
                          content_hash=verified.content_hash)
    return ParsedFile(relative, module_name, tree, line_count, content_hash=verified.content_hash)
