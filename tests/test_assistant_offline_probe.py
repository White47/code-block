"""Fixed offline entrypoint contracts; never imports an optional SDK here."""
import contextlib
import io
import json
import runpy
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/assistant_offline_probe.py'


class AssistantOfflineProbeTests(unittest.TestCase):
    def module(self):
        self.assertTrue(SCRIPT.is_file(), 'Missing GUI-free offline probe')
        return runpy.run_path(str(SCRIPT))

    def test_frozen_entry_has_a_separate_fixed_offline_route(self):
        entry = (ROOT / 'scripts/desktop_entry.py').read_text(encoding='utf-8')
        self.assertIn('--_codeviz-assistant-offline-probe', entry)

    def test_extra_arguments_are_rejected_without_helpers_or_echo(self):
        module = self.module()
        for args in ([], ['--fake-only', '--key', 'SENSITIVE-ARG'], ['--fake-only', '--project', 'SENSITIVE-PATH']):
            with self.subTest(count=len(args)), patch.dict(module['main'].__globals__,
                    {'_helpers': unittest.mock.Mock(side_effect=AssertionError('helpers must stay unloaded'))}):
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    self.assertEqual(module['main'](args), 1)
                result = json.loads(output.getvalue())
                self.assertEqual(result['stage'], 'arguments')
                self.assertNotIn('SENSITIVE', output.getvalue())

    def test_helper_error_text_is_never_in_the_report(self):
        module = self.module()
        output = io.StringIO()
        with patch.dict(module['main'].__globals__, {'_helpers': unittest.mock.Mock(
                side_effect=RuntimeError('SENSITIVE-ERROR'))}), contextlib.redirect_stdout(output):
            self.assertEqual(module['main'](['--fake-only']), 1)
        result = json.loads(output.getvalue())
        self.assertEqual(result['errorType'], 'RuntimeError')
        self.assertNotIn('SENSITIVE', output.getvalue())

    def test_unknown_exception_names_are_not_serialized(self):
        module = self.module()
        error = type('SENSITIVE-TYPE', (Exception,), {})()
        self.assertEqual(module['_kind'](error), 'Error')

    def test_windowed_probe_writes_only_its_fixed_report_to_the_inherited_handle(self):
        module = self.module()
        self.assertTrue(callable(module.get('_emit')), 'Windowed probes need a receipt writer')
        writer = unittest.mock.Mock()
        with patch.dict(module['_emit'].__globals__, {'_write_windows': writer}), patch('sys.stdout', None):
            module['_emit']({'status': 'failed', 'stage': 'arguments'})
        payload = writer.call_args.args[0]
        self.assertEqual(json.loads(payload), {'status': 'failed', 'stage': 'arguments'})

    def test_tls_checkpoint_uses_only_a_fixed_module_label(self):
        module = self.module()
        self.assertTrue(callable(module.get('_checkpoint')), 'TLS failures need a bounded checkpoint')
        namespace = {'__name__': 'truststore._windows'}
        try:
            exec(compile("raise FileNotFoundError('SENSITIVE-PATH')", '<owned fixture>', 'exec'), namespace)
        except FileNotFoundError as error:
            self.assertEqual(module['_checkpoint'](error, 'tls-transport-init'), 'windows-trust-store')

    def test_windows_certificate_checkpoint_does_not_include_the_missing_filename(self):
        module = self.module()
        namespace = {'__name__': 'ssl'}
        exec(compile("def _load_windows_store_certs():\n raise FileNotFoundError('SENSITIVE-PATH')", '<owned fixture>', 'exec'), namespace)
        try:
            namespace['_load_windows_store_certs']()
        except FileNotFoundError as error:
            self.assertEqual(module['_checkpoint'](error, 'tls-transport-init'), 'windows-ca-enumeration')

    def test_shutdown_failure_marks_cleanup_failed_and_still_closes_transport(self):
        from codeviz.assistant import _deepseek_admission as admission
        from codeviz.assistant import sdk_provider as sdk

        module = self.module()
        runtime = Mock(_engine=None)
        runtime.get_settings.return_value = {'enabled': False, 'keySet': False}
        runtime.shutdown.side_effect = RuntimeError('SENSITIVE-SHUTDOWN')
        close = AsyncMock(side_effect=[RuntimeError('SENSITIVE-CLOSE'), None])
        helpers = ({'offline_guard': lambda: contextlib.nullcontext([])}, {})
        output = io.StringIO()
        with patch.dict(module['main'].__globals__, {'_helpers': Mock(return_value=helpers),
                '_close_transport': close}), patch.object(admission, '_create_application_runtime',
                return_value=runtime), patch.object(sdk, '_load_sdk_modules', return_value={}), \
                patch.object(sdk, '_new_secure_transport', return_value=object()), \
                contextlib.redirect_stdout(output):
            self.assertEqual(module['main'](['--fake-only']), 1)
        report = json.loads(output.getvalue())
        self.assertFalse(report['resourcesReleased'])
        self.assertEqual(close.await_count, 2)
        runtime.shutdown.assert_called_once()
        self.assertNotIn('SENSITIVE', output.getvalue())


if __name__ == '__main__':
    unittest.main()
