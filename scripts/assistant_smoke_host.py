"""Owned WebView2 fixture with a constructor-injected, network-free provider.

Not imported by the ordinary launcher. No public bridge test methods, real
credentials, configurable endpoints or analyzed-project execution are used.
"""

import argparse
import importlib.util
import json
import os
import runpy
import socket
import sys
import time
from contextlib import ExitStack
from http.client import HTTPConnection
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Timer
from unittest.mock import patch

_FIXTURE = '''raise RuntimeError("Static fixture must never execute")

def normalize(value):
    return value.strip()

def process_record(value):
    normalized = normalize(value)
    if not normalized:
        return None
    return normalized
'''
_KINDS = ("summary", "entry", "steps", "branches", "relations", "evidence", "unknowns")


def _wait_for_owned_asset_http(port, *, connection_factory=HTTPConnection,
                               timeout=5, sleep=time.sleep):
    if type(port) is not int or not 1024 <= port <= 65535:
        raise ValueError("Owned asset readiness port changed")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        connection = None
        try:
            remaining = deadline - time.monotonic()
            connection = connection_factory(
                "127.0.0.1", port, timeout=min(0.5, max(0.01, remaining)))
            connection.request("GET", "/", headers={"Connection": "close"})
            response = connection.getresponse()
            if response.status == 200 and response.read(1):
                return
        except Exception:
            pass
        finally:
            if connection is not None:
                connection.close()
        sleep(0.05)
    raise RuntimeError("Owned asset server did not become HTTP-ready")


class _SmokeProvider:
    """Only serializes a local protocol response; dispatch cannot use HTTP."""

    def __init__(self):
        self.sends = 0
        self.closed = False
        self.cancelled = 0
        self._prepared = {}
        self._preparations = 0

    async def prepare(self, draft):
        from codeviz.assistant.provider import PreparedRequest, WireOccurrence

        logical = json.loads(draft.body)
        body = json.dumps({"model": "probe-model", "messages": logical["messages"]},
                          ensure_ascii=False, separators=(",", ":")).encode()

        def tags(name):
            return tuple(WireOccurrence(item["pointer"], item["start"], item["end"], item["file"])
                         for item in logical.get(name, []))

        self._preparations += 1
        prepared = PreparedRequest(str(self._preparations), "POST",
            "https://provider.invalid:443/v1/chat/completions", body,
            draft.max_output_tokens, draft.deadline, len(body) + 1024,
            tags("sourceTags"), tags("metadataTags"), True)
        self._prepared[id(prepared)] = logical
        return prepared

    async def dispatch(self, prepared):
        from codeviz.assistant.provider import ProviderEvent

        self.sends += 1
        logical = self._prepared.pop(id(prepared))
        if logical["mode"] == "final":
            excerpts = [result for message in logical["messages"]
                        if message.get("role") == "tool"
                        for result in [json.loads(message["content"])["result"]]
                        if result["kind"] == "source_excerpt"]
            # C6 root excerpts are inert user messages: their first line is the
            # service-issued evidence header, not a fabricated tool response.
            for tag in logical.get("sourceTags", []):
                message = logical["messages"][int(tag["pointer"].split("/")[2])]
                if message.get("role") == "user":
                    header = json.loads(message["content"].split("\n", 1)[0])
                    if "evidenceId" in header:
                        excerpts.append(header)
            sections = [{"kind": kind, "claims": []} for kind in _KINDS]
            if excerpts:
                sections[0]["claims"] = [{"text": "Saved Python fixture: normalize then branch; not runtime observation.",
                    "basis": "inference", "evidenceIds": [excerpts[0]["evidenceId"]],
                    "relationEvidenceId": None}]
            sections[6]["claims"] = [{"text": "Unable to confirm runtime values or external effects.",
                "basis": "unknown", "evidenceIds": [], "relationEvidenceId": None}]
            yield ProviderEvent("answer", json.dumps({"index": 0, "last": True,
                "content": json.dumps({"sections": sections})}).encode())
        yield ProviderEvent("usage", b'{"inputTokens":1,"outputTokens":1,"basis":"actual"}')

    async def cancel(self):
        self.cancelled += 1

    async def aclose(self):
        self.closed = True
        self._prepared.clear()


def _helper(name):
    if name not in ("verify_assistant_dependencies", "verify_assistant_production"):
        raise ValueError("Unknown owned helper")
    if getattr(sys, "frozen", False):
        if name == "verify_assistant_dependencies":
            import verify_assistant_dependencies as module
        else:
            import verify_assistant_production as module
        return vars(module)
    return runpy.run_path(str(Path(__file__).with_name(name + ".py")))


def _source_bootstrap():
    if getattr(sys, "frozen", False):
        raise ValueError("Frozen artifacts cannot use a source tree")
    source = Path(__file__).resolve().parents[1] / "src"
    if not (source / "codeviz" / "__init__.py").is_file():
        raise ValueError("Owned source tree is missing")
    spec = importlib.util.find_spec("codeviz")
    if spec is None:
        sys.path.insert(0, str(source))
        spec = importlib.util.find_spec("codeviz")
    if spec is None or not spec.origin or not Path(spec.origin).resolve().is_relative_to(source):
        raise ValueError("Source artifact import escaped the owned tree")


