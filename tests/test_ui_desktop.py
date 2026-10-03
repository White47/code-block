"""Desktop launcher tests use a fake webview and never start a GUI."""

import inspect
import sys
import threading
import unittest
from importlib.util import find_spec
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

from codeviz.ui.desktop import (
    _API_ARITIES,
    _API_METHODS,
    _AssetApplication,
    _install_dispatch_guard,
    create_window,
    main,
    resolve_frontend_index,
)


class FakeEvent:
    def __init__(self) -> None:
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self

    def fire(self, *args):
        return [handler(*args) for handler in self.handlers]


class FakeWebview:
    def __init__(self) -> None:
        self.created: list[tuple[tuple[object, ...], dict[str, object]]] = []
        self.started = 0
        self.settings = {}
        self.util = SimpleNamespace(js_bridge_call=Mock())
        self.FileDialog = SimpleNamespace(FOLDER=20)
        self.http = SimpleNamespace(BottleServer=SimpleNamespace(start_server=Mock(
            return_value=("http://127.0.0.1:12345/", ".", SimpleNamespace()))))
        core = SimpleNamespace(WebResourceRequested=FakeEvent(),
                               FrameNavigationStarting=FakeEvent(),
                               NewWindowRequested=FakeEvent(), Environment=Mock(),
                               AddWebResourceRequestedFilter=Mock())
        control = SimpleNamespace(NavigationStarting=FakeEvent(),
                                  CoreWebView2InitializationCompleted=FakeEvent(),
                                  CoreWebView2=core)
        self.window = SimpleNamespace(
            events=SimpleNamespace(initialized=FakeEvent(), before_show=FakeEvent()),
            native=SimpleNamespace(browser=SimpleNamespace(webview=control)),
            real_url="http://127.0.0.1:12345/", create_file_dialog=Mock(return_value=None),
            expose=Mock())

    def create_window(self, *args: object, **kwargs: object) -> object:
        self.created.append((args, kwargs))
        return self.window

    def start(self, **kwargs) -> None:
        self.started += 1
        self.start_options = kwargs


