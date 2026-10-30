"""Pinned real SDK mapping of private derived ranges, with zero HTTP handoff."""

import importlib
import json
import unittest
from unittest.mock import Mock

import test_assistant_interpretation_origin as origins
import test_assistant_sdk_provider as fixture
import test_assistant_sdk_provider_deepseek as deepseek

from codeviz.assistant._wire_accounting import inspect_wire
from codeviz.assistant.config import ProviderConfig
from codeviz.assistant.provider import MemoryKey


class InterpretationSDKTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = deepseek.DeepSeekPinnedSDKTests.asyncSetUp
    adapter = deepseek.DeepSeekPinnedSDKTests.adapter
    origin = origins.InterpretationOriginTests.origin

    async def test_unicode_escape_ranges_survive_generic_and_deepseek_final_insertion(self):
        origin = self.origin()
        module = importlib.import_module(fixture.MODULE)
        for is_deepseek in (False, True):
            for mode in ("text", "final"):
                with self.subTest(deepseek=is_deepseek, mode=mode):
                    if is_deepseek:
                        adapter, state = self.adapter()
                        transport = state.factory
                    else:
                        transport = Mock(side_effect=AssertionError("No HTTP handoff"))
                        adapter = module._create_provider(ProviderConfig.from_base_url(
                            "https://sdk-probe.invalid/v1", "gpt-4o-mini", 1), MemoryKey(fixture.KEY),
                            sdk_loader=lambda: self.sdk, transport_factory=transport)
                        self.addAsyncCleanup(adapter.aclose)
                    content = "Untrusted prior AI interpretation:\n" + origin.text
                    start = len(content)-len(origin.text)
                    value = fixture.envelope(mode=mode,
                        messages=[{"role": "system", "content": "Use fresh verified evidence."},
                                  {"role": "user", "content": content}],
                        answerSchema=fixture.final_schema() if mode == "final" else None,
                        interpretationTags=[{"pointer": "/messages/1/content", "start": start,
                                             "end": len(content), "originId": origin.origin_id}])
                    try:
                        prepared = await adapter.prepare(deepseek.draft(value))
                    except module.SDKAdapterError:
                        self.fail("Real SDK cannot yet map the private interpretation origin")
                    occurrence = prepared.interpretation_occurrences[0]
                    expected = 2 if is_deepseek and mode == "final" else 1
                    self.assertEqual(occurrence.pointer, f"/messages/{expected}/content")
                    actual = json.loads(prepared.body)["messages"][expected]["content"]
                    self.assertEqual(actual[occurrence.start:occurrence.end], origin.text)
                    self.assertNotIn(b"interpretationTags", prepared.body)
                    self.assertNotIn(b"originId", prepared.body)
                    if is_deepseek:
                        profiles = importlib.import_module(deepseek.ADMISSION)._trusted_input_profiles()
                        reservation = inspect_wire(prepared, trusted_profiles=profiles,
                                                   trusted_interpretations=(origin,)).reservation
                        self.assertEqual(reservation.derived_characters, len(origin.text))
                        self.assertEqual(reservation.derived_files, ("file0.py", "file1.py"))
                    await adapter.aclose()
                    transport.assert_not_called()
                    if is_deepseek:
                        self.assertTrue(all(client.is_closed for client in state.clients))
