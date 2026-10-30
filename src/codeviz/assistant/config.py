"""Pure, immutable destination configuration and fixed public AI errors."""

import re
from dataclasses import dataclass
from ipaddress import IPv6Address
from typing import NoReturn
from urllib.parse import urlsplit

_MESSAGES = {
    "AI_INVALID_INPUT": "Invalid assistant input.",
    "AI_PROTOCOL_INCOMPATIBLE": "Assistant protocol is incompatible.",
    "AI_NOT_CONFIGURED": "Assistant is not configured.",
    "AI_DEPENDENCY_MISSING": "Assistant dependency is unavailable.",
    "AI_EVIDENCE_INVALID": "Assistant evidence validation failed.",
}
_MAX_REVISION = 2**63 - 1


class ProviderError(ValueError):
    """Expose only a fixed code/message, never credentials or SDK details."""

    def __init__(self, code: str = "AI_INVALID_INPUT") -> None:
        self.code = code if type(code) is str and code in _MESSAGES else "AI_INVALID_INPUT"
        super().__init__(_MESSAGES[self.code])


def _fail(code: str = "AI_INVALID_INPUT") -> NoReturn:
    raise ProviderError(code) from None


def _printable(value: str, maximum: int) -> None:
    if type(value) is not str or not 1 <= len(value) <= maximum or not value.isprintable():
        _fail()
    try:
        value.encode("utf-8", "strict")
    except UnicodeError:
        _fail()


def _revision(value: int) -> None:
    if type(value) is not int or not 0 <= value <= _MAX_REVISION:
        _fail()


def _parse_url(value: str) -> tuple[str, str]:
    """Return semantic origin and untouched path without DNS or normalization of aliases."""
    _printable(value, 2048)
    if any(c.isspace() for c in value) or any(c in value for c in "\\%?#@"):
        _fail()
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        port = parsed.port
        if (parsed.scheme != "https" or not host or not parsed.netloc
                or parsed.netloc.endswith(":") or parsed.username is not None
                or parsed.password is not None or port is not None and not 1 <= port <= 65535):
            _fail()
        if ":" in host:
            host = "[" + IPv6Address(host).compressed + "]"
            authority = re.fullmatch(r"\[[^\]]+\](?::([0-9]+))?", parsed.netloc)
        else:
            host = host.encode("idna").decode("ascii").lower()
            labels = host.split(".")
            if (len(host) > 253 or any(
                    re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) is None
                    for label in labels)):
                _fail()
            for label in labels:
                if label.startswith("xn--"):
                    decoded = label.encode("ascii").decode("idna")
                    if decoded.encode("idna").decode("ascii").lower() != label:
                        _fail()
            authority = re.fullmatch(r"[^:]+(?::([0-9]+))?", parsed.netloc)
        if authority is None:
            _fail()
        if authority[1] is not None and authority[1] != str(port):
            _fail()
        path = parsed.path
        if ("//" in path or any(part in (".", "..") for part in path.split("/"))):
            _fail()
        return f"https://{host}:{port or 443}", path
    except (ValueError, UnicodeError):
        _fail()


def _destination(base_url: str) -> tuple[str, str, str]:
    origin, path = _parse_url(base_url)
    if path not in ("", "/", "/v1", "/v1/"):
        _fail("AI_PROTOCOL_INCOMPATIBLE")
    prefix = "/v1" if path.startswith("/v1") else ""
    return origin + prefix, origin, origin + prefix + "/chat/completions"


@dataclass(frozen=True, slots=True)
class ProviderConfig:
    base_url: str
    origin: str
    endpoint: str
    model: str
    revision: int

    def __post_init__(self) -> None:
        base_url, origin, endpoint = _destination(self.base_url)
        if (self.base_url, self.origin, self.endpoint) != (base_url, origin, endpoint):
            _fail()
        _printable(self.model, 128)
        _revision(self.revision)

    @classmethod
    def from_base_url(cls, base_url: str, model: str, revision: int) -> "ProviderConfig":
        base, origin, endpoint = _destination(base_url)
        return cls(base, origin, endpoint, model, revision)


def _validate_config(config: ProviderConfig) -> None:
    if type(config) is not ProviderConfig:
        _fail()
    config.__post_init__()
