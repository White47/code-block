"""Launch an owned disposable project in the real Explorer PC shell."""

import argparse
import json
import os
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Timer
from urllib.parse import quote
from uuid import uuid4


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=9370)
    parser.add_argument("--executable", type=Path)
    parser.add_argument("--installed", type=Path)
    parser.add_argument("--analyze", action="store_true")
    parser.add_argument("--source-alias", action="store_true")
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("Port must be between 1024 and 65535")
    if args.executable and args.installed:
        parser.error("Choose either frozen executable or installed wheel")
    if args.installed:
        import sys
        sys.path.insert(0, str(args.installed.resolve(strict=True)))
    import webview

    import codeviz
    from codeviz.persistence.store import CacheStore
    from codeviz.ui.desktop import create_window
    from codeviz.ui.web_bridge import DesktopBridge

    if args.installed:
        assert Path(codeviz.__file__).resolve().is_relative_to(args.installed.resolve())
    with TemporaryDirectory(prefix="codeviz-explorer-smoke-") as temporary:
        scope = Path(temporary)
        project = scope / "project"
        project.mkdir()
        (project / "src").mkdir()
        (project / "src" / "helper.py").write_bytes(
            b'def helper(value: int) -> int:\r\n    return value + 1\r\n')
        (project / "README.md").write_text("# Explorer fixture\n\n中文文档。\n", encoding="utf-8")
        (project / "config.json").write_text('{"enabled": true}\n', encoding="utf-8")
        (project / "binary.bin").write_bytes(bytes(range(32)))
        (project / ".env").write_text("EXAMPLE=local\n", encoding="utf-8")
        for directory in (".git", ".venv", "node_modules"):
            (project / directory).mkdir()
            (project / directory / "protected.py").write_text("# protected fixture\n", encoding="utf-8")
        recycle_name = f"codeviz-explorer-recycle-{uuid4().hex}.txt"
        (project / recycle_name).write_text("Disposable recycle proof only.\n", encoding="utf-8")
        data = scope / "local" / "codeviz" if args.executable else scope / "cache"
        bridge = DesktopBridge(cache_store=CacheStore(data))
        assert bridge.open_project(str(project))["ok"]
        if args.source_alias:
            if args.analyze:
                parser.error("Source alias fixture starts without analysis")
            anchor = {"filePath": "SRC/HELPER.PY", "startLine": 1, "endLine": 1,
                      "startColumn": None, "endColumn": None}
            tab = "source:" + quote(json.dumps(anchor, separators=(",", ":")), safe="")
            assert bridge.save_layout({"activeView": None, "selectedId": None,
                                       "openTabs": ["Overview", tab], "graphRoots": {}})["ok"]
        if args.analyze:
            assert bridge.analyze()["ok"]
        information = {"project": str(project), "recycleName": recycle_name, "port": args.port}
        if args.executable:
            executable = args.executable.resolve(strict=True)
            if executable.name != "CodeViz.exe":
                parser.error("Expected explicit CodeViz.exe path")
            environment = dict(os.environ)
            environment["LOCALAPPDATA"] = str(scope / "local")
            environment["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = f"--remote-debugging-port={args.port}"
            process = subprocess.Popen([str(executable)], cwd=scope, env=environment)
            print(json.dumps({**information, "pid": process.pid}), flush=True)
            process.wait()
            return
        webview.settings["REMOTE_DEBUGGING_PORT"] = args.port
        window = create_window(webview, bridge=bridge)
        print(json.dumps({**information, "pid": os.getpid()}), flush=True)
        watchdog = Timer(1800, window.destroy)
        watchdog.daemon = True
        watchdog.start()
        try:
            webview.start(gui="edgechromium")
        finally:
            watchdog.cancel()


if __name__ == "__main__":
    main()