def _create_smoke_bridge(work: Path, *, deepseek=False):
    """Create only our own fixture; never accept an analyzed project argument."""
    from codeviz.assistant.service import _AssistantRuntime
    from codeviz.persistence.store import CacheStore
    from codeviz.ui.controller import AppController
    from codeviz.ui.web_bridge import DesktopBridge

    project = work / "project"
    project.mkdir()  # Fail closed on reuse: never overwrite an existing project.
    (project / "sample.py").write_text(_FIXTURE, encoding="utf-8")
    if deepseek:
        from codeviz.assistant._deepseek_admission import _create_application_runtime

        runtime = _create_application_runtime()
        provider = _helper("verify_assistant_production")["FixtureHTTP"](runtime, _KINDS)
    else:
        provider = _SmokeProvider()
        runtime = _AssistantRuntime(provider_factory=lambda _config, _key: provider)
    try:
        cache = CacheStore(work / "cache")
        controller = AppController(cache, assistant_publication_mirror=runtime)
        bridge = DesktopBridge(controller=controller, cache_store=cache)
        bridge._bind_assistant_runtime(runtime)
        for response in (bridge.open_project(str(project)), bridge.analyze(),
                         bridge.set_preferences({"language": "en"})):
            if not response["ok"]:
                raise RuntimeError("Owned native fixture initialization failed")
        return bridge, provider
    except Exception:
        runtime.shutdown()
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--deepseek", action="store_true")
    parser.add_argument("--source-tree", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if not 1024 <= args.port <= 65535:
        parser.error("Use an unprivileged loopback debugging port")
    if args.source_tree:
        _source_bootstrap()

    args.output.mkdir(parents=True, exist_ok=True)
    stages = []

    def mark(stage):
        stages.append(stage)
        (args.output / "host-stages.json").write_text(json.dumps(stages), encoding="utf-8")

    with TemporaryDirectory(prefix="codeviz-assistant-native-") as directory, ExitStack() as stack:
        attempts = []
        if args.deepseek:
            with socket.socket() as reservation:
                reservation.bind(("127.0.0.1", 0))
                asset_port = reservation.getsockname()[1]
            guard = _helper("verify_assistant_dependencies")["offline_guard"]
            attempts = stack.enter_context(guard(
                extra_environment={name: os.environ[name] for name in ("PATH", "COMSPEC") if name in os.environ},
                native_loopback_ports=(asset_port, args.port)))
            mark("native-guard")
        import webview
        mark("webview-imported")

        from codeviz.ui.desktop import create_window

        if args.deepseek:
            original_start = webview.http.BottleServer.start_server
            original_make = webview.http.make_server
            bound = Event()
            starts = []

            def owned_listener(*values, **kwargs):
                server = original_make(*values, **kwargs)
                if (server.server_address != ("127.0.0.1", asset_port)
                        or server.socket.getsockname() != ("127.0.0.1", asset_port)):
                    server.server_close()
                    raise ValueError("Owned listener address changed")
                bound.set()
                mark("owned-asset-bound")
                return server

            def fixed_server(urls, http_port=None, **kwargs):
                starts.append(True)
                if len(starts) != 1 or http_port not in (None, asset_port):
                    raise ValueError("Unexpected owned asset port")
                result = original_start(urls, asset_port, **kwargs)
                if not bound.wait(5) or result[0] != f"http://127.0.0.1:{asset_port}/":
                    raise ValueError("Owned asset origin changed")
                _wait_for_owned_asset_http(asset_port)
                mark("owned-asset-ready")
                return result

            stack.enter_context(patch.object(webview.http, "make_server", owned_listener))
            stack.enter_context(patch.object(webview.http.BottleServer, "start_server", staticmethod(fixed_server)))
        bridge, provider = _create_smoke_bridge(Path(directory), deepseek=args.deepseek)
        mark("fixture-ready")
        if args.deepseek:
            from codeviz.assistant import sdk_provider

            stack.enter_context(patch.object(sdk_provider, "_new_secure_transport", provider.factory))
        watchdog = None
        try:
            (args.output / "host-start.json").write_text(json.dumps({
                "pid": os.getpid(), "parentPid": os.getppid(), "port": args.port,
            }), encoding="utf-8")
            webview.settings["REMOTE_DEBUGGING_PORT"] = args.port
            webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = False
            webview.settings["ALLOW_DOWNLOADS"] = False
            mark("create-window")
            window = create_window(webview, bridge=bridge)
            mark("window-created")
            if args.deepseek:
                def check_origin():
                    if window.real_url != f"http://127.0.0.1:{asset_port}/":
                        raise ValueError("Owned window origin changed")
                    mark("before-load")
                window.events.before_load += check_origin
            watchdog = Timer(240, window.destroy)
            watchdog.daemon = True
            watchdog.start()
            mark("native-loop")
            webview.start(gui="edgechromium")
            mark("native-loop-returned")
            if args.deepseek and (len(starts) != 1 or not bound.is_set()):
                raise ValueError("Owned asset listener was not started exactly once")
        finally:
            if watchdog is not None:
                watchdog.cancel()
            bridge._finish_assistant_close()
            # Counts only: never persist key, source, question or provider body.
            resources = {
                "fakeOnly": True, "sends": provider.sends, "providerClosed": provider.closed,
                "cancelCalls": provider.cancelled, "runtimeShutdown": True,
            } if not args.deepseek else {
                "fakeOnly": True, "productionSDK": True, "sends": len(provider.requests),
                "providerClosed": provider.released(), "runtimeShutdown": True,
                "externalAttempts": len(attempts), "originalRequests": len(provider.requests) == 4,
            }
            (args.output / "host-exit.json").write_text(json.dumps(resources), encoding="utf-8")


if __name__ == "__main__":
    main()
