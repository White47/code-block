"""Base-only desktop exposes a closed AI boundary without SDK or source work."""

import inspect
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from codeviz.persistence.store import CacheStore
from codeviz.ui.desktop import _API_ARITIES, _API_METHODS
from codeviz.ui.web_bridge import DesktopBridge


class AssistantBridgeBoundaryTests(unittest.TestCase):
    def test_base_only_methods_are_allowed_but_fail_closed_without_project_lock(self):
        methods = {
            "assistant_get_settings": None,
            "assistant_configure": {"baseUrl": "https://provider.invalid", "model": "fake", "key": None, "enabled": True},
            "assistant_test_connection": {"configRevision": 0, "requestId": "request"},
            "assistant_get_state": {"projectEpoch": "epoch"},
            "assistant_grant_consent": {"projectEpoch": "epoch", "analysisGeneration": 1,
                                        "configRevision": 0, "allowedFiles": [], "confirmed": True},
            "assistant_revoke_consent": {"projectEpoch": "epoch"},
            "assistant_new_conversation": {"projectEpoch": "epoch"},
            "assistant_clear_conversation": {"projectEpoch": "epoch", "conversationId": "chat"},
            "assistant_start": {"projectEpoch": "epoch", "analysisGeneration": 1, "conversationId": "chat",
                                "requestId": "request", "configRevision": 0, "consentRevision": 0,
                                "question": "Explain", "context": {"kind": "question"}},
            "assistant_poll": {"jobId": "job", "projectEpoch": "epoch", "afterSeq": 0},
            "assistant_cancel": {"jobId": "job", "projectEpoch": "epoch"},
            "assistant_resolve_evidence": {"projectEpoch": "epoch", "analysisGeneration": 1,
                                          "conversationId": "chat", "evidenceId": "evidence", "action": "source"},
        }
        with TemporaryDirectory() as directory:
            bridge = DesktopBridge(cache_store=CacheStore(Path(directory)))
            for name in methods:
                self.assertIn(name, _API_METHODS, "Missing native assistant allowlist entry")
                expected_arity = 0 if name == "assistant_get_settings" else 1
                self.assertEqual(_API_ARITIES[name], (expected_arity, expected_arity))
                self.assertEqual(len(inspect.signature(getattr(bridge, name)).parameters), expected_arity)
            with patch.object(bridge, "_invoke", side_effect=AssertionError("No project write queue")), \
                    patch.object(Path, "open", side_effect=AssertionError("No source/config file reads")), \
                    patch("codeviz.ui.web_bridge.logger.exception") as logger:
                settings = bridge.assistant_get_settings()
                self.assertTrue(settings["ok"])
                self.assertFalse(settings["data"]["available"])
                self.assertFalse(settings["data"]["keySet"])
                for name, payload in methods.items():
                    if payload is None:
                        continue
                    response = getattr(bridge, name)(payload)
                    self.assertFalse(response["ok"])
                    self.assertEqual(response["error"]["code"], "AI_DEPENDENCY_MISSING")
                response = bridge.assistant_start(methods["assistant_start"] | {"sourceText": "forbidden"})
                self.assertEqual(response["error"]["code"], "AI_INVALID_INPUT")
                logger.assert_not_called()
