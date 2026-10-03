"""Launch the real desktop shell with an isolated cache for GUI smoke tests."""

import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Timer

import webview

from codeviz.persistence.store import CacheStore
from codeviz.ui.desktop import create_window
from codeviz.ui.web_bridge import DesktopBridge


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9237
    project = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "mvp_project"
    with TemporaryDirectory(prefix="codeviz-desktop-smoke-") as directory:
        api = DesktopBridge(cache_store=CacheStore(Path(directory)))
        assert api.open_project(str(project))["ok"]
        assert api.analyze()["ok"]
        webview.settings["REMOTE_DEBUGGING_PORT"] = port
        window = create_window(webview, bridge=api)
        # Never leave an unattended smoke-test window behind.
        watchdog = Timer(300, window.destroy)
        watchdog.daemon = True
        watchdog.start()
        webview.start(gui="edgechromium")
        watchdog.cancel()


if __name__ == "__main__":
    main()
