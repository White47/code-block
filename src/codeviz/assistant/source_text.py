"""Pure Python decoding, source coordinate selection and credential screening."""

import re
from dataclasses import dataclass
from io import BytesIO
from tokenize import detect_encoding

from .snapshot import AnchorRecord
from .source_policy import _fail, _text, validate_source_path

SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED )?PRIVATE KEY-----"),
    re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    re.compile(r"\b(?:ghp_|github_pat_)[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"(?i)\bBearer[ \t]+[A-Za-z0-9._~+/-]{16,}={0,2}"),
    re.compile(
        r"(?im)\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|"
        r"client[_-]?secret|password|passwd|secret[_-]?key)\b"
        r"[\"']?[ \t]*(?:=|:)[ \t]*[\"'][^\"'\n]{8,}[\"']"
    ),
    re.compile(r"(?i)\b(?:https?|postgres(?:ql)?|mysql|mongodb(?:\+srv)?)://"
               r"[^\s/:@]+:[^\s/@]+@"),
)


@dataclass(frozen=True, slots=True)
class MonacoRange:
    start_line: int
    start_column: int
    end_line: int
    end_column: int

    def __post_init__(self) -> None:
        for value in (self.start_line, self.start_column, self.end_line, self.end_column):
            if type(value) is not int or value < 1:
                _fail()
        if (self.start_line, self.start_column) > (self.end_line, self.end_column):
            _fail()


@dataclass(frozen=True, slots=True)
class ExcerptLimits:
    max_file_bytes: int = 5_000_000
    max_lines: int = 200
    max_characters: int = 4_000

    def __post_init__(self) -> None:
        for value, cap in ((self.max_file_bytes, 5_000_000), (self.max_lines, 200),
                           (self.max_characters, 4_000)):
            if type(value) is not int or not 1 <= value <= cap:
                _fail()


_DEFAULT_LIMITS = ExcerptLimits()


def _anchor(anchor: AnchorRecord) -> None:
    if type(anchor) is not AnchorRecord:
        _fail()
    try:
        validate_source_path(anchor.file_path)
        if (type(anchor.start_line) is not int or type(anchor.end_line) is not int
                or not 1 <= anchor.start_line <= anchor.end_line):
            _fail("invalid_range")
        for column in (anchor.start_column, anchor.end_column):
            if column is not None and (type(column) is not int or column < 0):
                _fail("invalid_range")
        if (anchor.start_line == anchor.end_line and anchor.start_column is not None
                and anchor.end_column is not None and anchor.start_column > anchor.end_column):
            _fail("invalid_range")
    except AttributeError:
        _fail()


@dataclass(frozen=True, slots=True)
class TextSelection:
    text: str
    returned_anchor: AnchorRecord
    monaco_range: MonacoRange
    truncated: bool

    def __post_init__(self) -> None:
        _text(self.text)
        _anchor(self.returned_anchor)
        if type(self.monaco_range) is not MonacoRange or type(self.truncated) is not bool:
            _fail()
        try:
            self.monaco_range.__post_init__()
        except AttributeError:
            _fail()


def decode_python_source(raw_bytes: bytes) -> str:
    if type(raw_bytes) is not bytes:
        _fail()
    try:
        encoding, _ = detect_encoding(BytesIO(raw_bytes).readline)
        text = raw_bytes.decode(encoding, errors="strict")
        text.encode("utf-8", errors="strict")
    except (SyntaxError, UnicodeError, LookupError):
        _fail("decode_failed")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _boundary(line: str, offset: int) -> tuple[int, int]:
    """Return Unicode and one-based UTF-16 positions for a UTF-8 boundary."""
    if type(offset) is not int or offset < 0:
        _fail("invalid_range")
    encoded = line.encode("utf-8")
    if offset > len(encoded):
        _fail("invalid_range")
    try:
        prefix = encoded[:offset].decode("utf-8", errors="strict")
    except UnicodeError:
        _fail("invalid_range")
    return len(prefix), len(prefix.encode("utf-16-le")) // 2 + 1


def select_source_text(source: str, anchor: AnchorRecord,
                       *, limits: ExcerptLimits = _DEFAULT_LIMITS) -> TextSelection:
    _text(source)
    _anchor(anchor)
    if type(limits) is not ExcerptLimits:
        _fail()
    try:
        limits.__post_init__()
    except AttributeError:
        _fail()
    lines = source.split("\n")
    if source and source.endswith("\n"):
        lines.pop()
    if anchor.end_line > len(lines):
        _fail("invalid_range")
    first, last = anchor.start_line - 1, anchor.end_line - 1
    # Validate both supplied endpoints before None expands the selection.
    for index, offset in ((first, anchor.start_column), (last, anchor.end_column)):
        if offset is not None:
            _boundary(lines[index], offset)
    start = anchor.start_column
    end = anchor.end_column
    if start is None or end is None:
        start, end = 0, len(lines[last].encode("utf-8"))
    start_char, start_monaco = _boundary(lines[first], start)
    end_char, _ = _boundary(lines[last], end)
    # Construct only the bounded prefix, while preserving the requested endpoint.
    pieces: list[str] = []
    remaining = limits.max_characters
    final_line, final_char = first, start_char
    stop_line = min(last, first + limits.max_lines - 1)
    for index in range(first, stop_line + 1):
        left = start_char if index == first else 0
        right = end_char if index == last else len(lines[index])
        take = min(right - left, remaining)
        pieces.append(lines[index][left:left + take])
        remaining -= take
        final_line, final_char = index, left + take
        if take < right - left or index == last or index == stop_line or remaining == 0:
            break
        pieces.append("\n")
        remaining -= 1
        final_line, final_char = index + 1, 0
        if remaining == 0:
            break
    text = "".join(pieces)
    final_byte = len(lines[final_line][:final_char].encode("utf-8"))
    _, final_monaco = _boundary(lines[final_line], final_byte)
    returned = AnchorRecord(anchor.file_path, anchor.start_line, start,
                            final_line + 1, final_byte)
    monaco = MonacoRange(anchor.start_line, start_monaco, final_line + 1, final_monaco)
    return TextSelection(text, returned, monaco, (final_line, final_byte) != (last, end))


def contains_suspected_secret(source: str) -> bool:
    _text(source)
    return any(pattern.search(source) is not None for pattern in SECRET_PATTERNS)
