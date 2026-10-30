"""A private offline profile must not enable the application or load its SDK."""

import subprocess
import sys
import unittest
from pathlib import Path
from textwrap import dedent


class DeepSeekImportBoundaryTests(unittest.TestCase):
    def test_private_admission_import_keeps_production_closed_without_sdk(self):
        root = Path(__file__).resolve().parents[1]
        probe = dedent("""
            import importlib
            import sys
            from unittest.mock import patch
            sys.path.insert(0, 'src')
            blocked = ('openai', 'pydantic_ai', 'pydantic', 'httpx2', 'httpcore2')
            original_import = __import__
            def no_sdk(name, *args, **kwargs):
                if any(name == prefix or name.startswith(prefix + '.') for prefix in blocked):
                    raise AssertionError('Optional SDK must remain unloaded')
                return original_import(name, *args, **kwargs)
            with patch('builtins.__import__', side_effect=no_sdk), \\
                    patch('socket.socket.connect', side_effect=AssertionError('No network')), \\
                    patch('socket.socket.connect_ex', side_effect=AssertionError('No network')):
                importlib.import_module('codeviz.assistant._deepseek_admission')
                from codeviz.assistant.bridge_requests import ConnectionRequest, parse_configure
                from codeviz.assistant.service import ServiceError, _AssistantRuntime
                runtime = _AssistantRuntime()
                try:
                    runtime.configure(parse_configure({
                        'baseUrl': 'https://api.deepseek.com/v1', 'model': 'deepseek-flash',
                        'enabled': True, 'key': 'offline-test-only',
                    }))
                    settings = runtime.get_settings()
                    assert settings['enabled'] is True
                    # available describes the settings owner, not dispatch admission.
                    try:
                        runtime.test_connection(ConnectionRequest(settings['configRevision'], 'offline-probe'))
                    except ServiceError as error:
                        assert error.code == 'AI_DEPENDENCY_MISSING'
                    else:
                        raise AssertionError('Unregistered production profile must not dispatch')
                    assert runtime._engine is None
                    assert not any(name == prefix or name.startswith(prefix + '.')
                                   for name in sys.modules for prefix in blocked)
                finally:
                    runtime.shutdown()
            """)
        result = subprocess.run([sys.executable, '-B', '-c', probe], cwd=root,
                                capture_output=True, text=True, timeout=15, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
