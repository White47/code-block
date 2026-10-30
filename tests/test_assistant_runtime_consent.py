"""Runtime grants bind the current immutable publication, without reading source."""

import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from assistant_tool_fixtures import index_case_with, make_index_case
from test_assistant_runtime_conversations import context, publish

from codeviz.assistant.bridge_requests import (
    ConsentRequest,
    ProjectRequest,
    parse_configure,
)
from codeviz.assistant.service import ServiceError, _AssistantRuntime
from codeviz.assistant.snapshot import build_snapshot


class RuntimeConsentTests(unittest.TestCase):
    def configured(self, case=None):
        case = case or make_index_case()
        snapshot = build_snapshot(case.analysis, binding=replace(case.snapshot.binding,
            project_root=str(Path(case.snapshot.binding.project_root))))
        runtime = _AssistantRuntime()
        self.addCleanup(runtime.shutdown)
        ctx = context(snapshot=snapshot, epoch=snapshot.binding.project_epoch,
                      root=Path(snapshot.binding.project_root))
        publish(runtime, ctx)
        runtime.configure(parse_configure({"baseUrl": "https://provider.invalid/v1", "model": "probe-model",
            "key": "offline-test-only", "enabled": True}))
        return runtime, ctx

    def test_explicit_grant_uses_exact_snapshot_without_starting_worker(self):
        runtime, ctx = self.configured()
        with patch("codeviz.assistant.service.AssistantService", side_effect=AssertionError("worker")), \
                patch("codeviz.assistant.provider.create_provider", side_effect=AssertionError("SDK")), \
                patch.object(Path, "open", side_effect=AssertionError("source")):
            receipt = runtime.grant_consent(ConsentRequest(ctx.project_epoch, 1, 1, ("app.py",)), ctx)
            self.assertEqual(receipt, {"consentRevision": 1, "origin": "https://provider.invalid:443",
                "allowedFiles": ["app.py"], "enabled": True})
            self.assertEqual(runtime.get_state(ProjectRequest(ctx.project_epoch), ctx)["grant"], receipt)
            self.assertIsNone(runtime._engine)
            grant = runtime._consents.get(project_key=ctx.project_key, origin=receipt["origin"])
            self.assertTrue(runtime._consents.is_current(grant, config=runtime._config))

    def test_unparsed_hashless_and_unlisted_files_never_create_permission(self):
        def hashless(analysis):
            return replace(analysis, files=tuple(replace(file, content_hash=None) for file in analysis.files))

        for case, path in ((make_index_case(), "missing.py"),
                           (index_case_with(hashless), "app.py")):
            with self.subTest(path=path, hash=case.snapshot.file_inventory[0].content_hash):
                runtime, ctx = self.configured(case)
                with self.assertRaises(ServiceError):
                    runtime.grant_consent(ConsentRequest(ctx.project_epoch, 1, 1, (path,)), ctx)
                self.assertIsNone(runtime._consents.get(project_key=ctx.project_key,
                                                       origin=runtime._config.origin))

    def test_stale_config_and_publication_are_rejected_without_replacing_grant(self):
        runtime, ctx = self.configured()
        request = ConsentRequest(ctx.project_epoch, 1, 1, ("app.py",))
        receipt = runtime.grant_consent(request, ctx)
        with self.assertRaises(ServiceError):
            runtime.grant_consent(replace(request, config_revision=0), ctx)
        current = replace(ctx, publication_revision=ctx.publication_revision + 1)
        publish(runtime, current)
        with self.assertRaises(ServiceError):
            runtime.grant_consent(request, ctx)
        self.assertEqual(runtime.get_state(ProjectRequest(current.project_epoch), current)["grant"], receipt)
