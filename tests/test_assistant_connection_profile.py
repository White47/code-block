"""The synthetic probe cannot carry project context or arbitrary output schemas."""

import importlib
import importlib.util
import json
import time
import unittest
from dataclasses import replace

from codeviz.assistant.sdk_provider import _draft


class ConnectionProfileTests(unittest.TestCase):
    def profile(self):
        name = "codeviz.assistant.connection_profile"
        self.assertIsNotNone(importlib.util.find_spec(name), "Missing fixed connection profile")
        return importlib.import_module(name)

    def test_fixed_tools_and_final_profiles_are_accepted_without_optional_sdk(self):
        profile = self.profile()
        deadline = time.monotonic() + 15
        for stage in ("tools", "final"):
            draft = profile.connection_draft(stage, deadline=deadline)
            logical, wire = _draft(draft, time.monotonic())
            self.assertEqual(profile.connection_stage(logical), stage)
            self.assertEqual(len(wire), 1)
            self.assertEqual(logical["sourceTags"], [])
            self.assertNotIn("metadataTags", logical)
            self.assertEqual(draft.deadline, deadline)
        self.assertEqual(sum(profile.connection_draft(stage, deadline=deadline).max_output_tokens
                             for stage in ("tools", "final")), 128)

    def test_probe_schema_cannot_be_reused_with_source_or_conversation_history(self):
        profile = self.profile()
        base = profile.connection_draft("final", deadline=time.monotonic() + 15)
        for mutation in (
            {"messages": [{"role": "user", "content": "private project question"}]},
            {"metadataTags": []},
            {"answerSchema": {"type": "string"}},
        ):
            logical = json.loads(base.body) | mutation
            self.assertIsNone(profile.connection_stage(logical))
            with self.assertRaises(ValueError):
                _draft(replace(base, body=json.dumps(logical).encode()), time.monotonic())

    def test_profile_output_envelope_cannot_be_enlarged(self):
        profile = self.profile()
        for stage in ("tools", "final"):
            base = profile.connection_draft(stage, deadline=time.monotonic() + 15)
            with self.assertRaises(ValueError):
                _draft(replace(base, max_output_tokens=base.max_output_tokens + 1), time.monotonic())
