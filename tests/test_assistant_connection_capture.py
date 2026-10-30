"""Synthetic SDK capture is not proof of hidden model framing or a live API."""

import importlib
import json
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import Mock

import test_assistant_sdk_provider as fixture

from codeviz.assistant.connection_profile import connection_draft, connection_schema
from codeviz.assistant.provider import MemoryKey
from codeviz.assistant.sdk_provider import _create_provider
from codeviz.assistant.service import ServiceError, _wire_reservation


async def assert_capture(case, sdk_loader=None):
    from codeviz.assistant.config import ProviderConfig

    config = ProviderConfig.from_base_url("https://provider.invalid/v1", "p", 1)
    total_bytes = 0
    for stage in ("tools", "final"):
        transport = Mock(side_effect=AssertionError("No transport before verified reservation"))
        options = {} if sdk_loader is None else {"sdk_loader": sdk_loader}
        adapter = _create_provider(config, MemoryKey("test-only"), transport_factory=transport, **options)
        try:
            prepared = await adapter.prepare(connection_draft(stage, deadline=time.monotonic() + 15))
            wire = json.loads(prepared.body)
            total_bytes += len(prepared.body)
            case.assertEqual(len(wire["messages"]), 1)
            if stage == "tools":
                case.assertEqual(wire["tools"][0]["function"]["name"], "p")
                case.assertEqual(wire["tools"][0]["function"]["parameters"], connection_schema())
            else:
                case.assertEqual(wire["response_format"]["json_schema"], {
                    "name": "p", "strict": True, "schema": connection_schema()})
            case.assertIsNone(prepared.input_token_upper_bound)
            case.assertFalse(prepared.attribution_complete)
            case.assertEqual(prepared.source_occurrences, ())
            case.assertEqual(prepared.metadata_occurrences, ())
            with case.assertRaises(ServiceError):
                _wire_reservation(prepared)
            transport.assert_not_called()
        finally:
            await adapter.aclose()
    case.assertLessEqual(total_bytes, 1024)  # Captured bytes ONLY, not a framing/token proof.


class ConnectionFakeCaptureTests(unittest.IsolatedAsyncioTestCase):
    async def test_fixed_profiles_capture_without_transport_and_remain_fail_closed(self):
        fake = fixture.FakeSDK()
        with fixture.offline_guard() as attempts:
            await assert_capture(self, lambda: fake.modules)
        self.assertEqual(attempts, [])


class ConnectionPinnedCaptureTests(unittest.IsolatedAsyncioTestCase):
    async def test_pinned_sdk_profile_wire_is_small_but_framing_is_still_unproven(self):
        if Path(sys.prefix).resolve() != fixture.SDK_ENV.resolve():
            self.skipTest("Pinned SDK capture needs the explicit isolated interpreter; not acceptance")
        with fixture.offline_guard() as attempts:
            # Explicitly use the installed pinned SDK rather than the fake encoder.
            modules = {key: importlib.import_module(name) for key, name in fixture.SDK_MODULES.items()}
            await assert_capture(self, lambda: modules)
        self.assertEqual(attempts, [])
