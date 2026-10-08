"""Editing desktop boundary tests; native doubles never start a GUI."""

import inspect
import json
import queue
import re
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

from codeviz.persistence.store import CacheStore
from codeviz.ui.desktop import (
    _API_ARITIES,
    _API_METHODS,
    _AssetApplication,
    create_window,
)
from codeviz.ui.web_bridge import DesktopBridge


class Event:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self

    def fire(self, *args):
        return [handler(*args) for handler in self.handlers]


class SourceEditingDesktopTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix="codeviz-edit-desktop-tests-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.index = self.root / "frontend" / "dist" / "index.html"
        self.index.parent.mkdir(parents=True)
        self.index.write_text("desktop", encoding="utf-8")
        self.bridge = DesktopBridge(cache_store=CacheStore(self.root / "cache"))
        core = SimpleNamespace(
            WebResourceRequested=Event(), FrameNavigationStarting=Event(),
            NewWindowRequested=Event(), Environment=Mock(),
            AddWebResourceRequestedFilter=Mock())
        control = SimpleNamespace(
            NavigationStarting=Event(), CoreWebView2InitializationCompleted=Event(),
            CoreWebView2=core)
        self.window = SimpleNamespace(
            events=SimpleNamespace(initialized=Event(), before_show=Event(),
                                   closing=Event(), before_load=Event(),
                                   loaded=Event(), closed=Event()),
            native=SimpleNamespace(browser=SimpleNamespace(webview=control)),
            real_url="http://127.0.0.1:12345/", create_file_dialog=Mock(return_value=None),
            expose=Mock(), run_js=Mock(return_value=None),
            evaluate_js=Mock(side_effect=AssertionError("Close dispatch must not use eval")),
            destroy=Mock())
        self.webview = SimpleNamespace(
            util=SimpleNamespace(js_bridge_call=Mock()), FileDialog=SimpleNamespace(FOLDER=20),
            http=SimpleNamespace(BottleServer=SimpleNamespace(start_server=Mock())),
            create_window=Mock(return_value=self.window))
        self.addCleanup(self.window.events.closed.fire)

    def capture_close_requests(self):
        requests = queue.Queue()

        def dispatch(script):
            self.assertIn("window.__codevizCloseReady", script)
            self.assertIn("codeviz:close-request", script)
            match = re.search(r'detail:\s*(\{"requestId"\s*:\s*"[^"\\]+"\})', script)
            self.assertIsNotNone(match, "Close request payload must contain JSON requestId")
            payload = json.loads(match.group(1))
            self.assertEqual(set(payload), {"requestId"})
            self.assertIsInstance(payload["requestId"], str)
            self.assertTrue(payload["requestId"])
            requests.put(payload["requestId"])
            return None

        self.window.run_js.side_effect = dispatch
        return requests

    def request_close(self, requests):
        self.assertIn(False, self.window.events.closing.fire())
        try:
            return requests.get(timeout=2)
        except queue.Empty:
            self.fail("Ready renderer received no codeviz:close-request through run_js")

    def assert_close_success(self, response):
        self.assertTrue(response["ok"], response)

    def assert_close_rejected(self, response):
        self.assertFalse(response["ok"], response)

    def bind(self):
        return create_window(self.webview, bridge=self.bridge, index_path=self.index)

    def test_approved_methods_are_exposed_with_exact_arity(self):
        expected = {"open_source_document": 2, "save_source_document": 1,
                    "approve_close": 1, "cancel_close": 1}
        for name, count in expected.items():
            with self.subTest(method=name):
                self.assertIn(name, _API_METHODS)
                self.assertEqual(_API_ARITIES[name], (count, count))
                method = getattr(DesktopBridge, name, None)
                self.assertTrue(callable(method), f"Approved editing API missing: {name}")
                inspect.signature(method).bind(None, *([None] * count))
        self.bind()
        self.assertIsNone(self.webview.create_window.call_args.kwargs["js_api"])
        self.assertEqual({method.__name__ for method in self.window.expose.call_args.args},
                         set(_API_METHODS))

    def test_worker_csp_is_same_origin_only(self):
        response = Mock()
        _AssetApplication(self.index)({"PATH_INFO": "/", "REQUEST_METHOD": "GET"}, response)
        csp = dict(response.call_args.args[1])["Content-Security-Policy"]
        directives = {part.strip().split()[0]: part.strip().split()[1:]
                      for part in csp.split(";") if part.strip()}
        self.assertEqual(directives["worker-src"], ["'self'"])
        self.assertEqual(directives["script-src"], ["'self'"])
        self.assertEqual(directives["frame-src"], ["'none'"])
        self.assertNotIn("blob:", csp)
        self.assertNotIn("unsafe-eval", csp)
        self.assertNotIn("https:", csp)

    def test_dispatch_rejects_wrong_editing_arity_and_forged_callback(self):
        expected = {"open_source_document": 2, "save_source_document": 1,
                    "approve_close": 1, "cancel_close": 1}
        self.bind()
        guard = self.webview.util.js_bridge_call
        for name, count in expected.items():
            with self.subTest(method=name):
                self.assertIn(name, _API_METHODS)
                original = guard._original
                original.reset_mock()
                guard(self.window, name, [None] * (count - 1), "token")
                guard(self.window, name, [None] * (count + 1), "token")
                guard(self.window, name, [None] * count, 'x\"];alert(1);//')
                original.assert_not_called()
                guard(self.window, name, [None] * count, "valid_token")
                original.assert_called_once_with(
                    self.window, name, [None] * count, "valid_token")

    def test_native_close_without_ready_frontend_fails_closed(self):
        self.bind()
        self.assertTrue(self.window.events.closing.handlers, "Native close veto missing")
        self.assertIn(False, self.window.events.closing.fire())
        self.window.destroy.assert_not_called()

    def test_native_close_veto_returns_before_blocked_javascript(self):
        entered = threading.Event()
        release = threading.Event()
        dispatch_threads = []

        def blocked_dispatch(*args, **kwargs):
            dispatch_threads.append(threading.get_ident())
            entered.set()
            release.wait(2)
            return None

        self.window.run_js.side_effect = blocked_dispatch
        self.bind()
        self.window.events.loaded.fire()
        self.assertTrue(self.window.events.closing.handlers, "Native close veto missing")
        try:
            started = time.monotonic()
            results = self.window.events.closing.fire()
            elapsed = time.monotonic() - started
            self.assertIn(False, results)
            self.assertLess(elapsed, 0.5, "Close callback blocks native UI thread")
            self.assertTrue(entered.wait(1), "Ready renderer did not receive close request")
            self.assertTrue(all(identifier != threading.get_ident() for identifier in dispatch_threads))
            self.window.destroy.assert_not_called()
        finally:
            release.set()

    def test_forged_approval_cannot_destroy_window(self):
        self.bind()
        for name in ("approve_close", "cancel_close"):
            method = getattr(self.bridge, name, None)
            self.assertTrue(callable(method), f"Approved close API missing: {name}")
            for token in ("forged_nonce", "", None, [], "x" * 100_000):
                with self.subTest(method=name, token_type=type(token).__name__):
                    response = method(token)
                    self.assertFalse(response["ok"], response)
        self.window.destroy.assert_not_called()

    def test_failed_renderer_dispatch_does_not_close_window(self):
        dispatched = threading.Event()

        def unavailable(script):
            dispatched.set()
            raise RuntimeError("renderer unavailable")

        self.window.run_js.side_effect = unavailable
        self.bind()
        self.window.events.loaded.fire()
        self.assertTrue(self.window.events.closing.handlers, "Native close veto missing")
        self.assertIn(False, self.window.events.closing.fire())
        self.assertTrue(dispatched.wait(2))
        self.window.destroy.assert_not_called()

    def test_cancel_consumes_nonce_and_next_request_is_distinct(self):
        requests = self.capture_close_requests()
        self.bind()
        self.window.events.loaded.fire()
        first = self.request_close(requests)
        self.assert_close_success(self.bridge.cancel_close(first))
        self.assert_close_rejected(self.bridge.cancel_close(first))
        self.assert_close_rejected(self.bridge.approve_close(first))
        second = self.request_close(requests)
        self.assertNotEqual(first, second)
        self.assert_close_success(self.bridge.cancel_close(second))
        self.window.destroy.assert_not_called()

    def test_approval_destroys_off_caller_thread_and_permit_is_single_use(self):
        requests = self.capture_close_requests()
        destroyed = threading.Event()
        observations = []

        def destroy():
            observations.append((threading.get_ident(), self.window.events.closing.fire(),
                                 self.window.events.closing.fire()))
            self.window.events.closed.fire()
            destroyed.set()

        self.window.destroy.side_effect = destroy
        self.bind()
        self.window.events.loaded.fire()
        request = self.request_close(requests)
        caller = threading.get_ident()
        self.assert_close_success(self.bridge.approve_close(request))
        self.assertTrue(destroyed.wait(2))
        identifier, allowed, vetoed = observations[0]
        self.assertNotEqual(identifier, caller)
        self.assertEqual(allowed, [None])
        self.assertIn(False, vetoed)
        self.assert_close_rejected(self.bridge.approve_close(request))
        self.assert_close_rejected(self.bridge.cancel_close(request))
        self.assertEqual(self.window.destroy.call_count, 1)

    def test_loaded_rotates_epoch_and_rejects_old_nonce(self):
        requests = self.capture_close_requests()
        self.bind()
        self.window.events.loaded.fire()
        old = self.request_close(requests)
        self.window.events.loaded.fire()
        self.assert_close_rejected(self.bridge.approve_close(old))
        self.assert_close_rejected(self.bridge.cancel_close(old))
        fresh = self.request_close(requests)
        self.assertNotEqual(old, fresh)
        self.assert_close_success(self.bridge.cancel_close(fresh))
        self.window.destroy.assert_not_called()

    def test_before_load_revokes_nonce_and_vetoes_until_loaded(self):
        requests = self.capture_close_requests()
        self.bind()
        self.window.events.loaded.fire()
        old = self.request_close(requests)
        self.window.events.before_load.fire()
        self.assert_close_rejected(self.bridge.approve_close(old))
        self.assertIn(False, self.window.events.closing.fire())
        self.assertTrue(requests.empty())
        self.window.events.loaded.fire()
        fresh = self.request_close(requests)
        self.assert_close_success(self.bridge.cancel_close(fresh))
        self.window.destroy.assert_not_called()

    def test_closed_revokes_nonce_even_if_loaded_arrives_later(self):
        requests = self.capture_close_requests()
        self.bind()
        self.window.events.loaded.fire()
        request = self.request_close(requests)
        self.window.events.closed.fire()
        self.window.events.loaded.fire()
        self.assert_close_rejected(self.bridge.approve_close(request))
        self.assert_close_rejected(self.bridge.cancel_close(request))
        self.assertIn(False, self.window.events.closing.fire())
        self.assertTrue(requests.empty())
        self.window.destroy.assert_not_called()

    def test_pending_close_is_coalesced_without_issuing_second_nonce(self):
        requests = self.capture_close_requests()
        self.bind()
        self.window.events.loaded.fire()
        request = self.request_close(requests)
        for _ in range(5):
            self.assertIn(False, self.window.events.closing.fire())
        self.assertTrue(requests.empty())
        self.assertEqual(self.window.run_js.call_count, 1)
        self.assert_close_success(self.bridge.cancel_close(request))

    def test_nonce_from_another_window_cannot_approve_close(self):
        requests = self.capture_close_requests()
        self.bind()
        self.window.events.loaded.fire()
        request = self.request_close(requests)
        other = SimpleNamespace(**vars(self.window))
        other.events = SimpleNamespace(initialized=Event(), before_show=Event(), closing=Event(),
                                       before_load=Event(), loaded=Event(), closed=Event())
        other.run_js = Mock(return_value=None)
        other.evaluate_js = Mock(side_effect=AssertionError("Close dispatch must not use eval"))
        other.destroy = Mock()
        bridge = DesktopBridge(cache_store=CacheStore(self.root / "other-cache"))
        self.webview.create_window.return_value = other
        create_window(self.webview, bridge=bridge, index_path=self.index)
        self.addCleanup(other.events.closed.fire)
        other.events.loaded.fire()
        self.assert_close_rejected(bridge.approve_close(request))
        self.assert_close_rejected(bridge.cancel_close(request))
        self.assert_close_success(self.bridge.cancel_close(request))
        other.destroy.assert_not_called()
        self.window.destroy.assert_not_called()

    def test_missing_frontend_handshake_keeps_native_veto_without_boolean_ack(self):
        acknowledged = threading.Event()

        def unavailable(script):
            self.assertIn("window.__codevizCloseReady !== true", script)
            acknowledged.set()
            return None

        self.window.run_js.side_effect = unavailable
        self.bind()
        self.window.events.loaded.fire()
        self.assertIn(False, self.window.events.closing.fire())
        self.assertTrue(acknowledged.wait(2))
        self.assert_close_rejected(self.bridge.approve_close("forged_nonce"))
        self.window.destroy.assert_not_called()

    def test_destroy_failure_notifies_matching_request_off_thread_and_revokes_approval(self):
        requests = self.capture_close_requests()
        request_dispatch = self.window.run_js.side_effect
        failures = queue.Queue()
        notified = threading.Event()
        destroy_threads = []

        def dispatch(script):
            if "codeviz:close-failed" not in script:
                return request_dispatch(script)
            match = re.search(r'detail:\s*(\{"requestId"\s*:\s*"[^"\\]+"\})', script)
            if match is not None:
                failures.put((json.loads(match.group(1)), threading.get_ident(), script))
            notified.set()
            return None

        def failed_destroy():
            destroy_threads.append(threading.current_thread())
            raise RuntimeError("private-native-destroy-detail")

        self.window.run_js.side_effect = dispatch
        self.window.destroy.side_effect = failed_destroy
        self.bind()
        self.window.events.loaded.fire()
        request = self.request_close(requests)
        caller = threading.get_ident()
        self.assert_close_success(self.bridge.approve_close(request))
        self.assertTrue(notified.wait(2), "Destroy failure did not emit codeviz:close-failed")
        for worker in destroy_threads:
            worker.join(timeout=2)
            self.assertFalse(worker.is_alive())
        self.assertFalse(failures.empty(), "close-failed requires exact requestId payload")
        payload, identifier, script = failures.get_nowait()
        self.assertEqual(payload, {"requestId": request})
        self.assertNotEqual(identifier, caller)
        self.assertNotIn("private-native-destroy-detail", script)
        self.window.evaluate_js.assert_not_called()
        self.assertEqual(self.window.destroy.call_count, 1)
        self.assert_close_rejected(self.bridge.approve_close(request))
        self.assert_close_rejected(self.bridge.cancel_close(request))
        fresh = self.request_close(requests)
        self.assertNotEqual(request, fresh)
        self.assert_close_success(self.bridge.cancel_close(fresh))
        self.assertEqual(self.window.destroy.call_count, 1)

    def test_close_dispatch_uses_run_js_without_eval_or_boolean_ack(self):
        requests = self.capture_close_requests()
        self.bind()
        self.window.events.loaded.fire()
        request = self.request_close(requests)
        self.assert_close_success(self.bridge.cancel_close(request))
        self.window.evaluate_js.assert_not_called()
        self.assertEqual(self.window.run_js.call_count, 1)
        script = self.window.run_js.call_args.args[0]
        self.assertNotRegex(script, r"\beval\s*\(")
        self.assertNotIn("__codevizSourceGuardReady", script)
        self.window.destroy.assert_not_called()

    def test_approved_close_remains_coalesced_while_destroy_is_in_flight(self):
        requests = self.capture_close_requests()
        entered = threading.Event()
        release = threading.Event()
        completed = threading.Event()

        def blocked_destroy():
            entered.set()
            release.wait(2)
            completed.set()

        self.window.destroy.side_effect = blocked_destroy
        self.bind()
        self.window.events.loaded.fire()
        request = self.request_close(requests)
        try:
            self.assert_close_success(self.bridge.approve_close(request))
            self.assertTrue(entered.wait(2))
            self.assertEqual(self.window.events.closing.fire(), [None])
            for _ in range(3):
                self.assertIn(False, self.window.events.closing.fire())
            self.assertTrue(requests.empty())
            self.assertEqual(self.window.run_js.call_count, 1)
            self.assert_close_rejected(self.bridge.approve_close(request))
            self.assert_close_rejected(self.bridge.cancel_close(request))
        finally:
            release.set()
            self.assertTrue(completed.wait(2))

    def test_remote_worker_requests_still_blocked_by_native_resource_guard(self):
        self.bind()
        with patch("codeviz.ui.desktop.importlib.import_module", return_value=SimpleNamespace(
                CoreWebView2WebResourceContext=SimpleNamespace(All=0))):
            self.window.events.before_show.fire()
        core = self.window.native.browser.webview.CoreWebView2
        for uri in ("https://evil.test/editor.worker.js", "http://127.0.0.1:54321/worker.js"):
            event = SimpleNamespace(Request=SimpleNamespace(Uri=uri), Response=None)
            core.WebResourceRequested.fire(core, event)
            self.assertIsNotNone(event.Response)
        event = SimpleNamespace(Request=SimpleNamespace(
            Uri=self.window.real_url + "assets/editor.worker.js"), Response=None)
        core.WebResourceRequested.fire(core, event)
        self.assertIsNone(event.Response)
