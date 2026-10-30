"""Fixed GUI-free frozen SDK check: inert credentials, owned fake HTTP only."""
import asyncio
import json
import runpy
import sys
from pathlib import Path
from unittest.mock import patch


class ProbeFailure(ValueError):
    pass


def _write_windows(payload):
    if sys.platform != 'win32':
        raise OSError()
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetStdHandle.argtypes = [wintypes.DWORD]
    kernel.GetStdHandle.restype = wintypes.HANDLE
    kernel.WriteFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                                ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    kernel.WriteFile.restype = wintypes.BOOL
    handle = kernel.GetStdHandle(-11)
    if not handle or handle == ctypes.c_void_p(-1).value:
        raise OSError()
    data = ctypes.create_string_buffer(payload)
    written = wintypes.DWORD()
    if not kernel.WriteFile(handle, data, len(payload), ctypes.byref(written), None) or written.value != len(payload):
        raise OSError()


def _emit(report):
    payload = json.dumps(report) + '\n'
    if sys.stdout is None:
        _write_windows(payload.encode('utf-8'))
    else:
        sys.stdout.write(payload)
        sys.stdout.flush()


def _kind(error):
    name = type(error).__name__
    allowed = {'RuntimeError', 'ValueError', 'TypeError', 'ImportError', 'ModuleNotFoundError',
               'FileNotFoundError', 'PermissionError', 'TimeoutError', 'SSLError',
               'SDKAdapterError', 'ProviderError', 'ProbeFailure', 'PackageNotFoundError',
               'AttributeError', 'KeyError', 'NameError'}
    return name if name in allowed else 'Error'


def _checkpoint(error, stage):
    if stage != 'tls-transport-init':
        return stage
    labels = {'ssl': 'system-tls-context', 'httpx2._config': 'http-client-tls',
              'truststore._windows': 'windows-trust-store',
              'truststore._api': 'system-trust-store', 'ctypes': 'native-tls-library',
              'httpcore2._async.connection_pool': 'http-connection-pool'}
    trace = error.__traceback__
    for _ in range(32):
        if trace is None:
            break
        module = trace.tb_frame.f_globals.get('__name__')
        stage = labels.get(module, stage)
        if module == 'ssl':
            stage = {'_load_windows_store_certs': 'windows-ca-enumeration',
                     'load_default_certs': 'system-ca-load',
                     '__new__': 'ssl-context-construction'}.get(trace.tb_frame.f_code.co_name, stage)
        trace = trace.tb_next
    return stage


def _helpers():
    if getattr(sys, 'frozen', False):
        import assistant_smoke_host
        helper = assistant_smoke_host._helper
    else:
        helper = runpy.run_path(str(Path(__file__).with_name('assistant_smoke_host.py')))['_helper']
    return helper('verify_assistant_dependencies'), helper('verify_assistant_production')


async def _close_transport(transport):
    await asyncio.wait_for(transport.aclose(), 2)


def main(argv=None):
    report = {'status': 'failed', 'fakeOnly': True, 'stage': 'arguments',
              'requests': 0, 'externalAttempts': 0, 'resourcesReleased': True}
    if (sys.argv[1:] if argv is None else argv) != ['--fake-only']:
        try:
            _emit(report)
        except OSError:
            pass
        return 1
    runtime = http = transport = None
    transport_closed = False
    attempts = []
    try:
        report['stage'] = 'helpers'
        dependencies, production = _helpers()
        with dependencies['offline_guard']() as attempts:
            try:
                report['stage'] = 'disabled-startup'
                from codeviz.assistant import _deepseek_admission as admission
                from codeviz.assistant import sdk_provider as sdk
                from codeviz.assistant.bridge_requests import (
                    ConnectionRequest,
                    parse_configure,
                )

                runtime = admission._create_application_runtime()
                settings = runtime.get_settings()
                if settings['enabled'] or settings['keySet'] or runtime._engine is not None:
                    raise ProbeFailure()
                report['stage'] = 'sdk-import'
                modules = sdk._load_sdk_modules()
                report['stage'] = 'tls-transport-init'
                transport = sdk._new_secure_transport(modules)
                asyncio.run(_close_transport(transport))
                transport_closed = True
                http = production['FixtureHTTP'](runtime, ())
                original_error = sdk._error

                def observed_error(error, boundary):
                    if 'sdkFailure' not in report:
                        check = 'sdk'
                        owned_checks = {'_capture': 'capture', '_deepseek_wire': 'wire',
                                        '_deepseek_attribution': 'attribution',
                                        '_handoff_check': 'handoff', '_draft': 'draft',
                                        '_inspect_wire_sources': 'source-proof'}
                        trace = error.__traceback__
                        for _ in range(32):
                            if trace is None:
                                break
                            if trace.tb_frame.f_globals.get('__name__') == 'codeviz.assistant.sdk_provider':
                                check = owned_checks.get(trace.tb_frame.f_code.co_name, check)
                            trace = trace.tb_next
                        report['sdkFailure'] = {'errorType': _kind(error), 'check': check}
                    return original_error(error, boundary)

                report['stage'] = 'source-free-connection'
                with patch.object(sdk, '_new_secure_transport', http.factory), patch.object(
                        sdk, '_error', observed_error):
                    runtime.configure(parse_configure({'baseUrl': 'https://api.deepseek.com/v1',
                        'model': 'deepseek-flash', 'key': 'offline-frozen-probe-only', 'enabled': True}))
                    started = runtime.test_connection(ConnectionRequest(1, 'fixed-offline-probe'))
                    result = production['_wait'](runtime, started['jobId'])
                    if result['status'] != 'Completed' or len(http.requests) != 2:
                        raise ProbeFailure()
                report['stage'] = 'cleanup'
            finally:
                if runtime is not None:
                    try:
                        runtime.shutdown()
                    except Exception:
                        report['resourcesReleased'] = False
                if transport is not None and not transport_closed:
                    try:
                        asyncio.run(_close_transport(transport))
                        transport_closed = True
                    except Exception:
                        report['resourcesReleased'] = False
                report['requests'] = 0 if http is None else len(http.requests)
                report['externalAttempts'] = len(attempts)
                report['resourcesReleased'] = (report['resourcesReleased']
                    and (transport is None or transport_closed) and (http is None or http.released()))
            if attempts or not report['resourcesReleased']:
                raise ProbeFailure()
        report.update(status='passed', stage='complete')
    except Exception as error:
        report['stage'] = _checkpoint(error, report['stage'])
        report['errorType'] = _kind(error)
    try:
        _emit(report)
    except OSError:
        return 1
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
