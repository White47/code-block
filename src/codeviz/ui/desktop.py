"""Native pywebview launcher for the bundled React frontend."""

import importlib
import mimetypes
import re
import sys
from collections.abc import Callable, Iterable
from pathlib import Path
from types import ModuleType
from typing import Any
from urllib.parse import urlsplit

from .web_bridge import DesktopBridge

_REMOTE_URL = re.compile(r"^[a-z][a-z0-9+.-]*://", re.IGNORECASE)
_API_METHODS = (
    "get_session", "open_project", "choose_project", "analyze", "get_overview",
    "get_view", "get_detail_view", "get_inspector", "get_view_state", "search",
    "read_source", "get_preferences", "set_language", "set_preferences",
    "get_analysis_settings", "set_analysis_settings", "get_cache_info", "save_layout",
    "move_node", "save_viewport", "expand", "collapse", "open_detail_island",
    "move_detail_island", "close_detail_island", "get_problems",
)
_API_ARITIES = {
    "get_session": (0, 0), "open_project": (1, 1), "choose_project": (0, 0),
    "analyze": (0, 0), "get_overview": (0, 0), "get_view": (1, 5),
    "get_detail_view": (3, 3), "get_inspector": (3, 4), "get_view_state": (0, 0),
    "search": (1, 3), "read_source": (1, 1), "get_preferences": (0, 0),
    "set_language": (1, 1), "set_preferences": (1, 1),
    "get_analysis_settings": (0, 0), "set_analysis_settings": (1, 1),
    "get_cache_info": (0, 0), "save_layout": (1, 1), "move_node": (2, 3),
    "save_viewport": (3, 3), "expand": (1, 1), "collapse": (1, 1),
    "open_detail_island": (2, 3), "move_detail_island": (2, 2),
    "close_detail_island": (1, 1), "get_problems": (0, 0),
}
_CALLBACK_TOKEN = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; font-src 'self'; connect-src 'self'; "
    "frame-src 'none'; frame-ancestors 'none'; object-src 'none'; "
    "base-uri 'none'; form-action 'none'; worker-src 'none'"
)


class _DispatchGuard:
    def __init__(self, original: Any) -> None:
        self._original = original
        self._codeviz_windows: dict[int, Any] = {}

    def __call__(self, target: Any, func_name: Any, param: Any, value_id: Any) -> Any:
        if self._codeviz_windows.get(id(target)) is target:
            if (type(func_name) is not str or func_name not in _API_METHODS or
                    type(param) is not list or
                    not _API_ARITIES[func_name][0] <= len(param) <= _API_ARITIES[func_name][1] or
                    type(value_id) is not str or _CALLBACK_TOKEN.fullmatch(value_id) is None):
                # No response JS: untrusted names/tokens must never reach interpolation.
                return None
        return self._original(target, func_name, param, value_id)


def _install_dispatch_guard(window: Any, util: Any) -> None:
    """Guard API dispatch only; native alert/console/drop branches are separate."""
    dispatcher = util.js_bridge_call
    if not isinstance(dispatcher, _DispatchGuard):
        dispatcher = _DispatchGuard(dispatcher)
        util.js_bridge_call = dispatcher
    dispatcher._codeviz_windows[id(window)] = window
    # If the renderer was imported first, fix its cached imported reference too.
    # Otherwise its later import picks up util's guarded dispatcher naturally.
    edge: Any = sys.modules.get("webview.platforms.edgechromium")
    if edge is not None:
        edge.js_bridge_call = dispatcher


class _AssetApplication:
    """WSGI assets only: no framework catch-all or project/API file routes."""

    def __init__(self, index: Path) -> None:
        self.root = index.parent
        self.index = index

    def __call__(self, environ: dict[str, Any],
                 start_response: Callable[..., Any]) -> Iterable[bytes]:
        status = "404 Not Found"
        content = b"Not found"
        mime = "text/plain"
        path = str(environ.get("PATH_INFO", "/"))
        method = environ.get("REQUEST_METHOD", "GET")
        if method not in {"GET", "HEAD"}:
            status, content = "405 Method Not Allowed", b"Method not allowed"
        elif "\\" not in path and "\x00" not in path:
            try:
                target = (self.index if path == "/" else
                          self.root / path.lstrip("/")).resolve(strict=True)
                if (target.is_relative_to(self.root) and target.is_file()
                        and not any(part.startswith(".") for part in
                                    target.relative_to(self.root).parts)):
                    content = target.read_bytes()
                    status = "200 OK"
                    mime = ("text/javascript" if target.suffix in {".js", ".mjs"}
                            else mimetypes.guess_type(target.name)[0] or
                            "application/octet-stream")
            except (OSError, ValueError):
                pass
        start_response(status, [
            ("Content-Type", mime), ("Content-Length", str(len(content))),
            ("Content-Security-Policy", _CSP),
            ("X-Content-Type-Options", "nosniff"),
            ("Referrer-Policy", "no-referrer"),
            ("Cache-Control", "no-store"),
        ])
        return [b"" if method == "HEAD" else content]


