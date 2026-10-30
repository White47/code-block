"""Project permission candidates are a local projection, never an upload/grant."""
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from assistant_tool_fixtures import make_index_case
from test_assistant_runtime_conversations import context, publish

from codeviz.assistant import bridge_requests
from codeviz.assistant.service import ServiceError, _AssistantRuntime
from codeviz.assistant.snapshot import build_snapshot


class ConsentCandidatesTests(unittest.TestCase):
    def test_candidates_exclude_sensitive_unparsed_hashless_and_ignored_files(self):
        case = make_index_case()
        source = case.analysis.files[0]
        extras = (replace(source, relative_path='secrets.py', module_name='secrets'),
                  replace(source, relative_path='hashless.py', content_hash=None),
                  replace(source, relative_path='unparsed.py', parsed_success=False),
                  replace(source, relative_path='ignored.py'))
        snapshot = build_snapshot(replace(case.analysis, files=case.analysis.files + extras),
            binding=replace(case.snapshot.binding, project_root=str(Path(case.snapshot.binding.project_root)),
                            ignore_paths=('ignored.py',)))
        runtime = _AssistantRuntime()
        self.addCleanup(runtime.shutdown)
        ctx = context(snapshot=snapshot, epoch=snapshot.binding.project_epoch, root=Path(snapshot.binding.project_root))
        publish(runtime, ctx)
        request = bridge_requests.parse_consent_candidates({'projectEpoch': ctx.project_epoch, 'analysisGeneration': 1})
        self.assertEqual(runtime.get_consent_candidates(request, ctx)['files'], ['app.py', 'denied.py'])

    def test_native_candidate_extension_fails_closed_without_runtime(self):
        from codeviz.ui.desktop import _API_ARITIES, _API_METHODS
        from codeviz.ui.web_bridge import DesktopBridge

        self.assertIn('assistant_get_consent_candidates', _API_METHODS)
        self.assertEqual(_API_ARITIES['assistant_get_consent_candidates'], (1, 1))
        result = DesktopBridge().assistant_get_consent_candidates({'projectEpoch': 'epoch', 'analysisGeneration': 1})
        self.assertFalse(result['ok'])
        self.assertEqual(result['error']['code'], 'AI_DEPENDENCY_MISSING')

    def test_current_candidates_are_read_only_and_old_generations_are_rejected(self):
        case = make_index_case()
        snapshot = build_snapshot(case.analysis, binding=replace(case.snapshot.binding,
            project_root=str(Path(case.snapshot.binding.project_root))))
        runtime = _AssistantRuntime()
        self.addCleanup(runtime.shutdown)
        ctx = context(snapshot=snapshot, epoch=snapshot.binding.project_epoch,
                      root=Path(snapshot.binding.project_root))
        publish(runtime, ctx)
        method = getattr(runtime, 'get_consent_candidates', None)
        self.assertTrue(callable(method), 'Missing project consent candidates')
        parser = getattr(bridge_requests, 'parse_consent_candidates', None)
        self.assertTrue(callable(parser))
        request = parser({'projectEpoch': ctx.project_epoch, 'analysisGeneration': ctx.analysis_generation})
        with patch.object(Path, 'open', side_effect=AssertionError('source read')), \
                patch('codeviz.assistant.service.AssistantService', side_effect=AssertionError('worker')):
            self.assertEqual(method(request, ctx), {'projectEpoch': ctx.project_epoch,
                'analysisGeneration': ctx.analysis_generation, 'files': ['app.py', 'denied.py']})
        self.assertIsNone(runtime._source_consent)
        self.assertIsNone(runtime._engine)
        with self.assertRaises(ServiceError):
            method(replace(request, analysis_generation=request.analysis_generation + 1), ctx)

    def test_request_rejects_extra_payload_and_invalid_generation(self):
        parser = getattr(bridge_requests, 'parse_consent_candidates', None)
        self.assertTrue(callable(parser), 'Missing closed candidate request')
        for payload in ({'projectEpoch': 'epoch', 'analysisGeneration': 0},
                        {'projectEpoch': 'epoch', 'analysisGeneration': 1, 'sourceText': 'forbidden'}):
            with self.subTest(payload_keys=sorted(payload)), self.assertRaises(bridge_requests.BridgeRequestError):
                parser(payload)
