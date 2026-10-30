"""Fixed SDK native harness wiring; never a GUI acceptance substitute."""

import importlib
import importlib.util
import socket
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


class ProductionNativeTests(unittest.TestCase):
    def test_reverse_dns_entries_are_blocked_without_calling_system_resolvers(self):
        helper = importlib.import_module("scripts.verify_assistant_dependencies")
        for ports in ((), (9239,)):
            with patch.object(socket, "getnameinfo", return_value=("unsafe", "80")) as reverse, \
                    patch.object(socket, "gethostbyaddr", return_value=("unsafe", [], [])), \
                    patch.object(socket, "getfqdn", return_value="unsafe"), \
                    helper.offline_guard(native_loopback_ports=ports) as attempts:
                with self.assertRaises(AssertionError):
                    socket.getnameinfo(("203.0.113.1", 80), 0)
                with self.assertRaises(AssertionError):
                    socket.gethostbyaddr("203.0.113.1")
                with self.assertRaises(AssertionError):
                    socket.getfqdn("untrusted.invalid")
                reverse.assert_not_called()
                self.assertEqual(len(attempts), 3)

    @unittest.skipUnless(importlib.util.find_spec("pydantic_ai"), "Requires the pinned optional SDK")
    def test_owned_native_fixture_uses_the_real_sdk_and_closes_original_requests(self):
        from scripts import assistant_smoke_host as host
        from scripts import verify_assistant_dependencies as dependencies
        from scripts import verify_assistant_production as production

        from codeviz.assistant import sdk_provider
        from codeviz.assistant._deepseek_admission import _production_provider_factory
        from codeviz.assistant.bridge_requests import (
            ProjectRequest,
            parse_consent,
            parse_start,
        )

        with dependencies.offline_guard() as attempts, TemporaryDirectory() as directory:
            bridge, http = host._create_smoke_bridge(Path(directory), deepseek=True)
            self.addCleanup(bridge._finish_assistant_close)
            runtime = http.runtime
            self.assertIs(runtime._connection_provider_factory, _production_provider_factory)
            self.assertFalse(bridge.assistant_get_settings()["data"]["keySet"])
            with patch.object(sdk_provider, "_new_secure_transport", http.factory):
                self.assertTrue(bridge.assistant_configure({"baseUrl": "https://api.deepseek.com/v1",
                    "model": "deepseek-flash", "key": "synthetic-native-only", "enabled": True})["ok"])
                connection = bridge.assistant_test_connection({"configRevision": 1, "requestId": "owned-native"})
                self.assertTrue(connection["ok"])
                production._completed(production._wait(runtime, connection["data"]["jobId"]), "connection")
                ctx = bridge._controller.capture_assistant_context()
                chat = runtime.new_conversation(ProjectRequest(ctx.project_epoch), ctx)["conversationId"]
                grant = runtime.grant_consent(parse_consent({"projectEpoch": ctx.project_epoch,
                    "analysisGeneration": ctx.analysis_generation, "configRevision": 1,
                    "allowedFiles": ["sample.py"], "confirmed": True}), ctx)
                started = runtime.start(parse_start({"projectEpoch": ctx.project_epoch,
                    "analysisGeneration": ctx.analysis_generation, "configRevision": 1,
                    "conversationId": chat, "requestId": "owned-native-explain",
                    "consentRevision": grant["consentRevision"], "question": "Explain saved source.",
                    "context": {"kind": "source", "filePath": "sample.py", "mode": "file", "anchor": None}}), ctx)
                state = production._wait(runtime, started["jobId"], ctx.project_epoch)
                production._completed(state, "explanation")
                self.assertEqual(len(state["answer"]["sections"]), 7)
                self.assertTrue(state["answer"]["evidence"])
                bridge._finish_assistant_close()
            self.assertEqual(len(http.requests), 4)
            self.assertTrue(http.released())
            self.assertEqual(attempts, [])

    def test_fixed_mode_reaches_requested_artifact_without_public_provider_inputs(self):
        verifier = importlib.import_module("scripts.verify_assistant_native")
        args = verifier._parse_args(["--source", "--fake-only", "--deepseek"])
        with TemporaryDirectory() as directory:
            command = verifier._host_command(args, port=9239, output=Path(directory))
        self.assertIn("--deepseek", command)
        self.assertIn("--source-tree", command)
        self.assertNotIn("--endpoint", command)
        self.assertNotIn("--provider", command)

    def test_native_guard_allows_only_declared_owned_loopback_port(self):
        helper = importlib.import_module("scripts.verify_assistant_dependencies")
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            port = listener.getsockname()[1]
            with helper.offline_guard(native_loopback_ports=(port,)) as attempts:
                self.assertEqual(socket.getaddrinfo("localhost", port)[0][-1], ("127.0.0.1", port))
                with socket.socket() as client:
                    client.connect(("127.0.0.1", port))
                    peer, _ = listener.accept()
                    peer.close()
                with socket.socket() as client, self.assertRaises(AssertionError):
                    client.connect(("127.0.0.1", 9))
                with self.assertRaises(AssertionError):
                    socket.getaddrinfo("api.deepseek.com", 443)
                with socket.socket(type=socket.SOCK_DGRAM) as client, self.assertRaises(AssertionError):
                    client.connect(("127.0.0.1", port))
                left, right = socket.socketpair()
                left.close()
                right.close()
                self.assertEqual(len(attempts), 3)


if __name__ == "__main__":
    unittest.main()
