"""Run the real PC shell against an owned, disposable editing fixture."""

import argparse
import json
import os
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Timer

import webview

from codeviz.persistence.store import CacheStore
from codeviz.ui.desktop import create_window
from codeviz.ui.web_bridge import DesktopBridge


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=9341)
    parser.add_argument("--executable", type=Path,
                        help="Run an actual frozen package with isolated local app data")
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("Port must be between 1024 and 65535")
    with TemporaryDirectory(prefix="codeviz-source-edit-smoke-") as directory:
        scope = Path(directory)
        project = scope / "project"
        project.mkdir()
        (project / "sample.py").write_bytes(
            '# -*- coding: utf-8 -*-\r\n'
            'def atoms(text: str, chunk_size: int = 8) -> list[str]:\r\n'
            '    """中文字符串与 Python 语法高亮。"""\r\n'
            '    # Readable source; never executed by this test.\r\n'
            '    if len(text) <= chunk_size:\r\n'
            '        return [text]\r\n'
            '    return [text[:chunk_size], text[chunk_size:]]\r\n'.encode("utf-8"))
        data_dir = scope / "local" / "codeviz" if args.executable else scope / "cache"
        bridge = DesktopBridge(cache_store=CacheStore(data_dir))
        assert bridge.open_project(str(project))["ok"]
        assert bridge.analyze()["ok"]
        if args.executable:
            executable = args.executable.resolve(strict=True)
            if executable.name != "CodeViz.exe":
                parser.error("Expected an explicit CodeViz.exe package path")
            environment = dict(os.environ)
            environment["LOCALAPPDATA"] = str(scope / "local")
            environment["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = (
                f"--remote-debugging-port={args.port}")
            process = subprocess.Popen([str(executable)], cwd=scope, env=environment)
            print(json.dumps({"pid": process.pid, "project": str(project),
                              "file": str(project / "sample.py"), "port": args.port,
                              "executable": str(executable)}), flush=True)
            process.wait()
            return
        webview.settings["REMOTE_DEBUGGING_PORT"] = args.port
        window = create_window(webview, bridge=bridge)
        print(json.dumps({"pid": os.getpid(), "project": str(project),
                          "file": str(project / "sample.py"), "port": args.port}),
              flush=True)
        watchdog = Timer(1800, window.destroy)
        watchdog.daemon = True
        watchdog.start()
        try:
            webview.start(gui="edgechromium")
        finally:
            watchdog.cancel()


if __name__ == "__main__":
    main()
