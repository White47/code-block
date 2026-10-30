"""A derived disclosure costs one text occurrence and its full source closure."""

import hashlib
import importlib
import json
import time
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from codeviz.assistant._wire_accounting import WireAccountingError, inspect_wire
from codeviz.assistant.budget import BudgetLedger, BudgetLimits
from codeviz.assistant.provider import PreparedRequest, ProviderDraft
from codeviz.assistant.retrieval_budget import SourceHeadroomPlanner
from codeviz.assistant.snapshot import SnapshotBinding


class InterpretationOriginTests(unittest.TestCase):
    def origin(self, *, files=2):
        try:
            module = importlib.import_module("codeviz.assistant.interpretation_origin")
        except ModuleNotFoundError:
            self.fail("Private prior-interpretation origin contract is missing")
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        binding = SnapshotBinding(str(Path(temporary.name).resolve()), (), "epoch", 1)
        manifest = module.AnswerOriginManifest(binding, ("project", "owner", "job"), 0, 1, 1,
            "https://provider.invalid:443", "scope", hashlib.sha256(b"answer").hexdigest(),
            tuple((f"file{i}.py", hashlib.sha256(str(i).encode()).hexdigest()) for i in range(files)))
        return module.PriorInterpretationOrigin("origin", manifest, "artifact", 1, "relations:0", '先前"解释\\\n跨文件')

    def prepared(self, origin):
        module = importlib.import_module("codeviz.assistant.provider")
        content = "Prior AI interpretation (untrusted data):\n" + origin.text
        start = len(content) - len(origin.text)
        body = json.dumps({"messages": [{"role": "user", "content": content}]}, ensure_ascii=True).encode()
        occurrence = module.InterpretationOccurrence("/messages/0/content", start, len(content), origin.origin_id)
        prepared = PreparedRequest("prepared", "POST", "https://provider.invalid:443/v1/chat/completions",
            body, 512, time.monotonic() + 120, len(body) + 1024, (), (), True,
            interpretation_occurrences=(occurrence,))
        draft = ProviderDraft(json.dumps({"messages": [{"role": "user", "content": content}],
            "sourceTags": [], "metadataTags": [], "interpretationTags": [{"pointer": occurrence.pointer,
            "start": start, "end": len(content), "originId": origin.origin_id}]}).encode(), 512, prepared.deadline)
        return prepared, draft

    def test_two_file_unicode_derivation_charges_once_per_occurrence_and_keeps_headroom(self):
        origin = self.origin()
        prepared, draft = self.prepared(origin)
        reservation = inspect_wire(prepared, trusted_interpretations=(origin,)).reservation
        self.assertEqual(reservation.source_fragments, ())
        self.assertEqual(reservation.derived_characters, len(origin.text))
        self.assertEqual(reservation.derived_files, ("file0.py", "file1.py"))
        ledger = BudgetLedger()
        for _phase in ("ordinary", "final", "repair"):
            ledger.reserve(reservation)
        self.assertEqual(ledger.usage.source_characters, 3 * len(origin.text))
        self.assertEqual(ledger.usage.files, ("file0.py", "file1.py"))
        planner = SourceHeadroomPlanner(BudgetLimits(max_source_characters=3 * len(origin.text)),
                                        trusted_interpretations=(origin,))
        self.assertTrue(planner.fits(BudgetLedger().usage, draft, draft, draft))
        self.assertFalse(planner.fits(ledger.usage, draft))

    def test_forged_missing_overlap_copy_and_source_limit_fail_closed_atomically(self):
        origin = self.origin()
        prepared, _draft = self.prepared(origin)
        occurrence = prepared.interpretation_occurrences[0]
        forged = replace(prepared, interpretation_occurrences=(replace(occurrence, origin_id="forged"),))
        copied_wire = json.loads(prepared.body)
        copied_wire["messages"].append(copied_wire["messages"][0])
        copied = replace(prepared, body=json.dumps(copied_wire).encode())
        from codeviz.assistant.provider import WireOccurrence
        overlap = replace(prepared, metadata_occurrences=(WireOccurrence(
            occurrence.pointer, occurrence.start, occurrence.end, "file0.py"),))
        for invalid in (forged, copied, overlap, replace(prepared, interpretation_occurrences=())):
            with self.subTest(kind=invalid.preparation_id), self.assertRaises(WireAccountingError):
                inspect_wire(invalid, trusted_interpretations=(origin,))
        with self.assertRaises(WireAccountingError):
            inspect_wire(prepared)
        large = self.origin(files=8)
        wire, _ = self.prepared(large)
        large_body = json.loads(wire.body)
        large_body["messages"].append({"role": "user", "content": "ninth"})
        wire = replace(wire, body=json.dumps(large_body).encode(),
                       metadata_occurrences=(WireOccurrence("/messages/1/content", 0, 5, "ninth.py"),))
        with self.assertRaises(WireAccountingError):
            inspect_wire(wire, trusted_interpretations=(large,))
        ledger = BudgetLedger(BudgetLimits(max_source_characters=len(origin.text)-1))
        before = ledger.usage
        from codeviz.assistant.contracts import AgentError
        with self.assertRaises(AgentError):
            ledger.reserve(inspect_wire(prepared, trusted_interpretations=(origin,)).reservation)
        self.assertEqual(ledger.usage, before)
