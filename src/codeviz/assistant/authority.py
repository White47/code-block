"""Immutable, trusted in-memory authority captured outside the service lock."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from .config import ProviderConfig, _parse_url, _validate_config
from .consent import ConsentGrant


@dataclass(frozen=True, slots=True)
class AuthorityState:
    config_enabled: bool
    config_revision: int
    grant_revision: int
    project_key: str
    origin: str
    scope_fingerprint: str
    conversation_revisions: tuple[tuple[str, int], ...]
    model: str
    endpoint: str

    def __post_init__(self) -> None:
        if (type(self.config_enabled) is not bool
                or type(self.config_revision) is not int or self.config_revision < 0
                or type(self.grant_revision) is not int or self.grant_revision < 0
                or type(self.project_key) is not str or not self.project_key
                or type(self.origin) is not str or not self.origin
                or type(self.scope_fingerprint) is not str or not self.scope_fingerprint
                or type(self.conversation_revisions) is not tuple
                or type(self.model) is not str or not self.model
                or len(self.conversation_revisions) > 10
                or type(self.endpoint) is not str):
            raise ValueError("Invalid trusted authority.")
        normalized_origin, path = _parse_url(self.origin)
        if normalized_origin != self.origin or path:
            raise ValueError("Invalid trusted authority.")
        endpoint_origin, endpoint_path = _parse_url(self.endpoint)
        if endpoint_origin != self.origin or not endpoint_path:
            raise ValueError("Invalid trusted authority.")
        names: set[str] = set()
        for entry in self.conversation_revisions:
            if (type(entry) is not tuple or len(entry) != 2
                    or type(entry[0]) is not str or not entry[0] or entry[0] in names
                    or type(entry[1]) is not int or entry[1] < 0):
                raise ValueError("Invalid trusted authority.")
            names.add(entry[0])

    @classmethod
    def from_trusted(cls, config: ProviderConfig, grant: ConsentGrant,
                     conversation_revisions: tuple[tuple[str, int], ...]) -> AuthorityState:
        """Copy verified config/grant values; never consult ConsentStore under a service lock."""
        _validate_config(config)
        if (type(grant) is not ConsentGrant or not grant.enabled
                or grant.origin != config.origin
                or grant.config_revision != config.revision):
            raise ValueError("Invalid trusted authority.")
        grant.__post_init__()
        scope = json.dumps(grant.allowed_files, ensure_ascii=True,
                           separators=(",", ":")).encode("ascii")
        return cls(True, config.revision, grant.revision, grant.project_key,
                   grant.origin, hashlib.sha256(scope).hexdigest(),
                   conversation_revisions, config.model, config.endpoint)

    def conversation_revision(self, conversation_id: str) -> int | None:
        for name, revision in self.conversation_revisions:
            if name == conversation_id:
                return revision
        return None
