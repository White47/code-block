"""Root evidence is resolved from exact final-wire occurrence provenance."""

import json
import time
import unittest
from dataclasses import replace

from codeviz.assistant.provider import PreparedRequest, ProviderDraft, WireOccurrence
from codeviz.assistant.response import ResponseError
from codeviz.assistant.response_session import (
    RootSourceFragment,
    inject_root,
    verify_root_wire,
)
from codeviz.assistant.snapshot import AnchorRecord


def _fragment(evidence_id: str = "current-evidence") -> RootSourceFragment:
    return RootSourceFragment(evidence_id, "app.py", "value = 1\n",
                              AnchorRecord("app.py", 1, 0, 1, 9), False)


def _draft() -> ProviderDraft:
    logical = {
        "version": 1,
        "mode": "final",
        "messages": [{"role": "user", "content": "question"}],
        "tools": [],
        "answerSchema": {},
        "sourceTags": [],
        "metadataTags": [],
    }
    return ProviderDraft(json.dumps(logical, separators=(",", ":")).encode("utf-8"),
                         512, time.monotonic() + 30)


def _prepared(*, shifted: bool = False) -> tuple[
    PreparedRequest, RootSourceFragment, str, str, int, dict[str, object], str,
]:
    fragment = _fragment()
    injected, logical_pointer, content, source_start = inject_root(_draft(), fragment)
    logical = json.loads(injected.body)
    messages = list(logical["messages"])
    if shifted:
        messages.insert(0, {"role": "system", "content": "json instructions"})
    actual_index = len(messages) - 1
    actual_pointer = f"/messages/{actual_index}/content"
    wire = {"messages": messages}
    prepared = PreparedRequest(
        "prepared", "POST", "https://provider.invalid:443/v1/chat",
        json.dumps(wire, separators=(",", ":")).encode("utf-8"),
        injected.max_output_tokens, injected.deadline, 1024,
        (WireOccurrence(actual_pointer, source_start, len(content), "app.py"),),
        (WireOccurrence(actual_pointer, 0, source_start, "app.py"),), True,
    )
    return (prepared, fragment, logical_pointer, content, source_start, wire,
            actual_pointer)


