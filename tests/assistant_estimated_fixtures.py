import hashlib
import json

from codeviz.assistant._wire_accounting import TrustedEstimatedProfile
from codeviz.assistant.provider import EstimatedInputAccounting, PreparedRequest

PROFILE = TrustedEstimatedProfile(
    "estimated-fixture-v1",
    "https://estimated.invalid:443/v1/chat/completions",
    "estimated-fixture",
)


def prepared(size: int, identity: str = "one") -> PreparedRequest:
    body = json.dumps(
        {
            "model": PROFILE.model,
            "messages": [{"role": "user", "content": "x"}],
        },
        separators=(",", ":"),
    ).encode("utf-8")
    if size < len(body):
        raise ValueError("Synthetic body size is too small")
    body += b" " * (size - len(body))
    estimate = EstimatedInputAccounting(
        PROFILE.profile_id,
        "wire-bytes-v1",
        len(body),
        hashlib.sha256(body).hexdigest(),
    )
    return PreparedRequest(
        identity,
        "POST",
        PROFILE.endpoint,
        body,
        32,
        1_000_000_000.0,
        None,
        (),
        (),
        True,
        estimate,
    )