class DesktopLauncherTests(unittest.TestCase):
    @unittest.skipUnless(find_spec("webview"), "pywebview is not installed")
    def test_real_dispatch_blocks_forged_names_and_retains_dto_validation(self) -> None:
        import webview

        from codeviz.persistence.store import CacheStore
        from codeviz.ui.web_bridge import DesktopBridge
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            index = root / "frontend" / "dist" / "index.html"
            index.parent.mkdir(parents=True)
            index.write_text("desktop", encoding="utf-8")
            bridge = DesktopBridge(cache_store=CacheStore(root / "appdata"))
            original = webview.util.js_bridge_call
            platform_name = "webview.platforms.edgechromium"
            # Avoid importing the GUI platform; its eventual from-import obtains
            # the already guarded util function.
            with patch.object(webview.util, "js_bridge_call", original), \
                    patch.dict(sys.modules, {platform_name: None}):
                window = create_window(webview, bridge=bridge, index_path=index)
                self.addCleanup(webview.windows.remove, window)
                self.assertIsNone(window._js_api)
                self.assertEqual(set(window._functions), set(_API_METHODS))
                guarded = webview.util.js_bridge_call
                _install_dispatch_guard(window, webview.util)
                self.assertIs(webview.util.js_bridge_call, guarded)
                self.assertIsNone(sys.modules[platform_name])
                rendered = []
                completed = threading.Event()

                def render(script):
                    rendered.append(script)
                    completed.set()

                with patch.object(window, "evaluate_js", side_effect=render), \
                        patch.object(bridge._controller, "expand") as expand, \
                        patch.object(window, "destroy") as destroy, \
                        patch.object(guarded, "_original", wraps=guarded._original) as dispatch:
                    forged = (
                        "_controller.expand", "_window.destroy", "__class__", "expand.extra",
                        "_bind_window", "pywebviewMoveWindow", "pywebviewEventHandler",
                        "pywebviewAsyncCallback", "pywebviewStateUpdate", "pywebviewStateDelete",
                        'expand\"];alert(1);//',
                    )
                    for name in forged:
                        guarded(window, name, ["missing"], "valid_token")
                    for name, param, token in ((None, [], "token"), ([], [], "token"),
                                               ("expand", {}, "token"),
                                               ("expand", (), "token"),
                                               ("expand", [], None),
                                               ("expand", [], ""),
                                               ("expand", [], "x" * 129),
                                               ("expand", [], 'x\"];alert(1);//'),
                                               ("expand", [None] * 257, "token")):
                        guarded(window, name, param, token)
                    for name, (minimum, maximum) in _API_ARITIES.items():
                        guarded(window, name, [None] * (maximum + 1), "valid_token")
                        if minimum:
                            guarded(window, name, [None] * (minimum - 1), "valid_token")
                    self.assertEqual(rendered, [])
                    dispatch.assert_not_called()
                    expand.assert_not_called()
                    destroy.assert_not_called()
                    guarded(window, "expand", [False], "valid_token")
                    self.assertTrue(completed.wait(2))
                    self.assertIn("INVALID_REQUEST", rendered[-1])
                    expand.assert_not_called()
                    completed.clear()
                    fixture = Path(__file__).parent / "fixtures" / "mvp_project"
                    self.assertTrue(bridge.open_project(str(fixture))["ok"])
                    self.assertTrue(bridge.analyze()["ok"])
                    node = bridge._analysis().model.definitions[0].symbol_id
                    guarded(window, "expand", [node], "valid_token_2")
                    self.assertTrue(completed.wait(2))
                    expand.assert_called_once_with(node)
                    self.assertIn('"ok": true', rendered[-1])
                    for parameters in (["model"], ["model", 10], ["model", 10, None]):
                        completed.clear()
                        guarded(window, "search", parameters, "optional_defaults")
                        self.assertTrue(completed.wait(2))
                        self.assertIn('"ok": true', rendered[-1])
                        self.assertNotIn("Traceback", rendered[-1])
                # A renderer loaded afterwards imports the same patched reference.
                late_platform = SimpleNamespace(js_bridge_call=webview.util.js_bridge_call)
                self.assertIs(late_platform.js_bridge_call, guarded)
                with patch.object(window, "evaluate_js") as evaluate:
                    late_platform.js_bridge_call(window, "_controller.expand", [], "token")
                    evaluate.assert_not_called()

    def test_fixed_arity_contract_matches_required_parameters_and_optional_defaults(self) -> None:
        from codeviz.ui.web_bridge import DesktopBridge
        self.assertEqual(set(_API_ARITIES), set(_API_METHODS))
        for name, (minimum, maximum) in _API_ARITIES.items():
            signature = inspect.signature(getattr(DesktopBridge, name))
            positional = [param for param in signature.parameters.values()
                          if param.name != "self"]
            self.assertEqual(len(positional), maximum, name)
            required = [param for param in positional if param.default is inspect.Parameter.empty]
            self.assertEqual(len(required), minimum, name)
            for count in range(minimum, maximum + 1):
                signature.bind(None, *([None] * count))

    @unittest.skipUnless(find_spec("webview"), "pywebview is not installed")
    def test_real_dispatch_patches_loaded_renderer_and_preserves_other_windows(self) -> None:
        from webview import util
        current = util.js_bridge_call
        original = Mock(wraps=current)
        target = SimpleNamespace()
        other = SimpleNamespace()
        platform = SimpleNamespace(js_bridge_call=current)
        with patch.object(util, "js_bridge_call", original), \
                patch.dict(sys.modules, {"webview.platforms.edgechromium": platform}):
            _install_dispatch_guard(target, util)
            guarded = util.js_bridge_call
            self.assertIs(platform.js_bridge_call, guarded)
            _install_dispatch_guard(target, util)
            self.assertIs(util.js_bridge_call, guarded)
            platform.js_bridge_call(target, "_window.destroy", [], "token")
            original.assert_not_called()
            # Unknown windows retain the original framework dispatcher unchanged.
            original.side_effect = lambda *args: "original-result"
            self.assertEqual(guarded(other, "pywebviewMoveWindow", [1, 2], "token"),
                             "original-result")
            original.assert_called_once_with(other, "pywebviewMoveWindow", [1, 2], "token")

    def test_resolve_frontend_index_supports_development_tree(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            index = root / "frontend" / "dist" / "index.html"
            index.parent.mkdir(parents=True)
            index.write_text("desktop", encoding="utf-8")
            fake_module = root / "src" / "codeviz" / "ui" / "desktop.py"

            with patch("codeviz.ui.desktop.__file__", str(fake_module)), \
                    patch.object(sys, "_MEIPASS", None, create=True):
                self.assertEqual(resolve_frontend_index(), index.resolve())

    def test_resolve_frontend_index_supports_pyinstaller(self) -> None:
        with TemporaryDirectory() as temporary:
            bundle = Path(temporary)
            index = bundle / "frontend" / "dist" / "index.html"
            index.parent.mkdir(parents=True)
            index.write_text("desktop", encoding="utf-8")

            with patch.object(sys, "_MEIPASS", str(bundle), create=True):
                self.assertEqual(resolve_frontend_index(), index.resolve())

    def test_missing_frontend_has_clear_error(self) -> None:
        with TemporaryDirectory() as temporary:
            fake_module = Path(temporary) / "src" / "codeviz" / "ui" / "desktop.py"
            with patch("codeviz.ui.desktop.__file__", str(fake_module)), \
                    patch.object(sys, "_MEIPASS", None, create=True):
                with self.assertRaisesRegex(RuntimeError, "frontend/dist/index.html"):
                    resolve_frontend_index()

    def test_create_window_uses_local_file_bridge_and_minimum_size(self) -> None:
        with TemporaryDirectory() as temporary:
            index = Path(temporary) / "frontend" / "dist" / "index.html"
            index.parent.mkdir(parents=True)
            index.write_text("desktop", encoding="utf-8")
            fake = FakeWebview()
            bridge = Mock()

            window = create_window(fake, bridge=bridge, index_path=index)

        self.assertIsNotNone(window)
        args, kwargs = fake.created[0]
        self.assertEqual(args[0], "CodeViz")
        self.assertIsInstance(args[1], _AssetApplication)
        self.assertEqual(args[1].root, index.parent.resolve())
        bridge._bind_window.assert_called_once_with(window, 20)
        self.assertIsNone(kwargs["js_api"])
        self.assertEqual(window.expose.call_args.args,
                         tuple(getattr(bridge, name) for name in _API_METHODS))
        self.assertEqual((kwargs["width"], kwargs["height"]), (1200, 700))
        self.assertEqual(kwargs["min_size"], (1000, 640))

    def test_remote_frontend_url_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "remote"):
            create_window(FakeWebview(), bridge=SimpleNamespace(),
                          index_path="https://example.com/index.html")

    def test_unc_and_network_frontend_paths_are_rejected(self) -> None:
        for path in (r"\\server\share\index.html", "//server/share/index.html"):
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, "network"):
                create_window(FakeWebview(), bridge=SimpleNamespace(), index_path=path)

    def test_resolved_frontend_target_cannot_be_a_network_path(self) -> None:
        with TemporaryDirectory() as temporary:
            local = Path(temporary) / "index.html"
            local.write_text("desktop", encoding="utf-8")
            resolved_unc = Path(r"\\server\share\index.html")
            with patch("codeviz.ui.desktop.Path.resolve", return_value=resolved_unc):
                with self.assertRaisesRegex(ValueError, "network"):
                    create_window(
                        FakeWebview(), bridge=SimpleNamespace(), index_path=local)

    def test_main_lazily_creates_window_then_starts_webview(self) -> None:
        with TemporaryDirectory() as temporary:
            index = Path(temporary) / "frontend" / "dist" / "index.html"
            index.parent.mkdir(parents=True)
            index.write_text("desktop", encoding="utf-8")
            fake = FakeWebview()
            with patch("codeviz.ui.desktop._load_webview", return_value=fake), \
                    patch("codeviz.ui.desktop.resolve_frontend_index", return_value=index):
                main()

        self.assertEqual(len(fake.created), 1)
        self.assertEqual(fake.started, 1)
        self.assertEqual(fake.start_options, {"gui": "edgechromium"})

    def test_resolve_supports_wheel_assets(self) -> None:
        with TemporaryDirectory() as temporary:
            module = Path(temporary) / "codeviz" / "ui" / "desktop.py"
            index = module.parent / "assets" / "index.html"
            index.parent.mkdir(parents=True)
            index.write_text("desktop", encoding="utf-8")
            with patch("codeviz.ui.desktop.__file__", str(module)), \
                    patch.object(sys, "_MEIPASS", None, create=True):
                self.assertEqual(resolve_frontend_index(), index.resolve())

    def test_arbitrary_project_root_is_rejected(self) -> None:
        with TemporaryDirectory() as temporary:
            index = Path(temporary) / "index.html"
            index.write_text("desktop", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "frontend/dist"):
                create_window(FakeWebview(), index_path=index)

    def test_asset_application_contains_root_and_sets_local_script_csp(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            index = root / "frontend" / "dist" / "index.html"
            index.parent.mkdir(parents=True)
            index.write_text("desktop", encoding="utf-8")
            (root / "secret.py").write_text("private", encoding="utf-8")
            (index.parent / "app.js").write_text("local", encoding="utf-8")
            app = _AssetApplication(index.resolve())
            for path, expected in (("/", "200"), ("/app.js", "200"),
                                   ("/../../secret.py", "404"), ("/js_api/token", "404"),
                                   ("/secret.py", "404"), ("/..\\..\\secret.py", "404")):
                response = Mock()
                body = b"".join(app({"PATH_INFO": path, "REQUEST_METHOD": "GET"}, response))
                self.assertTrue(response.call_args.args[0].startswith(expected))
                headers = dict(response.call_args.args[1])
                self.assertIn("script-src 'self';", headers["Content-Security-Policy"])
                self.assertNotIn("https:", headers["Content-Security-Policy"])
                self.assertNotEqual(body, b"private")
            response = Mock()
            app({"PATH_INFO": "/", "REQUEST_METHOD": "POST"}, response)
            self.assertEqual(response.call_args.args[0], "405 Method Not Allowed")

    def test_framework_server_receives_only_asset_application(self) -> None:
        with TemporaryDirectory() as temporary:
            index = Path(temporary) / "frontend" / "dist" / "index.html"
            index.parent.mkdir(parents=True)
            index.write_text("desktop", encoding="utf-8")
            fake = FakeWebview()
            create_window(fake, bridge=Mock(), index_path=index)
            args, kwargs = fake.created[0]
            address, common, server = kwargs["server"].start_server([str(Path(temporary))], None)
            self.assertEqual(server.root_path, str(index.parent.resolve()))
            self.assertEqual(common, server.root_path)
            self.assertEqual(fake.http.BottleServer.start_server.call_args.args[0], [args[1]])

    def test_native_guards_cancel_remote_navigation_frames_and_resources(self) -> None:
        with TemporaryDirectory() as temporary:
            index = Path(temporary) / "frontend" / "dist" / "index.html"
            index.parent.mkdir(parents=True)
            index.write_text("desktop", encoding="utf-8")
            fake = FakeWebview()
            window = create_window(fake, bridge=Mock(), index_path=index)
            self.assertEqual(window.events.initialized.fire("mshtml"), [False])
            with patch("codeviz.ui.desktop.importlib.import_module", return_value=SimpleNamespace(
                    CoreWebView2WebResourceContext=SimpleNamespace(All=0))):
                window.events.before_show.fire()
            control = window.native.browser.webview
            for uri, allowed in ((window.real_url, True), (window.real_url + "#view", True),
                                 ("https://evil.test", False), ("file:///secret", False),
                                 ("http://127.0.0.1:54321/", False),
                                 (window.real_url + "app.js", False)):
                event = SimpleNamespace(Uri=uri, Cancel=False)
                control.NavigationStarting.fire(control, event)
                self.assertEqual(event.Cancel, not allowed)
            core = control.CoreWebView2
            core.AddWebResourceRequestedFilter.assert_called_once_with("*", 0)
            event = SimpleNamespace(Request=SimpleNamespace(Uri="https://evil.test/script.js"),
                                    Response=None)
            core.WebResourceRequested.fire(core, event)
            self.assertIsNotNone(event.Response)
            event = SimpleNamespace(Cancel=False)
            core.FrameNavigationStarting.fire(core, event)
            self.assertTrue(event.Cancel)
            event = SimpleNamespace(Handled=False)
            core.NewWindowRequested.fire(core, event)
            self.assertTrue(event.Handled)


if __name__ == "__main__":
    unittest.main()