class RootWireMappingTests(unittest.TestCase):
    def test_shifted_final_message_is_resolved_from_both_occurrence_groups(self):
        prepared, fragment, logical_pointer, content, source_start, _, _ = _prepared(shifted=True)

        verify_root_wire(prepared, fragment, logical_pointer, content, source_start)

    def test_unshifted_generic_message_remains_valid(self):
        prepared, fragment, pointer, content, source_start, _, _ = _prepared()

        verify_root_wire(prepared, fragment, pointer, content, source_start)

    def test_historical_same_source_with_other_evidence_id_is_not_a_root_candidate(self):
        prepared, fragment, pointer, content, source_start, wire, actual_pointer = _prepared()
        historical = _fragment("history-evidence")
        _, _, historical_content, historical_start = inject_root(_draft(), historical)
        messages = list(wire["messages"])
        messages.insert(1, {"role": "user", "content": historical_content})
        current_pointer = "/messages/2/content"
        historical_pointer = "/messages/1/content"
        source = (
            WireOccurrence(historical_pointer, historical_start, len(historical_content), "app.py"),
            WireOccurrence(current_pointer, source_start, len(content), "app.py"),
        )
        metadata = (
            WireOccurrence(historical_pointer, 0, historical_start, "app.py"),
            WireOccurrence(current_pointer, 0, source_start, "app.py"),
        )
        remapped = replace(prepared,
                           body=json.dumps({"messages": messages}, separators=(",", ":")).encode(),
                           source_occurrences=source, metadata_occurrences=metadata)
        self.assertNotEqual(actual_pointer, current_pointer)

        verify_root_wire(remapped, fragment, pointer, content, source_start)

    def test_missing_duplicate_ambiguous_and_cross_group_occurrences_are_rejected(self):
        prepared, fragment, pointer, content, source_start, wire, actual_pointer = _prepared()
        source = prepared.source_occurrences[0]
        metadata = prepared.metadata_occurrences[0]
        duplicate_messages = [*wire["messages"], {"role": "user", "content": content}]
        other_pointer = f"/messages/{len(duplicate_messages) - 1}/content"
        duplicate_body = json.dumps({"messages": duplicate_messages},
                                    separators=(",", ":")).encode()
        cases = (
            replace(prepared, source_occurrences=()),
            replace(prepared, source_occurrences=(source, source)),
            replace(prepared, body=duplicate_body,
                    source_occurrences=(source, WireOccurrence(
                        other_pointer, source_start, len(content), "app.py"))),
            replace(prepared, body=duplicate_body,
                    metadata_occurrences=(WireOccurrence(
                        other_pointer, 0, source_start, "app.py"),)),
        )
        self.assertEqual(source.pointer, actual_pointer)
        self.assertEqual(metadata.pointer, actual_pointer)
        for candidate in cases:
            with self.subTest(candidate=candidate.source_occurrences), self.assertRaises(ResponseError):
                verify_root_wire(candidate, fragment, pointer, content, source_start)

    def test_role_shape_path_span_content_and_bool_offsets_are_rejected(self):
        prepared, fragment, pointer, content, source_start, wire, actual_pointer = _prepared()
        invalid_messages = []
        for replacement in (
            {"role": "system", "content": content},
            {"role": "user", "content": content, "name": "extra"},
            {"role": "user", "content": content + "changed"},
        ):
            messages = list(wire["messages"])
            messages[-1] = replacement
            invalid_messages.append(replace(
                prepared, body=json.dumps({"messages": messages}, separators=(",", ":")).encode()))
        cases = (
            *invalid_messages,
            replace(prepared, source_occurrences=(WireOccurrence(
                actual_pointer, source_start, len(content), "other.py"),)),
            replace(prepared, source_occurrences=(WireOccurrence(
                actual_pointer, source_start + 1, len(content), "app.py"),)),
            replace(prepared, source_occurrences=(WireOccurrence(
                actual_pointer, True, len(content), "app.py"),)),
        )
        for candidate in cases:
            with self.subTest(body=candidate.body[-32:]), self.assertRaises(ResponseError):
                verify_root_wire(candidate, fragment, pointer, content, source_start)

    def test_malformed_or_unbounded_final_wire_is_rejected(self):
        prepared, fragment, pointer, content, source_start, wire, actual_pointer = _prepared()
        messages = json.dumps(wire["messages"], separators=(",", ":")).encode()
        duplicate = b'{"messages":' + messages + b',"messages":' + messages + b'}'
        nonfinite = b'{"messages":' + messages + b',"temperature":NaN}'
        oversized = b'{"messages":' + messages + b',"padding":"' + b"x" * (256 * 1024) + b'"}'
        too_many = json.dumps({"messages": [*wire["messages"], *(
            {"role": "user", "content": "filler"} for _ in range(40))]},
            separators=(",", ":")).encode()
        irrelevant = tuple(WireOccurrence(f"/other/{index}", 0, 1, "app.py")
                           for index in range(1000))
        cases = (
            replace(prepared, body=duplicate),
            replace(prepared, body=nonfinite),
            replace(prepared, body=b"\xef\xbb\xbf" + prepared.body),
            replace(prepared, body=prepared.body + b"\xff"),
            replace(prepared, body=oversized),
            replace(prepared, body=too_many),
            replace(prepared, source_occurrences=prepared.source_occurrences + irrelevant),
        )
        self.assertEqual(prepared.source_occurrences[0].pointer, actual_pointer)
        for candidate in cases:
            with self.subTest(size=len(candidate.body)), self.assertRaises(ResponseError):
                verify_root_wire(candidate, fragment, pointer, content, source_start)


if __name__ == "__main__":
    unittest.main()
