"""Pure source permission values and conservative lexical path checks."""

import re
import unicodedata
from dataclasses import dataclass
from typing import Literal, NoReturn
from urllib.parse import urlsplit

from .contracts import AgentError
from .snapshot import SnapshotBinding

SourceErrorCode = Literal[
    "invalid_input", "scope_mismatch", "consent_required", "path_denied",
    "source_untrusted", "source_changed", "source_unavailable", "decode_failed",
    "invalid_range", "secret_blocked", "verification_failed",
]

_MESSAGES = {
    "invalid_input": "Invalid source excerpt input.",
    "scope_mismatch": "Source request scope changed.",
    "consent_required": "Source permission is unavailable.",
    "path_denied": "Source path is not permitted.",
    "source_untrusted": "Source analysis identity is unavailable.",
    "source_changed": "Source differs from the analysis snapshot.",
    "source_unavailable": "Source could not be safely read.",
    "decode_failed": "Source encoding could not be decoded.",
    "invalid_range": "Source range is invalid.",
    "secret_blocked": "Source excerpt blocked by suspected credentials.",
    "verification_failed": "Source verification could not be completed.",
}
_OPAQUE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_DEVICE = re.compile(r"(?:con|prn|aux|nul|com[1-9¹²³]|lpt[1-9¹²³])", re.IGNORECASE)
_PROTECTED_COMPONENTS = frozenset({
    ".git", ".hg", ".svn", ".ssh", ".aws", ".azure", ".gnupg", ".codex",
    "secrets", "credentials", "private_keys",
})
_KEY_NAMES = ("id_rsa", "id_dsa", "id_ecdsa", "id_ed25519")
_PROTECTED_NAMES = frozenset({
    "credentials", "credentials.json", "secrets.json", "secrets.py", "credentials.py",
})
_PROTECTED_EXTENSIONS = (".pem", ".key", ".p12", ".pfx", ".crt", ".cer",
                         ".der", ".jks", ".keystore")


class SourceExcerptError(ValueError):
    """Fixed public errors without user input or underlying exception details."""

    def __init__(self, code: SourceErrorCode) -> None:
        self.code: SourceErrorCode = (
            code if type(code) is str and code in _MESSAGES else "invalid_input")
        self.message: str = _MESSAGES[self.code]
        super().__init__(self.message)


def _fail(code: SourceErrorCode = "invalid_input") -> NoReturn:
    raise SourceExcerptError(code) from None


def _text(value: str) -> None:
    if type(value) is not str:
        _fail()
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeError:
        _fail()


def validate_source_path(relative_path: str) -> str:
    """Validate, without normalizing, a portable canonical relative file path."""
    _text(relative_path)
    if (not relative_path or any(c in relative_path for c in '\\:*?"<>|[]')
            or any(unicodedata.category(c) == "Cc" for c in relative_path)):
        _fail()
    for component in relative_path.split("/"):
        if (not component or component in (".", "..") or component.endswith((".", " "))
                or _DEVICE.fullmatch(component.split(".", 1)[0].rstrip(" ").casefold()) is not None):
            _fail()
    return relative_path


def is_protected_source_path(relative_path: str) -> bool:
    components = validate_source_path(relative_path).casefold().split("/")
    basename = components[-1]
    return (any(c in _PROTECTED_COMPONENTS for c in components)
            or basename == ".env" or basename.startswith(".env.")
            or any(basename == name or basename.startswith(name + ".") for name in _KEY_NAMES)
            or basename in _PROTECTED_NAMES or basename.endswith(_PROTECTED_EXTENSIONS))


def _binding(binding: SnapshotBinding) -> None:
    if type(binding) is not SnapshotBinding:
        _fail()
    try:
        # Revalidate without mutating an existing, potentially corrupted DTO.
        SnapshotBinding(binding.project_root, binding.ignore_paths,
                        binding.project_epoch, binding.analysis_generation)
    except (AgentError, AttributeError, TypeError, ValueError):
        _fail()


def _origin(origin: str) -> None:
    _text(origin)
    if any(c.isspace() or unicodedata.category(c) == "Cc" for c in origin):
        _fail()
    try:
        parsed = urlsplit(origin)
        port = parsed.port
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or parsed.path not in ("", "/")
                or parsed.query or parsed.fragment or "?" in origin or "#" in origin
                or "\\" in origin or parsed.netloc.endswith(":")
                or (port is not None and not 1 <= port <= 65535)):
            _fail()
    except ValueError:
        _fail()


@dataclass(frozen=True, slots=True)
class SourceConsent:
    binding: SnapshotBinding
    consent_id: str
    provider_origin: str
    allowed_files: tuple[str, ...]

    def __post_init__(self) -> None:
        _binding(self.binding)
        _text(self.consent_id)
        if _OPAQUE.fullmatch(self.consent_id) is None:
            _fail()
        _origin(self.provider_origin)
        if type(self.allowed_files) is not tuple:
            _fail()
        for path in self.allowed_files:
            validate_source_path(path)
        if (self.allowed_files != tuple(sorted(set(self.allowed_files)))
                or len({p.casefold() for p in self.allowed_files}) != len(self.allowed_files)):
            _fail()
