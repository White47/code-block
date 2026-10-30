"""Protocol-valid, source-free answers for service lifecycle test providers."""

import hashlib
import json

from codeviz.assistant.provider import ProviderEvent


def scope_fingerprint(allowed_files: tuple[str, ...]) -> str:
    encoded = json.dumps(allowed_files, ensure_ascii=True,
                         separators=(",", ":")).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


EMPTY_SCOPE_FINGERPRINT = scope_fingerprint(())


def final_answer_text() -> str:
    sections = [{"kind": kind, "claims": []} for kind in (
        "summary", "entry", "steps", "branches", "relations", "evidence", "unknowns")]
    sections[0]["claims"] = [{
        "text": "Unable to confirm the function without evidence.",
        "basis": "unknown", "evidenceIds": [], "relationEvidenceId": None,
    }]
    return json.dumps({"sections": sections}, separators=(",", ":"))


def final_answer_event() -> ProviderEvent:
    return ProviderEvent("answer", json.dumps({
        "index": 0, "last": True, "content": final_answer_text(),
    }, separators=(",", ":")).encode("utf-8"))
