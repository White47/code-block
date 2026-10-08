"""Launch an empty real WebView2 workspace with isolated smoke-test cache."""

import argparse
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Timer


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=9237, help="WebView2 CDP port")
    parser.add_argument("--watchdog", type=int, default=300, help="Host lifetime limit in seconds")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535 or args.watchdog <= 0:
        parser.error("port must be 1..65535 and watchdog must be positive")

    import webview

    from codeviz.persistence.store import CacheStore
    from codeviz.ui.desktop import create_window
    from codeviz.ui.web_bridge import DesktopBridge

    fixtures = Path(__file__).resolve().parents[1] / "tests" / "fixtures"
    projects = [(fixtures / name).resolve(strict=True)
                for name in ("mvp_project", "general_python_project")]
    with TemporaryDirectory(prefix="codeviz-general-python-smoke-") as directory:
        store = CacheStore(Path(directory))
        for project in projects:
            store.record_recent_project(project)
        bridge = DesktopBridge(cache_store=store)
        # Seed metadata only: project opening and analysis belong to rendered UI.
        assert bridge.get_session()["data"]["projectPath"] is None
        webview.settings["REMOTE_DEBUGGING_PORT"] = args.port
        window = create_window(webview, bridge=bridge)
        watchdog = Timer(args.watchdog, window.destroy)
        watchdog.daemon = True
        watchdog.start()
        print(f"General Python native smoke host PID={os.getpid()} CDP={args.port}", flush=True)
        try:
            webview.start(gui="edgechromium")
        finally:
            watchdog.cancel()


if __name__ == "__main__":
    main()
