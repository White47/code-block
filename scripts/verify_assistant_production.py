"""Fixed offline production-SDK probe. Never accepts a key, project or endpoint."""

import argparse
import asyncio
import hashlib
import importlib.metadata as metadata
import json
import runpy
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from unittest.mock import patch

_SDK_VERSIONS = {"pydantic-ai-slim": "2.55.0", "openai": "3.29.0",
                 "httpx2": "2.13.1", "httpcore2": "2.13.1", "pydantic": "2.14.1"}
_KEY = "offline-production-only"  # Inert fixture value, never a user credential.


class ProbeFailure(ValueError):
    """Only fixed, application-owned diagnostic labels may reach the receipt."""


def _need(condition, label):
    if not condition:
        raise ProbeFailure(label)


def _frame(delta, finish=None, *, usage=None):
    value = {"id": "owned-production", "object": "chat.completion.chunk", "created": 0,
             "model": "deepseek-flash", "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
    if usage is not None:
        value["usage"] = usage
    return b"data: " + json.dumps(value, ensure_ascii=False).encode() + b"\n\n"


class FixtureHTTP:
    """The only replacement is HTTP transport; app, SDK and accounting are real."""

    def __init__(self, runtime, kinds):
        self.runtime, self.kinds = runtime, kinds
        self.requests, self.adapters, self.transports, self.streams = [], [], [], []
        self.stall = False
        self.blocked = Event()

    def _answer(self, wire):
        evidence = []
        for message in wire["messages"]:
            if message.get("role") != "user" or type(message.get("content")) is not str:
                continue
            try:
                header = json.loads(message["content"].split("\n", 1)[0])
            except (ValueError, TypeError):
                continue
            if type(header) is dict and type(header.get("evidenceId")) is str:
                evidence.append(header["evidenceId"])
        _need(bool(evidence), "source evidence was not carried by the real SDK")
        sections = [{"kind": kind, "claims": []} for kind in self.kinds]
        sections[0]["claims"] = [{"text": "Saved structure.",
            "basis": "inference", "evidenceIds": evidence[:1], "relationEvidenceId": None}]
        sections[-1]["claims"] = [{"text": "Unable to confirm runtime behavior.",
            "basis": "unknown", "evidenceIds": [], "relationEvidenceId": None}]
        answer = json.dumps({"sections": sections}, separators=(",", ":"))
        _need(type(wire.get("max_tokens")) is int and len(answer.encode()) <= wire["max_tokens"],
              "fixture answer exceeds wire allocation")
        return answer

    def factory(self, modules):
        from codeviz.assistant.sdk_provider import SDKProviderAdapter

        owner, httpx = self, modules["httpx2"]

        class Stream(httpx.AsyncByteStream):
            def __init__(self, chunks, stalled):
                self.chunks, self.stalled, self.closed = chunks, stalled, False
                owner.streams.append(self)

            async def __aiter__(self):
                for index, chunk in enumerate(self.chunks):
                    yield chunk
                    if self.stalled and index == 0:
                        owner.blocked.set()
                        await asyncio.Event().wait()

            async def aclose(self):
                self.closed = True

        class Transport(httpx.AsyncBaseTransport):
            closed = False

            async def handle_async_request(self, request):
                engine = owner.runtime._engine
                with engine._lock:
                    providers = [job.resources.provider for job in engine._connection_jobs.values()]
                    providers += [job.provider for job in engine._jobs.values()]
                    matches = [provider for provider in providers
                        if type(provider) is SDKProviderAdapter and provider._send is not None
                        and provider._send.request is request]
                _need(len(matches) == 1, "transport did not receive the original SDK request")
                _need(not any(previous is request for previous in owner.requests), "request sent twice")
                _need(str(request.url) == "https://api.deepseek.com/v1/chat/completions",
                      "production endpoint changed")
                owner.requests.append(request)
                if matches[0] not in owner.adapters:
                    owner.adapters.append(matches[0])
                wire = json.loads(request.content)
                tools = wire.get("tools", [])
                if tools and tools[0]["function"]["name"] == "p":
                    delta = {"role": "assistant", "tool_calls": [{"index": 0, "id": "c",
                        "type": "function", "function": {"name": "p", "arguments": "{}"}}]}
                    finish = "tool_calls"
                else:
                    fixed_final = not tools and wire["messages"][-1].get("content") == "Return {}."
                    text = "" if tools else "{}" if fixed_final else owner._answer(wire)
                    delta, finish = {"role": "assistant", "content": text}, "stop"
                if len(owner.requests) <= 2:
                    _need(not any("Static fixture must never execute" in str(message.get("content", ""))
                                  for message in wire["messages"]), "connection disclosed fixture source")
                chunks = [_frame(delta), _frame({}, finish, usage={"prompt_tokens": 1,
                    "completion_tokens": 1, "total_tokens": 2}), b"data: [DONE]\n\n"]
                return httpx.Response(200, headers={"content-type": "text/event-stream"},
                                      stream=Stream(chunks, owner.stall), request=request)

            async def aclose(self):
                self.closed = True

        transport = Transport()
        self.transports.append(transport)
        return transport

    def released(self):
        return (all(item.closed for item in self.streams + self.transports)
                and all(item._closed and item._key is None for item in self.adapters))


def _wait(runtime, job_id, epoch=None):
    from codeviz.assistant.bridge_requests import PollRequest

    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        state = runtime.poll(PollRequest(job_id, epoch, 0))
        if state["resourcesReleased"]:
            return state
        time.sleep(0.005)
    raise ProbeFailure("job did not release within the offline probe deadline")


def _completed(state, label):
    if state["status"] != "Completed":
        error = state.get("issue")
        code = error.get("code", "unknown") if type(error) is dict else "unknown"
        raise ProbeFailure(label + ": " + code)


def run_probe():
    scripts = Path(__file__).resolve().parent
    dependencies = runpy.run_path(str(scripts / "verify_assistant_dependencies.py"))
    with dependencies["offline_guard"]() as attempts, TemporaryDirectory(prefix="codeviz-production-") as work:
        try:
            versions = {name: metadata.version(name) for name in _SDK_VERSIONS}
        except metadata.PackageNotFoundError:
            return {"status": "skipped", "fakeOnly": True, "reason": "Audited optional SDK not installed"}
        _need(versions == _SDK_VERSIONS, "audited optional SDK versions differ")
        fixture = runpy.run_path(str(scripts / "assistant_smoke_host.py"))
        from codeviz.assistant import _deepseek_admission as admission
        from codeviz.assistant import sdk_provider
        from codeviz.assistant.bridge_requests import (
            CancelRequest,
            ConnectionRequest,
            ProjectRequest,
            parse_configure,
            parse_consent,
            parse_resolve,
            parse_start,
        )
        from codeviz.persistence.store import CacheStore
        from codeviz.ui.controller import AppController

        runtime = admission._create_application_runtime()
        http = FixtureHTTP(runtime, fixture["_KINDS"])
        try:
            _need(runtime._connection_provider_factory is admission._production_provider_factory,
                  "application does not use the production factory")
            settings = runtime.get_settings()
            _need(settings["enabled"] is False and settings["keySet"] is False and runtime._engine is None,
                  "application did not start disabled without a key")
            _need(not any(name == prefix or name.startswith(prefix + ".") for name in sys.modules
                          for prefix in ("openai", "pydantic_ai", "httpx2", "httpcore2")),
                  "application startup imported the optional SDK")
            root = Path(work)
            project = root / "project"
            project.mkdir()
            (project / "sample.py").write_text(fixture["_FIXTURE"], encoding="utf-8")
            controller = AppController(CacheStore(root / "cache"), assistant_publication_mirror=runtime)
            controller.open_project(project)
            controller.analyze()
            ctx = controller.capture_assistant_context()
            with patch.object(sdk_provider, "_new_secure_transport", http.factory):
                runtime.configure(parse_configure({"baseUrl": "https://api.deepseek.com/v1",
                    "model": "deepseek-flash", "key": _KEY, "enabled": True}))
                connection = runtime.test_connection(ConnectionRequest(1, "owned-connection"))
                connected = _wait(runtime, connection["jobId"])
                _completed(connected, "source-free connection failed")
                capabilities = connected["connectionResult"]
                _need(capabilities == dict(toolSupported=True, structuredOutputSupported=False,
                    jsonObjectSupported=True, outputValidation="local-json-schema"), "connection capability changed")
                _need(len(http.requests) == 2, "connection did not send exactly two fixed probes")
                conversation = runtime.new_conversation(ProjectRequest(ctx.project_epoch), ctx)["conversationId"]
                grant = runtime.grant_consent(parse_consent({"projectEpoch": ctx.project_epoch,
                    "analysisGeneration": ctx.analysis_generation, "configRevision": 1,
                    "allowedFiles": ["sample.py"], "confirmed": True}), ctx)
                explanation = runtime.start(parse_start({"projectEpoch": ctx.project_epoch,
                    "analysisGeneration": ctx.analysis_generation, "conversationId": conversation,
                    "requestId": "owned-explanation", "configRevision": 1,
                    "consentRevision": grant["consentRevision"], "question": "Explain this saved source.",
                    "context": {"kind": "source", "filePath": "sample.py", "mode": "file", "anchor": None}}), ctx)
                explained = _wait(runtime, explanation["jobId"], ctx.project_epoch)
                _completed(explained, "source explanation failed")
                answer = explained["answer"]
                _need(len(answer["sections"]) == 7 and bool(answer["evidence"]), "validated answer lacks local evidence")
                evidence_id = answer["evidence"][0]["evidenceId"]
                navigation = runtime.resolve_evidence(parse_resolve({"projectEpoch": ctx.project_epoch,
                    "analysisGeneration": ctx.analysis_generation, "conversationId": conversation,
                    "evidenceId": evidence_id, "action": "source"}), ctx)
                resolved = _wait(runtime, navigation["jobId"], ctx.project_epoch)
                _completed(resolved, "retained source evidence did not resolve")
                _need(resolved["target"]["astAnchor"]["filePath"] == "sample.py"
                      and resolved["target"]["evidenceId"] == evidence_id, "source evidence resolved elsewhere")
                _need(len(http.requests) == 4, "explanation or navigation added unexpected requests")
                http.stall = True
                pending = runtime.test_connection(ConnectionRequest(1, "owned-cancel"))
                _need(http.blocked.wait(5), "cancel probe did not enter the fake stream")
                runtime.cancel(CancelRequest(pending["jobId"], None))
                cancelled = _wait(runtime, pending["jobId"])
                _need(cancelled["status"] == "Cancelled" and len(http.requests) == 5,
                      "cancelled connection sent another request")
            runtime.shutdown()
            _need(http.released() and len(http.transports) == len(http.requests), "production resources remain owned")
            _need(not attempts, "external communication attempted")
            package = Path(admission.__file__).parent
            digest = hashlib.sha256()
            assembly_files = ("_deepseek_admission.py", "sdk_provider.py", "connection.py", "connection_profile.py",
                              "service.py", "response_session.py")
            for name in assembly_files:
                digest.update(name.encode() + (package / name).read_bytes())
            return {"status": "passed", "fakeOnly": True, "profile": admission._PROFILE_ID,
                "artifact": "python-package", "packageRoot": str(package.resolve()),
                "assemblySha256": digest.hexdigest(), "assemblyFiles": list(assembly_files), "sdkVersions": versions,
                "requests": len(http.requests), "externalAttempts": len(attempts), "originalRequests": True,
                "released": True, "capabilities": capabilities, "sections": 7, "sourceEvidence": True,
                "cancelled": True, "checks": ["disabled-startup", "source-free-connection", "local-answer",
                    "retained-source-navigation", "cancelled-stream", "cleanup", "offline-guard"]}
        finally:
            runtime.shutdown()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fake-only", action="store_true", required=True)
    parser.parse_args(argv)
    try:
        report = run_probe()
    except ProbeFailure as error:
        report = {"status": "failed", "fakeOnly": True, "reason": str(error)}
    except Exception as error:
        report = {"status": "failed", "fakeOnly": True, "reason": "offline production probe failed",
                  "errorType": type(error).__name__}
    print(json.dumps(report, ensure_ascii=True))
    return 0 if report["status"] == "passed" else 2 if report["status"] == "skipped" else 1


if __name__ == "__main__":
    raise SystemExit(main())
