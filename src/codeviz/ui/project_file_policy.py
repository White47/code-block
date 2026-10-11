"""Explicit local text policy; unknown files never become editable text."""

import codecs
import io
import re
from pathlib import Path, PurePosixPath, PureWindowsPath
from tokenize import detect_encoding

_TEXT_EXTENSIONS = frozenset({
    ".py", ".pyi", ".txt", ".md", ".json", ".jsonl", ".yaml", ".yml",
    ".toml", ".ini", ".cfg", ".conf", ".log", ".csv", ".tsv", ".xml",
    ".html", ".css", ".js", ".jsx", ".ts", ".tsx", ".sql", ".sh",
    ".bat", ".ps1", ".rst",
})
_TEXT_NAMES = frozenset({
    "readme", "license", "dockerfile", "makefile", ".gitignore",
    ".gitattributes", ".editorconfig", ".env",
})
_PROTECTED_NAMES = frozenset({".git", ".venv", "venv", "node_modules"})


def validate_name(name: str) -> str:
    if (not name or len(name) > 255 or name in {".", ".."} or name[-1] in ". "
            or any(ord(char) < 32 or char in '<>:"/\\|?*' for char in name)):
        raise ValueError("invalid project entry name")
    stem = name.split(".", 1)[0].rstrip(" ").casefold()
    if (stem in {"con", "prn", "aux", "nul", "conin$", "conout$"}
            or stem in {f"{prefix}{number}" for prefix in ("com", "lpt")
                        for number in "123456789¹²³"}):
        raise ValueError("reserved project entry name")
    return name


def relative_path(value: str, *, allow_root: bool = False) -> str:
    if value == "" and allow_root:
        return value
    windows = PureWindowsPath(value)
    if (not value or len(value) > 4096 or windows.drive or windows.root
            or Path(value).is_absolute()):
        raise ValueError("invalid project relative path")
    return "/".join(validate_name(part) for part in value.replace("\\", "/").split("/"))


def is_text_file(relative: str) -> bool:
    name = PurePosixPath(relative.replace("\\", "/")).name.casefold()
    return (PurePosixPath(name).suffix in _TEXT_EXTENSIONS or name in _TEXT_NAMES
            or name.startswith(".env."))


def protected_path_reason(relative: str) -> str | None:
    if any(part.casefold() in _PROTECTED_NAMES
           for part in relative.replace("\\", "/").split("/")):
        return "Protected project files cannot be edited."
    return None


def encode_text(relative: str, text: str, encoding: str) -> bytes:
    content = text.encode(encoding, errors="strict")
    if not relative.casefold().endswith(".py"):
        if encoding == "utf-16-le":
            return codecs.BOM_UTF16_LE + content
        if encoding == "utf-16-be":
            return codecs.BOM_UTF16_BE + content
    return content


def decode_text(relative: str, content: bytes) -> tuple[str, str, str, str | None]:
    """Decode with exact byte roundtrip; coding cookies apply only to Python."""
    if not is_text_file(relative):
        raise ValueError("unsupported text type")
    if relative.casefold().endswith(".py"):
        encoding, _ = detect_encoding(io.BytesIO(content).readline)
        text = content.decode(encoding, errors="strict")
    else:
        # UTF-32 LE starts with the UTF-16 LE marker, so reject it first.
        if content.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
            raise ValueError("unsupported text encoding")
        if content.startswith(codecs.BOM_UTF16_LE):
            encoding = "utf-16-le"
            text = content[2:].decode(encoding, errors="strict")
        elif content.startswith(codecs.BOM_UTF16_BE):
            encoding = "utf-16-be"
            text = content[2:].decode(encoding, errors="strict")
        else:
            encoding = "utf-8-sig" if content.startswith(codecs.BOM_UTF8) else "utf-8"
            text = content.decode(encoding, errors="strict")
        if any((ord(char) < 32 and char not in "\t\r\n") or 127 <= ord(char) <= 159
               for char in text):
            raise ValueError("binary or control characters in text")
    if "\x00" in text:
        raise ValueError("NUL characters in text")
    if encode_text(relative, text, encoding) != content:
        raise ValueError("text cannot roundtrip exactly")
    ends = set(re.findall(r"\r\n|\r|\n", text))
    newline = ("Mixed" if len(ends) > 1 else
               {"\n": "LF", "\r\n": "CRLF", "\r": "CR"}.get(next(iter(ends), "\n"), "LF"))
    reason = {
        "Mixed": "Mixed line endings cannot be edited safely.",
        "CR": "CR-only line endings cannot be edited safely in this editor.",
    }.get(newline)
    return text, encoding, newline, reason
