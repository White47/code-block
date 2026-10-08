"""Start an isolated native host; seed recent-project metadata, never run sources."""

import argparse
import os
import socket
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Timer


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, action="append", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--watchdog", type=int, default=1200)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535 or not 1 <= args.watchdog <= 3600:
        parser.error("port must be 1..65535 and watchdog 1..3600 seconds")
    projects = [project.resolve(strict=True) for project in args.project]
    if not all(project.is_dir() for project in projects):
        parser.error("projects must be existing directories")
    # Reject reused test ports before pywebview chooses an IPv4/IPv6 listener.
    for family, address in ((socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")):
        with socket.socket(family) as probe:
            try:
                probe.bind((address, args.port))
            except OSError as error:
                parser.error(f"debug port unavailable on {address}: {error}")

    import webview

    from codeviz.persistence.store import CacheStore
    from codeviz.ui.desktop import create_window
    from codeviz.ui.web_bridge import DesktopBridge

    with TemporaryDirectory(prefix="codeviz-architecture-detail-") as directory:
        store = CacheStore(Path(directory))
        for project in projects:
            store.record_recent_project(project)
            store.save_annotations(project, {"architecture-test-note": "Retain during detail analysis"})
        bridge = DesktopBridge(cache_store=store)
        assert bridge.get_session()["data"]["projectPath"] is None
        webview.settings["REMOTE_DEBUGGING_PORT"] = args.port
        window = create_window(webview, bridge=bridge)
        watchdog = Timer(args.watchdog, window.destroy)
        watchdog.daemon = True
        watchdog.start()
        print(f"Architecture native host PID={os.getpid()} CDP={args.port}", flush=True)
        try:
            webview.start(gui="edgechromium")
        finally:
            watchdog.cancel()


if __name__ == "__main__":
    main()
