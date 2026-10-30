"""Memory-only composition never activates optional providers from settings."""

import unittest
from threading import Event, Thread
from types import SimpleNamespace
from unittest.mock import patch

from codeviz.assistant import service
from codeviz.assistant.bridge_requests import parse_configure


class RuntimeSettingsTests(unittest.TestCase):
    def test_settings_replacement_is_memory_only_and_never_returns_key(self):
        self.assertTrue(hasattr(service, "_AssistantRuntime"), "Missing runtime composition")
        with patch("codeviz.assistant.provider.create_provider", side_effect=AssertionError("No SDK")), \
                patch.object(service, "AssistantService", side_effect=AssertionError("No worker")):
            runtime = service._AssistantRuntime()
            self.assertEqual(runtime.get_settings()["configRevision"], 0)
            first = runtime.configure(parse_configure({
                "baseUrl": "https://example.invalid/v1", "model": "demo",
                "key": "test-only-memory-value", "enabled": True}))
            self.assertEqual(first["configRevision"], 1)
            self.assertTrue(first["keySet"])
            self.assertNotIn("test-only-memory-value", repr(first))
            unchanged = runtime.configure(parse_configure({
                "baseUrl": "https://example.invalid/v1", "model": "demo",
                "key": None, "enabled": True}))
            self.assertEqual(unchanged, first)
            with self.assertRaises(service.ServiceError):
                runtime.configure(parse_configure({"baseUrl": "https://other.invalid/v1",
                    "model": "demo", "key": None, "enabled": True}))
            self.assertEqual(runtime.get_settings(), first)
            replaced = runtime.configure(parse_configure({
                "baseUrl": "https://other.invalid/v1", "model": "demo",
                "key": "", "enabled": False}))
            self.assertEqual(replaced["configRevision"], 2)
            self.assertFalse(replaced["keySet"])
            self.assertFalse(replaced["enabled"])
            self.assertIsNone(runtime._engine)

    def test_native_prepare_drops_credentials_but_failed_destroy_allows_reconfiguration(self):
        self.assertTrue(hasattr(service, "_AssistantRuntime"), "Missing runtime composition")
        runtime = service._AssistantRuntime()
        runtime.configure(parse_configure({"baseUrl": "https://example.invalid",
            "model": "demo", "key": "test-only", "enabled": True}))
        runtime.prepare_close()
        self.assertFalse(runtime.get_settings()["keySet"])
        self.assertFalse(runtime.get_settings()["enabled"])
        runtime.configure(parse_configure({"baseUrl": "https://example.invalid",
            "model": "demo", "key": "replacement-test-only", "enabled": True}))
        self.assertTrue(runtime.get_settings()["keySet"])
        runtime.shutdown()
        runtime.shutdown()
        self.assertFalse(runtime.get_settings()["keySet"])
        with self.assertRaises(service.ServiceError):
            runtime.configure(parse_configure({"baseUrl": "https://example.invalid",
                "model": "demo", "key": "", "enabled": False}))

    def test_final_shutdown_refuses_concurrent_reconfiguration(self):
        runtime = service._AssistantRuntime()
        entered, release, finished = Event(), Event(), Event()

        def shutdown():
            entered.set()
            release.wait(2)

        runtime._engine = SimpleNamespace(_replace_runtime_authority=lambda *_: None,
            _prepare_close=lambda: None, _drain_pending_closures=lambda: None, shutdown=shutdown)
        worker = Thread(target=lambda: (runtime.shutdown(), finished.set()))
        worker.start()
        try:
            self.assertTrue(entered.wait(1))
            with self.assertRaises(service.ServiceError):
                runtime.configure(parse_configure({"baseUrl": "https://example.invalid",
                    "model": "demo", "key": "test-only", "enabled": True}))
        finally:
            release.set()
            worker.join(2)
        self.assertTrue(finished.is_set())
        self.assertFalse(runtime.get_settings()["keySet"])
