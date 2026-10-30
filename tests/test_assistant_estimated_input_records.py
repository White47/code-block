"""Frozen estimated-accounting values and final-wire admission checks."""

import hashlib
import json
import unittest
from dataclasses import FrozenInstanceError, replace

from assistant_estimated_fixtures import PROFILE, prepared

from codeviz.assistant._usage_values import InputAccountingSnapshot, ObservedUsage
from codeviz.assistant._wire_accounting import (
    WireAccountingError,
    inspect_wire,
    wire_reservation,
)
from codeviz.assistant.poll_public import ObservedUsage as PublicObservedUsage
from codeviz.assistant.provider import (
    EstimatedInputAccounting,
    PreparedRequest,
    WireOccurrence,
)


class EstimatedInputRecordTests(unittest.TestCase):
    def test_usage_records_are_frozen_and_observed_usage_is_atomically_reexported(self):
        observed = ObservedUsage(3, 2, "actual")
        snapshot = InputAccountingSnapshot("wire-bytes-v1", 20_000, 3, None)

        self.assertIs(PublicObservedUsage, ObservedUsage)
        with self.assertRaises(FrozenInstanceError):
            observed.input_tokens = 4  # type: ignore[misc]
        with self.assertRaises(FrozenInstanceError):
            snapshot.limit_tokens = 1  # type: ignore[misc]

    def test_strict_inspection_preserves_source_and_compatibility_result(self):
        body = json.dumps({
            "model": "strict",
            "messages": [{"role": "user", "content": "alpha beta"}],
        }, separators=(",", ":")).encode("utf-8")
        request = PreparedRequest(
            "strict-one", "POST", "https://provider.invalid:443/v1/chat",
            body, 16, 100.0, len(body) + 10,
            (WireOccurrence("/messages/0/content", 0, 5, "a.py"),),
            (WireOccurrence("/messages/0/content", 6, 10, "b.py"),), True,
        )

        accepted = inspect_wire(request)

        self.assertEqual(accepted.reservation.input_tokens, len(body) + 10)
        self.assertEqual(accepted.reservation.source_fragments, (("a.py", "alpha"),))
        self.assertEqual(accepted.files, ("a.py", "b.py"))
        self.assertEqual((accepted.mode, accepted.profile_id, accepted.method),
                         ("strict", None, None))
        self.assertEqual(wire_reservation(request), (accepted.reservation, accepted.files))

    def test_estimated_inspection_requires_matching_frozen_body_and_trusted_profile(self):
        request = prepared(1000)

        accepted = inspect_wire(request, trusted_profiles=(PROFILE,))

        self.assertEqual(accepted.reservation.input_tokens, 1000)
        self.assertEqual(accepted.files, ())
        self.assertEqual((accepted.mode, accepted.profile_id, accepted.method),
                         ("estimated", PROFILE.profile_id, "wire-bytes-v1"))
        for bad in (
            replace(request, estimated_input_accounting=replace(
                request.estimated_input_accounting, estimated_input_tokens=999)),
            replace(request, estimated_input_accounting=replace(
                request.estimated_input_accounting, body_sha256="0" * 64)),
            replace(request, estimated_input_accounting=replace(
                request.estimated_input_accounting, profile_id="untrusted")),
            replace(request, endpoint="https://other.invalid:443/v1/chat/completions"),
        ):
            with self.subTest(bad=bad), self.assertRaises(WireAccountingError):
                inspect_wire(bad, trusted_profiles=(PROFILE,))
        with self.assertRaises(WireAccountingError):
            inspect_wire(request, trusted_profiles=())

        wrong_model_body = json.dumps({
            "model": "other",
            "messages": [{"role": "user", "content": "x"}],
        }, separators=(",", ":")).encode("utf-8")
        wrong_model = replace(
            request,
            body=wrong_model_body,
            estimated_input_accounting=EstimatedInputAccounting(
                PROFILE.profile_id,
                "wire-bytes-v1",
                len(wrong_model_body),
                hashlib.sha256(wrong_model_body).hexdigest(),
            ),
        )
        with self.assertRaises(WireAccountingError):
            inspect_wire(wrong_model, trusted_profiles=(PROFILE,))

    def test_estimated_metadata_rejects_malformed_exact_field_types(self):
        class StringSubclass(str):
            pass

        request = prepared(1000)
        accounting = request.estimated_input_accounting
        cases = (
            (replace(request, estimated_input_accounting=replace(
                accounting, estimated_input_tokens=True)), (PROFILE,)),
            (replace(request, estimated_input_accounting=replace(
                accounting, body_sha256=accounting.body_sha256.upper())), (PROFILE,)),
            (replace(request, estimated_input_accounting=replace(
                accounting, profile_id="bad\n")),
             (replace(PROFILE, profile_id="bad\n"),)),
            (replace(request, estimated_input_accounting=replace(
                accounting, method=StringSubclass("wire-bytes-v1"))), (PROFILE,)),
            (request, (replace(
                PROFILE, method=StringSubclass("wire-bytes-v1")),)),
        )

        for malformed, trusted_profiles in cases:
            with self.subTest(malformed=malformed), self.assertRaises(WireAccountingError):
                inspect_wire(malformed, trusted_profiles=trusted_profiles)

    def test_modes_and_attribution_fail_closed(self):
        estimated = prepared(1000)
        missing = replace(estimated, estimated_input_accounting=None)
        both = replace(estimated, input_token_upper_bound=1000)
        incomplete = replace(estimated, attribution_complete=False)

        for request in (missing, both, incomplete):
            with self.subTest(request=request), self.assertRaises(WireAccountingError) as caught:
                inspect_wire(request, trusted_profiles=(PROFILE,))
            self.assertEqual(caught.exception.code, "AI_PROTOCOL_INCOMPATIBLE")
            self.assertEqual(str(caught.exception), "AI_PROTOCOL_INCOMPATIBLE")

    def test_deeply_nested_valid_wire_normalizes_parser_recursion_failure(self):
        body = (
            b'{"messages":[{"role":"user","content":"x","nested":'
            + b"[" * 1100
            + b"0"
            + b"]" * 1100
            + b"}]}"
        )
        request = PreparedRequest(
            "deep-wire", "POST", "https://provider.invalid:443/v1/chat",
            body, 1, 100.0, len(body), (), (), True,
        )

        with self.assertRaises(WireAccountingError) as caught:
            inspect_wire(request)

        self.assertEqual(caught.exception.code, "AI_PROTOCOL_INCOMPATIBLE")

    def test_strict_wire_duplicate_fragment_and_metadata_checks_remain_closed(self):
        duplicate_body = b'{"messages":[{"content":"alpha"}],"messages":[{"content":"alpha"}]}'
        duplicate = PreparedRequest(
            "duplicate", "POST", "https://provider.invalid:443/v1/chat",
            duplicate_body, 1, 100.0, len(duplicate_body), (), (), True,
        )
        repeated_body = b'{"messages":[{"content":"alpha alpha"}]}'
        unattributed = PreparedRequest(
            "fragment", "POST", "https://provider.invalid:443/v1/chat",
            repeated_body, 1, 100.0, len(repeated_body),
            (WireOccurrence("/messages/0/content", 0, 5, "a.py"),), (), True,
        )
        overlap_body = b'{"messages":[{"content":"alpha"}]}'
        overlap = PreparedRequest(
            "metadata", "POST", "https://provider.invalid:443/v1/chat",
            overlap_body, 1, 100.0, len(overlap_body),
            (WireOccurrence("/messages/0/content", 0, 5, "a.py"),),
            (WireOccurrence("/messages/0/content", 0, 5, "a.py"),), True,
        )

        for request in (duplicate, unattributed, overlap):
            with self.subTest(request=request), self.assertRaises(WireAccountingError):
                inspect_wire(request)


if __name__ == "__main__":
    unittest.main()
