"""Detached, non-navigable metadata retained for failed explanation jobs."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import cast

from .contracts import AgentError


@dataclass(frozen=True, slots=True)
class FailedEvidenceSnapshot:
    """Canonical metadata only; it owns no reader, registry, session, or source text."""

    evidence_id: str
    _logical_json: bytes

    @classmethod
    def _from_verified(
        cls, evidence_id: str, *, evidence: dict[str, object],
        binding: dict[str, object],
    ) -> FailedEvidenceSnapshot:
        if (type(evidence_id) is not str or not evidence_id
                or type(evidence) is not dict or set(evidence) != {"record", "receipt"}
                or type(binding) is not dict
                or set(binding) != {"binding", "chosen_target"}):
            raise AgentError("evidence_unavailable")
        try:
            logical_json = json.dumps(
                {"evidence": evidence, "binding": binding},
                ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError, UnicodeError):
            raise AgentError("evidence_unavailable") from None
        return cls(evidence_id, logical_json)

    def storage_entries(self) -> tuple[dict[str, object], dict[str, object]]:
        value = cast(dict[str, dict[str, object]], json.loads(self._logical_json))
        return value["evidence"], value["binding"]
