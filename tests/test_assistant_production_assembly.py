"""Closed application-owned DeepSeek assembly; all acceptance stays offline."""

import importlib
import sys
import time
import unittest
from dataclasses import FrozenInstanceError
from unittest.mock import Mock, patch

from codeviz.assistant.bridge_requests import (
    ConnectionRequest,
    PollRequest,
    parse_configure,
)
from codeviz.assistant.config import ProviderConfig, ProviderError
from codeviz.assistant.provider import MemoryKey
from codeviz.assistant.service import _AssistantRuntime


class ProductionAssemblyTests(unittest.TestCase):
    def test_application_runtime_owns_fresh_closed_registration_without_loading_sdk(self):
        blocked = ("openai", "pydantic_ai", "httpx2", "httpcore2")
        loaded_before = {name for name in sys.modules
                         if any(name == prefix or name.startswith(prefix + ".")
                                for prefix in blocked)}
        admission = importlib.import_module("codeviz.assistant._deepseek_admission")

        first_input = admission._trusted_input_profiles()
        second_input = admission._trusted_input_profiles()
        first_connection = admission._trusted_connection_profiles()
        second_connection = admission._trusted_connection_profiles()
        runtime = admission._create_application_runtime()
        bare = _AssistantRuntime()
        self.addCleanup(runtime.shutdown)
        self.addCleanup(bare.shutdown)

        self.assertEqual(first_input, second_input)
        self.assertEqual(first_connection, second_connection)
        self.assertIsNot(first_input, second_input)
        self.assertIsNot(first_input[0], second_input[0])
        self.assertIsNot(first_connection, second_connection)
        self.assertIsNot(first_connection[0], second_connection[0])
        with self.assertRaises(FrozenInstanceError):
            first_input[0].model = "other"
        with self.assertRaises(FrozenInstanceError):
            first_connection[0].model = "other"
        self.assertEqual(runtime._trusted_input_profiles, first_input)
        self.assertEqual(runtime._trusted_connection_profiles, first_connection)
        self.assertIs(runtime._connection_provider_factory, admission._production_provider_factory)
        self.assertEqual(bare._trusted_input_profiles, ())
        self.assertEqual(bare._trusted_connection_profiles, ())
        self.assertIsNone(bare._connection_provider_factory)
        self.assertEqual(runtime.get_settings()["enabled"], False)
        self.assertEqual(runtime.get_settings()["keySet"], False)
        self.assertIsNone(runtime._engine)
        loaded_after = {name for name in sys.modules
                        if any(name == prefix or name.startswith(prefix + ".")
                               for prefix in blocked)}
        self.assertEqual(loaded_after, loaded_before)

    def test_production_factory_refuses_unknown_target_before_sdk_loading(self):
        admission = importlib.import_module("codeviz.assistant._deepseek_admission")
        key = MemoryKey("offline-test-only")
        loader = Mock(side_effect=AssertionError("optional SDK loaded"))
        transport = Mock(side_effect=AssertionError("transport constructed"))
        configs = (
            ProviderConfig.from_base_url("https://other.invalid/v1", "deepseek-flash", 1),
            ProviderConfig.from_base_url("https://api.deepseek.com/v1", "deepseek-reasoner", 1),
        )

        with patch("codeviz.assistant.sdk_provider._load_sdk_modules", loader), \
                patch("codeviz.assistant.sdk_provider._new_secure_transport", transport):
            for config in configs:
                with self.subTest(config=config), self.assertRaises(ProviderError) as caught:
                    admission._production_provider_factory(config, key)
                self.assertEqual(caught.exception.code, "AI_INVALID_INPUT")

        loader.assert_not_called()
        transport.assert_not_called()

    def test_requested_connection_reports_missing_sdk_without_transport(self):
        admission = importlib.import_module("codeviz.assistant._deepseek_admission")
        runtime = admission._create_application_runtime()
        self.addCleanup(runtime.shutdown)
        runtime.configure(parse_configure({
            "baseUrl": "https://api.deepseek.com/v1",
            "model": "deepseek-flash",
            "key": "offline-test-only",
            "enabled": True,
        }))
        sdk = importlib.import_module("codeviz.assistant.sdk_provider")
        transport = Mock(side_effect=AssertionError("transport constructed"))

        with patch.object(sdk.importlib, "import_module",
                          side_effect=ImportError("synthetic dependency detail")), \
                patch.object(sdk, "_new_secure_transport", transport):
            job_id = runtime.test_connection(ConnectionRequest(1, "missing-sdk"))["jobId"]
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                state = runtime.poll(PollRequest(job_id, None, 0))
                if state["resourcesReleased"]:
                    break
                time.sleep(0.005)
            else:
                self.fail(f"Connection lifecycle did not settle: {state}")

        self.assertEqual(state["status"], "Failed")
        self.assertEqual(state["issue"]["code"], "AI_DEPENDENCY_MISSING")
        self.assertNotIn("synthetic dependency detail", str(state))
        transport.assert_not_called()

    def test_desktop_bridge_uses_application_runtime_constructor(self):
        admission = importlib.import_module("codeviz.assistant._deepseek_admission")
        desktop = importlib.import_module("codeviz.ui.desktop")
        with patch.object(admission, "_create_application_runtime",
                          wraps=admission._create_application_runtime) as constructor:
            bridge = desktop._create_desktop_bridge()
        self.addCleanup(bridge._finish_assistant_close)

        constructor.assert_called_once_with()
        self.assertIs(bridge._assistant_runtime._connection_provider_factory,
                      admission._production_provider_factory)


if __name__ == "__main__":
    unittest.main()
