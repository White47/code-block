"""Closed C7 JSON projection for receipt-validated assistant answers.

This sidecar only revalidates and serializes an existing ``Answer``. It neither
resolves receipt authority nor turns projected prose into proof.
"""

import json

from .response import Answer, ResponseError, _answer_json

_MAX_PUBLIC_BYTES = 64 * 1024


def _project(answer: Answer) -> dict[str, object]:
    if type(answer) is not Answer:
        raise TypeError
    Answer.__post_init__(answer)
    if any(claim.basis not in ("inference", "unknown")
           for section in answer.sections for claim in section.claims):
        raise ValueError
    public = _answer_json(answer)
    encoded = json.dumps(
        public,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    if len(encoded) > _MAX_PUBLIC_BYTES:
        raise ValueError
    return public


def answer_to_public(answer: Answer) -> dict[str, object]:
    """Return the exact bounded C7 answer DTO or one fixed response failure."""
    try:
        return _project(answer)
    except Exception:
        pass
    raise ResponseError() from None