def _asset_server(module: Any, app: _AssetApplication) -> type:
    class AssetServer:
        @classmethod
        def start_server(cls, urls: list[Any], http_port: int | None,
                         **kwargs: Any) -> tuple[Any, Any, Any]:
            # Pass only our WSGI app: BottleServer never installs its default
            # static routes or permissive /js_api HTTP endpoint in this mode.
            address, _, server = module.http.BottleServer.start_server(
                [app], http_port, **kwargs)
            server.root_path = str(app.root)
            server.common_path = str(app.root)
            return address, str(app.root), server

    return AssetServer


def _install_window_guards(window: Any) -> None:
    def require_edge(renderer: str) -> bool | None:
        if renderer != "edgechromium":
            return False
        return None

    def before_show() -> None:
        control = window.native.browser.webview

        def trusted(uri: str, *, document: bool = False) -> bool:
            expected = urlsplit(window.real_url)
            actual = urlsplit(uri)
            if (actual.scheme != "http" or actual.hostname != "127.0.0.1"
                    or actual.netloc != expected.netloc):
                return False
            return not document or actual.path in {expected.path, "/index.html"}

        def navigating(sender: Any, args: Any) -> None:
            if not trusted(str(args.Uri), document=True):
                args.Cancel = True

        def ready(sender: Any, args: Any) -> None:
            if not args.IsSuccess:
                return
            core = control.CoreWebView2
            edge = importlib.import_module("webview.platforms.edgechromium")
            core.AddWebResourceRequestedFilter("*", edge.CoreWebView2WebResourceContext.All)

            def resource(sender: Any, args: Any) -> None:
                if not trusted(str(args.Request.Uri)):
                    args.Response = core.Environment.CreateWebResourceResponse(
                        None, 403, "Forbidden", "Content-Type: text/plain")

            def frame(sender: Any, args: Any) -> None:
                args.Cancel = True

            def popup(sender: Any, args: Any) -> None:
                args.Handled = True

            core.WebResourceRequested += resource
            core.FrameNavigationStarting += frame
            core.NewWindowRequested += popup

        control.NavigationStarting += navigating
        control.CoreWebView2InitializationCompleted += ready
        if control.CoreWebView2 is not None:
            ready(control, type("Ready", (), {"IsSuccess": True})())

    window.events.initialized += require_edge
    window.events.before_show += before_show


def _is_network_path(value: str) -> bool:
    windows_path = value.replace("/", "\\")
    if windows_path.casefold().startswith("\\\\?\\unc\\"):
        return True
    return windows_path.startswith("\\\\") and not windows_path.startswith("\\\\?\\")


def resolve_frontend_index() -> Path:
    """Find frontend assets in a source checkout or a PyInstaller bundle."""
    candidates: list[Path] = []
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        candidates.append(Path(bundle_root) / "frontend" / "dist" / "index.html")
    candidates.append(
        Path(__file__).resolve().parents[3] / "frontend" / "dist" / "index.html")
    candidates.append(Path(__file__).resolve().parent / "assets" / "index.html")
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    searched = ", ".join(str(candidate) for candidate in candidates)
    raise RuntimeError(
        "Bundled frontend/dist/index.html is missing. Build the React frontend "
        f"before launching CodeViz. Searched: {searched}")


def _load_webview() -> ModuleType:
    try:
        return importlib.import_module("webview")
    except ImportError as error:
        raise RuntimeError(
            "pywebview is required for the React desktop application") from error


def create_window(webview: Any = None, *, bridge: DesktopBridge | Any = None,
                  index_path: Path | str | None = None) -> object:
    """Create, but do not start, a local-only desktop window."""
    module = webview if webview is not None else _load_webview()
    api = bridge if bridge is not None else DesktopBridge()
    raw_path = resolve_frontend_index() if index_path is None else index_path
    raw_text = str(raw_path)
    if _REMOTE_URL.match(raw_text):
        raise ValueError("remote frontend URLs are not allowed")
    if _is_network_path(raw_text):
        raise ValueError("network frontend paths are not allowed")
    index = Path(raw_path).resolve(strict=True)
    if _is_network_path(str(index)):
        raise ValueError("resolved frontend path is on a network share")
    if not index.is_file():
        raise RuntimeError("frontend index must be a local file")
    if (index.name != "index.html" or not (
            index.parent.parts[-2:] == ("frontend", "dist") or
            index.parent == Path(__file__).resolve().parent / "assets")):
        raise ValueError("frontend must be frontend/dist/index.html or bundled UI assets")
    app = _AssetApplication(index)
    window = module.create_window(
        "CodeViz",
        app,
        server=_asset_server(module, app),
        js_api=None,
        width=1200,
        height=700,
        min_size=(1000, 640),
    )
    if window is None:
        raise RuntimeError("desktop window could not be created")
    window.expose(*(getattr(api, name) for name in _API_METHODS))
    _install_dispatch_guard(window, module.util)
    api._bind_window(window, int(module.FileDialog.FOLDER))
    _install_window_guards(window)
    return window


def main() -> None:
    """Launch the GUI only when explicitly invoked."""
    webview = _load_webview()
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = False
    webview.settings["ALLOW_DOWNLOADS"] = False
    create_window(webview, index_path=resolve_frontend_index())
    webview.start(gui="edgechromium")


if __name__ == "__main__":
    main()
