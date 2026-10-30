"""C1 public fake SDK gate, not an application provider. No installs/app writes.

Use --isolated --fake-only --python-env <preinstalled venv>. SDK imports are
lazy and guarded. Reports contain metadata and synthetic request facts only.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib
import importlib.metadata as metadata
import json
import logging
import os
import socket
import ssl
import subprocess
import sys
import threading
import time
from contextlib import ExitStack, contextmanager
from dataclasses import FrozenInstanceError, dataclass
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV = ROOT / "build/assistant-sdk-probe-20261012"
ENDPOINT = "https://sdk-probe.invalid/v1/chat/completions"
SOURCE = "PUBLIC_FAKE_SOURCE_中文_😀"
LIMIT, MAX_BODY = 37, 65536
RESULTS: list[dict] = []


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def record(name, status, detail):
    RESULTS.append({"probe": name, "status": status, "detail": detail})


def isolated_environment(area=None):
    """Clear ambient secrets while keeping Windows/cache expansion deterministic."""
    area = area or ROOT / "build/assistant-gate-environment-20261012"
    check(area.resolve().is_relative_to((ROOT / "build").resolve()), "cache area must remain in build")
    for part in ("temp", "profile", "appdata", "programdata"):
        (area / part).mkdir(parents=True, exist_ok=True)
    return {"SystemDrive": "C:", "SystemRoot": r"C:\Windows",
        "PROGRAMDATA": str(area / "programdata"), "USERPROFILE": str(area / "profile"),
        "APPDATA": str(area / "appdata"), "LOCALAPPDATA": str(area / "appdata"),
        "TEMP": str(area / "temp"), "TMP": str(area / "temp"),
        "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"}


@contextmanager
def offline_guard(extra_environment=None, *, native_loopback_ports=()):
    """Deny outbound TCP/UDP/DNS before imports; loop socketpair is allowed."""
    attempts = []
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    if (type(native_loopback_ports) is not tuple or len(native_loopback_ports) > 2
            or any(type(port) is not int or not 1 <= port <= 65535 for port in native_loopback_ports)):
        raise ValueError("Invalid owned native ports")
    ports = frozenset(native_loopback_ports)

    def owned_pair(family=socket.AF_INET, type=socket.SOCK_STREAM, proto=0):
        # Windows implements socketpair in Python using connect(), which is
        # intentionally denied below. Only these newly owned peers may connect;
        # never unpatch a global method or authorize a caller-supplied endpoint.
        if family not in (socket.AF_INET, socket.AF_INET6) or type != socket.SOCK_STREAM or proto != 0:
            raise ValueError("Unsupported owned socketpair")
        host = "127.0.0.1" if family == socket.AF_INET else "::1"
        client = server = None
        try:
            with socket.socket(family, type, proto) as listener:
                listener.settimeout(2)
                listener.bind((host, 0))
                listener.listen(1)
                client = socket.socket(family, type, proto)
                client.settimeout(2)
                original_connect(client, listener.getsockname())
                server, _ = listener.accept()
                if (server.getsockname() != client.getpeername()
                        or client.getsockname() != server.getpeername()):
                    raise ConnectionError("Unexpected owned socketpair peer")
            server.settimeout(None)
            client.settimeout(None)
            return server, client
        except BaseException:
            if server is not None:
                server.close()
            if client is not None:
                client.close()
            raise

    def denied(*args, **kwargs):
        attempts.append("blocked")
        raise AssertionError("real network forbidden")

    def allowed(address):
        return (type(address) is tuple and len(address) == 2
                and type(address[0]) is str and address[0] == "127.0.0.1"
                and type(address[1]) is int and address[1] in ports)

    def native_connect(sock, address):
        if sock.family != socket.AF_INET or sock.type != socket.SOCK_STREAM or not allowed(address):
            return denied()
        return original_connect(sock, address)

    def native_connect_ex(sock, address):
        if sock.family != socket.AF_INET or sock.type != socket.SOCK_STREAM or not allowed(address):
            return denied()
        return original_connect_ex(sock, address)

    def local_name(host):
        if type(host) is not str or host not in ("", "localhost", "127.0.0.1"):
            return denied()
        return "localhost"

    def local_address(host):
        local_name(host)
        return "127.0.0.1"

    def local_lookup(host):
        local_name(host)
        return ("localhost", [], ["127.0.0.1"])

    def local_addrinfo(host, port, family=0, type=0, proto=0, flags=0):
        if (host not in ("localhost", "127.0.0.1")
                or family not in (0, socket.AF_INET) or type not in (0, socket.SOCK_STREAM)
                or proto not in (0, socket.IPPROTO_TCP) or not allowed(("127.0.0.1", port))):
            return denied()
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", port))]

    synthetic = {**isolated_environment(),
        "OPENAI_API_KEY": "synthetic-ambient-key",
        "OPENAI_BASE_URL": "https://ambient.invalid/v9",
        "OPENAI_ORG_ID": "synthetic-ambient-org",
        "OPENAI_PROJECT_ID": "synthetic-ambient-project",
        "OPENAI_WEBHOOK_SECRET": "synthetic-ambient-webhook",
        "HTTP_PROXY": "http://ambient-proxy.invalid:9",
        "HTTPS_PROXY": "http://ambient-proxy.invalid:9",
        "OTEL_EXPORTER_OTLP_ENDPOINT": "https://ambient-telemetry.invalid",
        "LOGFIRE_TOKEN": "synthetic-ambient-logfire",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    if extra_environment:
        synthetic.update(extra_environment)
    with ExitStack() as stack:
        stack.enter_context(patch.dict(os.environ, synthetic, clear=True))
        stack.enter_context(patch.object(socket, "getnameinfo", denied))
        if os.name == "nt":
            stack.enter_context(patch.object(socket, "socketpair", owned_pair))
        if ports:
            for name, value in (("getaddrinfo", local_addrinfo), ("gethostbyname", local_address),
                                ("gethostbyname_ex", local_lookup), ("gethostbyaddr", local_lookup),
                                ("getfqdn", lambda name="": local_name(name))):
                stack.enter_context(patch.object(socket, name, value))
        else:
            for name in ("getaddrinfo", "gethostbyname", "gethostbyname_ex", "gethostbyaddr",
                         "getfqdn", "create_connection"):
                stack.enter_context(patch.object(socket, name, denied))
        stack.enter_context(patch.object(socket.socket, "connect", native_connect if ports else denied))
        stack.enter_context(patch.object(socket.socket, "connect_ex", native_connect_ex if ports else denied))
        stack.enter_context(patch.object(socket.socket, "sendto", denied))
        old_logging = logging.root.manager.disable
        logging.disable(logging.CRITICAL)
        try:
            yield attempts
        finally:
            logging.disable(old_logging)


@dataclass(frozen=True, slots=True)
class FrozenWire:
    """Read-only synthetic view, no headers or secret references in repr."""
    method: str
    endpoint: str
    body: bytes
    output_limit: int


def fingerprint(request):
    # Header integrity is private to the gate and is never reported.
    return request.method, str(request.url), request.content, tuple(request.headers.raw)


def make_gate(httpx2, handler):
    class CaptureGate(httpx2.AsyncBaseTransport):
        def __init__(self):
            self.ready, self.release = asyncio.Event(), asyncio.Event()
            self.request = self.wire = self.private_fingerprint = None
            self.entries = self.sends = 0
            self.consumed = self.cancelled = self.closed = False
            self.responses = []
            self.mock = httpx2.MockTransport(handler)

        async def handle_async_request(self, request):
            self.entries += 1
            check(self.entries == 1 and not self.cancelled, "second transport entry rejected")
            # Never aread() an arbitrary/unbounded framework request stream.
            body = request.content
            check(type(body) is bytes and len(body) <= MAX_BODY, "bounded replayable body required")
            check(request.method == "POST" and str(request.url) == ENDPOINT, "target mismatch")
            check(request.headers.get("content-type") == "application/json", "content type mismatch")
            check(request.headers.get("content-length") == str(len(body)), "length mismatch")
            envelope = json.loads(body)
            fields = [k for k in ("max_tokens", "max_completion_tokens") if k in envelope]
            check(len(fields) == 1 and envelope[fields[0]] == LIMIT, "output envelope mismatch")
            self.request, self.private_fingerprint = request, fingerprint(request)
            self.wire = FrozenWire(request.method, str(request.url), body, LIMIT)
            self.ready.set()
            await self.release.wait()
            check(not self.cancelled and not self.closed and self.consumed, "dispatch lacks live permit")
            check(fingerprint(request) == self.private_fingerprint, "request changed after preparation")
            self.sends += 1
            response = await self.mock.handle_async_request(request)
            self.responses.append(response)
            return response

        def approve(self, wire, input_bound, output_bound):
            check(wire is self.wire and not self.consumed, "invalid/reused preparation handle")
            check(not self.cancelled and not self.closed, "cancelled permit")
            check(input_bound >= len(wire.body) and output_bound >= LIMIT, "budget insufficient")
            check(fingerprint(self.request) == self.private_fingerprint, "request changed before approval")
            self.consumed = True
            self.release.set()

        async def aclose(self):
            self.closed = self.cancelled = True
            for response in self.responses:
                await response.aclose()
            await self.mock.aclose()

    return CaptureGate()


async def sdk_probes():
    # Actual installed low-level Model API, not Agent.run: no automatic loop,
    # tool executor, repair, remote files, prices updater or instrumentation.
    httpx2 = importlib.import_module("httpx2")
    openai = importlib.import_module("openai")
    models = importlib.import_module("pydantic_ai.models")
    messages = importlib.import_module("pydantic_ai.messages")
    sdk = importlib.import_module("pydantic_ai.models.openai")
    providers = importlib.import_module("pydantic_ai.providers.openai")
    profiles = importlib.import_module("pydantic_ai.profiles.openai")
    output = importlib.import_module("pydantic_ai.output")
    tools = importlib.import_module("pydantic_ai.tools")
    tls = ssl.create_default_context()
    check(tls.verify_mode == ssl.CERT_REQUIRED and tls.check_hostname, "TLS defaults weakened")

    def client(gate, ambient=False):
        http = httpx2.AsyncClient(transport=gate, verify=tls, follow_redirects=False,
                                 trust_env=False, timeout=2.0)
        kwargs = {} if ambient else {"organization": "", "project": "", "webhook_secret": ""}
        api = openai.AsyncOpenAI(api_key="synthetic-explicit-key", base_url="https://sdk-probe.invalid/v1",
                                http_client=http, max_retries=0, **kwargs)
        check(api.max_retries == 0 and not http.follow_redirects and not http.trust_env, "unsafe defaults")
        check(not http.event_hooks["request"] and not http.event_hooks["response"], "unexpected hooks")
        return http, api

    schema = {"type": "object", "properties": {"answer": {"type": "string"}},
              "required": ["answer"], "additionalProperties": False}
    tool_schema = {"type": "object", "properties": {"tag": {"type": "string"}},
                   "required": ["tag"], "additionalProperties": False}
    prompt = [messages.ModelRequest(parts=[messages.UserPromptPart(content=SOURCE)])]

    def params(mode):
        if mode == "tool":
            return models.ModelRequestParameters(function_tools=[tools.ToolDefinition(
                name="read_source_excerpt", parameters_json_schema=tool_schema, strict=True)])
        if mode == "native":
            return models.ModelRequestParameters(output_mode="native", output_object=output.OutputObjectDefinition(
                json_schema=schema, name="FakeAnswer", strict=True))
        return models.ModelRequestParameters()

    async def prepared_case(mode="native", status=200, ambient=False, legacy=False,
                            cancel=False, tamper=False, insufficient=False, bad_schema=False,
                            tool_history=False, connection_error=False, timeout_error=False):
        received = []
        request_messages = prompt
        expected_messages = [{"role": "user", "content": SOURCE}]
        if tool_history:
            # Service, rather than SDK, owns the explicit next call and tool return.
            request_messages = [*prompt, messages.ModelResponse(parts=[messages.ToolCallPart(
                tool_name="read_source_excerpt", args={"tag": "public"}, tool_call_id="fake-call")]),
                messages.ModelRequest(parts=[messages.ToolReturnPart(tool_name="read_source_excerpt",
                    content=SOURCE, tool_call_id="fake-call")])]
            expected_messages += [
                {"role": "assistant", "content": None, "tool_calls": [{"id": "fake-call", "type": "function",
                    "function": {"name": "read_source_excerpt", "arguments": '{"tag":"public"}'}}]},
                {"role": "tool", "tool_call_id": "fake-call", "content": SOURCE},
            ]

        def handler(request):
            check(request is gate.request and request.content is gate.wire.body, "request/bytes identity changed")
            check(fingerprint(request) == gate.private_fingerprint, "underlying request drift")
            received.append(request)
            if connection_error:
                raise httpx2.ConnectError("synthetic connection failure", request=request)
            if timeout_error:
                raise httpx2.ReadTimeout("synthetic timeout", request=request)
            if status != 200:
                return httpx2.Response(status, headers={"location": "https://redirect.invalid/v1"},
                                       json={"error": {"message": "synthetic failure", "type": "probe"}})
            message = {"role": "assistant", "content": '{"answer":1,"extra":true}' if bad_schema else '{"answer":"fake"}'}
            if mode == "tool":
                message = {"role": "assistant", "content": None, "tool_calls": [{
                    "id": "fake-call", "type": "function", "function": {
                        "name": "read_source_excerpt", "arguments": '{"tag":"public"}'}}]}
            return httpx2.Response(200, json={"id": "fake-completion", "object": "chat.completion",
                "created": 0, "model": "gpt-4o-mini", "choices": [{"index": 0, "message": message,
                "finish_reason": "tool_calls" if mode == "tool" else "stop"}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}})

        gate = make_gate(httpx2, handler)
        http, api = client(gate, ambient)
        model = sdk.OpenAIChatModel("gpt-4o-mini", provider=providers.OpenAIProvider(openai_client=api),
            profile=profiles.OpenAIModelProfile(supports_json_schema_output=True,
                openai_chat_supports_max_completion_tokens=not legacy))
        settings = {"max_tokens": LIMIT, "parallel_tool_calls": False}
        if mode != "tool":
            settings["tool_choice"] = "none"
        task = asyncio.create_task(model.request(request_messages, settings, params(mode)))
        try:
            waiter = asyncio.create_task(gate.ready.wait())
            try:
                done, _ = await asyncio.wait([task, waiter], timeout=3, return_when=asyncio.FIRST_COMPLETED)
                check(waiter in done and gate.ready.is_set(), "SDK did not reach capture seam")
                check(not task.done() and not received and gate.sends == 0, "prepare sent request")
            finally:
                waiter.cancel()
                await asyncio.gather(waiter, return_exceptions=True)
            wire, body = gate.wire, json.loads(gate.wire.body)
            check(body["messages"] == expected_messages, "synthetic source attribution changed")
            check(wire.body.decode().count(SOURCE) == (2 if tool_history else 1), "unexpected source duplication/encoding")
            check(body["model"] == "gpt-4o-mini", "model mapping changed")
            check(wire.body != json.dumps({"prompt": SOURCE}, ensure_ascii=False).encode(), "draft confused with wire")
            try:
                wire.body = b"changed"
            except FrozenInstanceError:
                pass
            else:
                raise AssertionError("public body mutable")
            if ambient:
                check(gate.request.headers.get("openai-organization") == "synthetic-ambient-org" and
                      gate.request.headers.get("openai-project") == "synthetic-ambient-project", "ambient hazard not reproduced")
            else:
                check(not gate.request.headers.get("openai-organization") and not gate.request.headers.get("openai-project"),
                      "ambient metadata leaked")
                check(all("ambient" not in value for value in gate.request.headers.values()), "ambient header leaked")
            check(gate.request.headers.get("authorization") == "Bearer synthetic-explicit-key", "ambient key fallback")
            if mode == "tool":
                definition = body["tools"][0]["function"]
                check(definition["name"] == "read_source_excerpt" and definition["strict"] is True and
                      definition["parameters"] == tool_schema and body["parallel_tool_calls"] is False, "tool mapping changed")
            elif mode == "native":
                fmt = body["response_format"]
                check(fmt["type"] == "json_schema" and fmt["json_schema"]["strict"] is True and
                      fmt["json_schema"]["schema"] == schema and body.get("tool_choice") in (None, "none") and "tools" not in body,
                      "strict final wire changed")
            if cancel:
                gate.cancelled = True
                try:
                    gate.approve(wire, MAX_BODY, LIMIT)
                except AssertionError:
                    pass
                else:
                    raise AssertionError("cancelled preparation accepted permit")
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                check(task.cancelled() and not received, "cancelled prepare sent request")
                return "suspended continuation cancelled; zero sends"
            if tamper or insufficient:
                if tamper:
                    gate.request._content = b'{"changed":true}'  # Deliberate negative test only.
                try:
                    gate.approve(wire, len(wire.body) - 1 if insufficient else MAX_BODY, LIMIT)
                except AssertionError:
                    check(not received, "unapproved request escaped")
                    return "mutation/insufficient final-byte budget rejected; zero sends"
                raise AssertionError("invalid approval accepted")
            gate.approve(wire, len(wire.body), LIMIT)
            try:
                gate.approve(wire, len(wire.body), LIMIT)
            except AssertionError:
                pass
            else:
                raise AssertionError("permit reused")
            try:
                response = await asyncio.wait_for(task, 3)
            except Exception as exc:
                expected_error = status != 200 or connection_error or timeout_error
                check(expected_error and isinstance(exc, importlib.import_module("pydantic_ai.exceptions").ModelAPIError),
                      "fake completion failed or unexpected error type")
            else:
                check(status == 200, "HTTP error accepted")
                if mode == "tool":
                    check(any(isinstance(p, messages.ToolCallPart) for p in response.parts), "tool not surfaced")
                else:
                    text = next(p.content for p in response.parts if isinstance(p, messages.TextPart))
                    if bad_schema:
                        # Low-level Model.request does NOT validate the final answer.
                        pydantic = importlib.import_module("pydantic")

                        class Answer(pydantic.BaseModel):
                            model_config = pydantic.ConfigDict(strict=True, extra="forbid")
                            answer: str

                        try:
                            Answer.model_validate_json(text)
                        except pydantic.ValidationError:
                            pass
                        else:
                            raise AssertionError("invalid answer accepted locally")
                    else:
                        check(json.loads(text) == {"answer": "fake"}, "response mismatch")
            check(gate.entries == gate.sends == len(received) == 1, "hidden retry/redirect/tool loop")
            return f"same request/bytes, one send; wire={len(wire.body)} bytes; output={LIMIT}"
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await api.close()
            check(http.is_closed and gate.closed and all(r.is_closed for r in gate.responses), "resource leak")

    cases = [
        ("prepare_freeze_tool_manual_loop", {"mode": "tool"}),
        ("prepare_freeze_strict_final", {}),
        ("profile_max_tokens_field", {"legacy": True}),
        ("cancel_prepare_zero_dispatch", {"cancel": True}),
        ("reject_wire_mutation", {"tamper": True}),
        ("reject_insufficient_final_byte_budget", {"insufficient": True}),
        ("ambient_metadata_hazard_reproduced", {"ambient": True}),
        ("invalid_schema_no_automatic_repair", {"bad_schema": True}),
        ("manual_tool_return_then_strict_final", {"tool_history": True}),
        ("connection_error_no_hidden_retry", {"connection_error": True}),
        ("timeout_no_hidden_retry", {"timeout_error": True}),
    ] + [(f"http_{status}_no_retry_redirect", {"status": status}) for status in (401, 429, 500, 307)]
    for name, kwargs in cases:
        try:
            record(name, "pass", await prepared_case(**kwargs))
        except Exception as exc:
            record(name, "fail", str(exc) if isinstance(exc, AssertionError) else type(exc).__name__)

    class FakeSSE(httpx2.AsyncByteStream):
        def __init__(self, block):
            self.block, self.closed = block, False
            self.waiting = asyncio.Event()

        async def __aiter__(self):
            for fragment in ("fake ", "stream"):
                payload = {"id": "fake-stream", "object": "chat.completion.chunk", "created": 0,
                    "model": "gpt-4o-mini", "choices": [{"index": 0, "delta": {"content": fragment}, "finish_reason": None}]}
                yield ("data: " + json.dumps(payload) + "\n\n").encode()
                if self.block:
                    self.waiting.set()
                    await asyncio.Event().wait()
            yield b'data: {"id":"fake-stream","object":"chat.completion.chunk","created":0,"model":"gpt-4o-mini","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
            yield b"data: [DONE]\n\n"

        async def aclose(self):
            self.closed = True

    async def stream_case(cancel):
        stream = FakeSSE(cancel)
        gate = make_gate(httpx2, lambda request: httpx2.Response(200,
            headers={"content-type": "text/event-stream"}, stream=stream))
        http, api = client(gate)
        model = sdk.OpenAIChatModel("gpt-4o-mini", provider=providers.OpenAIProvider(openai_client=api))
        fragments = []

        async def consume():
            async with model.request_stream(prompt, {"max_tokens": LIMIT, "tool_choice": "none"}, params("text")) as response:
                async for event in response:
                    if isinstance(event, messages.PartStartEvent) and isinstance(event.part, messages.TextPart):
                        fragments.append(event.part.content)
                    elif isinstance(event, messages.PartDeltaEvent) and isinstance(event.delta, messages.TextPartDelta):
                        fragments.append(event.delta.content_delta)

        task = asyncio.create_task(consume())
        try:
            await asyncio.wait_for(gate.ready.wait(), 3)
            check(gate.sends == 0, "stream preparation sent early")
            gate.approve(gate.wire, len(gate.wire.body), LIMIT)
            if cancel:
                await asyncio.wait_for(stream.waiting.wait(), 3)
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                check(task.cancelled(), "stream cancellation failed")
            else:
                await asyncio.wait_for(task, 3)
                check("".join(fragments) == "fake stream" and len(fragments) >= 2, "incremental stream missing")
            check(gate.entries == gate.sends == 1, "stream implicit send")
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await api.close()
        check(stream.closed and http.is_closed and gate.closed and all(r.is_closed for r in gate.responses), "stream resource leak")
        return "SSE response/stream/client/transport closed; one send"

    for cancel in (False, True):
        name = "cancel_inflight_stream_close" if cancel else "incremental_text_stream_close"
        try:
            record(name, "pass", await stream_case(cancel))
        except Exception as exc:
            record(name, "fail", str(exc) if isinstance(exc, AssertionError) else type(exc).__name__)
    try:
        bad_requests = [
            httpx2.Request("POST", "https://wrong.invalid/v1", json={"max_tokens": LIMIT}),
            httpx2.Request("POST", ENDPOINT, json={"max_tokens": LIMIT + 1}),
            httpx2.Request("POST", ENDPOINT, json={"max_tokens": LIMIT, "max_completion_tokens": LIMIT}),
            httpx2.Request("POST", ENDPOINT, json={"max_tokens": LIMIT, "padding": "x" * MAX_BODY}),
            httpx2.Request("POST", ENDPOINT, content=b"untyped opaque body"),
        ]

        class Unmaterialized(httpx2.AsyncByteStream):
            async def __aiter__(self):
                raise AssertionError("opaque request stream must not be read")
                yield b""  # pragma: no cover

        bad_requests.append(httpx2.Request("POST", ENDPOINT, stream=Unmaterialized()))
        for request in bad_requests:
            gate = make_gate(httpx2, lambda request: completion_impossible())
            try:
                await asyncio.wait_for(gate.handle_async_request(request), 1)
            except (AssertionError, httpx2.RequestNotRead):
                pass
            else:
                raise AssertionError("invalid/opaque request passed capture")
            check(gate.sends == 0, "negative capture sent request")
            await gate.aclose()
        gate = make_gate(httpx2, lambda request: httpx2.Response(200, json={}))
        request = httpx2.Request("POST", ENDPOINT, json={"max_tokens": LIMIT})
        task = asyncio.create_task(gate.handle_async_request(request))
        await asyncio.wait_for(gate.ready.wait(), 1)
        gate.approve(gate.wire, MAX_BODY, LIMIT)
        await asyncio.wait_for(task, 1)
        try:
            await gate.handle_async_request(request)
        except AssertionError:
            pass
        else:
            raise AssertionError("second transport entry accepted")
        check(gate.sends == 1, "second send escaped")
        await gate.aclose()
        record("negative_capture_contracts", "pass", "wrong target/limit, duplicate limits, oversized/opaque streams and second entry rejected")
    except Exception as exc:
        record("negative_capture_contracts", "fail", str(exc) if isinstance(exc, AssertionError) else type(exc).__name__)
    record("tls_proxy_retry_settings", "pass", "CERT_REQUIRED/hostname; mock, trust_env=False, redirects=False, retries=0")


def completion_impossible():
    raise AssertionError("invalid request reached underlying transport")


def inventory():
    graph = []
    for dist in sorted(metadata.distributions(), key=lambda d: d.metadata["Name"].lower()):
        files = list(dist.files or [])
        records = [p for p in files if str(p).endswith(".dist-info/RECORD")]
        notices = [p for p in files if Path(str(p)).name.lower().startswith(("license", "copying", "notice"))
                   and not str(p).endswith((".py", ".pyc"))]
        graph.append({"name": dist.metadata["Name"], "version": dist.version,
            "license": dist.metadata.get("License-Expression") or (dist.metadata.get("License") or "see notice").splitlines()[0],
            "requires": dist.requires or [],
            "license_paths": [str(dist.locate_file(p)) for p in notices],
            "license_sha256": [hashlib.sha256(dist.locate_file(p).read_bytes()).hexdigest() for p in notices],
            "record_sha256": hashlib.sha256(dist.locate_file(records[0]).read_bytes()).hexdigest() if records else None})
    slim = metadata.distribution("pydantic-ai-slim")
    check(slim.version == "2.55.0" and "openai" in slim.metadata.get_all("Provides-Extra", []), "candidate extra/version mismatch")
    for name, version in {"openai": "3.29.0", "httpx2": "2.13.1", "pydantic": "2.14.1"}.items():
        check(metadata.version(name) == version, "tested dependency graph changed")
    record("installed_sdk_metadata", "pass", "slim[openai]==2.55.0; exact graph and RECORD hashes reported")
    record("download_artifact_hash_lock", "skipped", "RECORD hashes are not wheel/sdist hashes; no acquisition artifact lock")
    missing_notices = [item["name"] for item in graph if not item["license_paths"]]
    record("transitive_license_notice_files", "skipped" if missing_notices else "pass",
           "installed metadata has no standalone notice for: " + ", ".join(missing_notices) if missing_notices else "all notice paths present")
    return graph


def offline_app_imports():
    """Import only: no GUI, project loading, user settings or app instance."""
    sys.path.insert(0, str(ROOT / "src"))
    with offline_guard() as attempts:
        for name in ("codeviz", "codeviz.assistant", "codeviz.ui.controller", "codeviz.ui.desktop"):
            importlib.import_module(name)
        controller_module = importlib.import_module("codeviz.ui.controller")
        controller = controller_module.AppController(cache_store=None)
        check(controller.project_root is None and controller.status == controller_module.AnalysisStatus.NOT_ANALYZED,
              "ordinary controller startup failed")
        parser_module = importlib.import_module("codeviz.analysis.python")
        handles = importlib.import_module("codeviz.source_handles")
        public_bytes = b"def public_fake(value):\n    return value + 1\n"
        with patch.object(parser_module, "read_source_bytes", return_value=handles.SourceRead(
                public_bytes, hashlib.sha256(public_bytes).hexdigest())):
            parsed = parser_module.parse_python_file(ROOT, "public_fake.py")
            check(parsed.was_parsed and not parsed.issues and parsed.tree is not None, "offline synthetic parsing failed")
        check(not any(n.startswith(("pydantic_ai", "openai", "logfire")) for n in sys.modules), "eager optional SDK import")
        check(not attempts, "base app attempted network")
    check(importlib.util.find_spec("pydantic_ai") is None, "base env contains SDK")
    return "base imports/controller init/synthetic parsing without extras; native GUI not tested"


def run_probes(frozen_host=False):
    graph = []
    # Windows asyncio socketpair initialization uses a local connect. Initialize
    # stdlib loop plumbing before the outbound guard; SDK imports stay guarded.
    loop = asyncio.new_event_loop()
    with offline_guard() as attempts:
        try:
            if not frozen_host:
                graph = inventory()
            loop.run_until_complete(sdk_probes())
        except Exception as exc:
            record("sdk_import_or_harness", "fail", str(exc) if isinstance(exc, AssertionError) else type(exc).__name__)
        finally:
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.run_until_complete(loop.shutdown_default_executor())
            loop.close()
        record("no_dns_socket_telemetry", "pass" if not attempts else "fail", f"outbound attempts={len(attempts)}; guarded import through teardown")
    base = ROOT / ".desktop-venv/Scripts/python.exe"
    if base.is_file() and not frozen_host:
        code = "import runpy; m=runpy.run_path('scripts/verify_assistant_dependencies.py'); print(m['offline_app_imports']())"
        child = subprocess.run([str(base), "-B", "-c", code], cwd=ROOT, capture_output=True,
            timeout=30, env=isolated_environment())
        record("base_app_missing_extras_lazy_import", "pass" if child.returncode == 0 else "fail",
               "imports/controller init/synthetic parsing without SDK in .desktop-venv" if child.returncode == 0 else "base-only import check failed")
    else:
        record("base_app_missing_extras_lazy_import", "skipped", "not part of frozen SDK-only host" if frozen_host else ".desktop-venv unavailable")
    record("pyinstaller_sdk_smoke", "pass" if frozen_host else "skipped",
           "actual frozen SDK host executed; no full app EXE E claim" if frozen_host else "run --freeze-sdk after main finishes isolated PyInstaller installation")
    record("production_service_authorization_source_gate", "skipped", "only synthetic byte reservation; production C contracts separate")
    expected = ["installed_sdk_metadata", "download_artifact_hash_lock", "transitive_license_notice_files",
        "prepare_freeze_tool_manual_loop", "prepare_freeze_strict_final", "profile_max_tokens_field",
        "cancel_prepare_zero_dispatch", "reject_wire_mutation", "reject_insufficient_final_byte_budget",
        "ambient_metadata_hazard_reproduced", "invalid_schema_no_automatic_repair",
        "manual_tool_return_then_strict_final", "connection_error_no_hidden_retry", "timeout_no_hidden_retry",
        "http_401_no_retry_redirect", "http_429_no_retry_redirect", "http_500_no_retry_redirect",
        "http_307_no_retry_redirect", "incremental_text_stream_close", "cancel_inflight_stream_close",
        "negative_capture_contracts", "tls_proxy_retry_settings"]
    for name in expected:
        if not any(item["probe"] == name for item in RESULTS):
            record(name, "skipped", "metadata inventory belongs to isolated source run" if frozen_host
                   else "not executed after SDK/import harness failure; no pass inferred")
    print(json.dumps({"scope": "C1 fake isolated only", "python": sys.version.split()[0],
        "environment": str(Path(sys.prefix)), "graph": graph, "probes": RESULTS}, ensure_ascii=True, indent=2))
    return int(any(item["status"] == "fail" for item in RESULTS))


def freeze_sdk(target):
    """Generate only isolated build artifacts from this existing gate entry."""
    check(metadata.version("pyinstaller") == "6.22.3", "isolated PyInstaller pin mismatch")
    check(metadata.version("setuptools") == "84.0.0", "isolated setuptools pin mismatch")
    output = ROOT / "build/assistant-native-sdk-gate-20261012"
    output.mkdir(exist_ok=True)
    temp = output / "temp"
    temp.mkdir(exist_ok=True)
    tool_profile = output / "tool-profile"
    tool_profile.mkdir(exist_ok=True)
    environment = {**isolated_environment(output), "PATH": str(target / "Scripts") + r";C:\Windows\System32;C:\Windows",
        "TEMP": str(temp), "TMP": str(temp), "PYTHONDONTWRITEBYTECODE": "1",
        "USERPROFILE": str(tool_profile), "PYTHONUTF8": "1",
        "PYINSTALLER_CONFIG_DIR": str(output / "cache")}
    command = [sys.executable, "-B", "-m", "PyInstaller", "--noconfirm", "--onedir", "--console", "--noupx",
        "--name", "AssistantSDKProbe", "--specpath", str(output), "--workpath", str(output / "work"),
        "--distpath", str(output / "dist")]
    for module in ("httpx2", "pydantic_ai.models.openai", "pydantic_ai.providers.openai", "genai_prices.data",
                   "truststore", "openai.resources.chat", "openai.resources.chat.completions", "openai.lib.streaming.chat"):
        command += ["--hidden-import", module]
    for distribution in ("pydantic-ai-slim", "pydantic-graph", "pydantic", "openai", "httpx2", "genai-prices"):
        command += ["--copy-metadata", distribution]
    command.append(str(Path(__file__).resolve()))
    started = time.monotonic()
    build = subprocess.run(command, cwd=ROOT, env=environment, capture_output=True, timeout=240)
    executable = output / "dist/AssistantSDKProbe/AssistantSDKProbe.exe"
    report = {"probe": "pyinstaller_sdk_smoke", "build_exit": build.returncode,
              "artifact": str(executable), "elapsed_seconds": round(time.monotonic() - started, 2),
              "build_warning_lines": [line for line in build.stderr.decode(errors="replace").splitlines()
                                      if "WARNING" in line or "ERROR" in line]}
    if build.returncode == 0 and executable.is_file():
        smoke = subprocess.run([str(executable), "--isolated", "--fake-only", "--sdk-host"],
            cwd=output, env=environment, capture_output=True, timeout=45)
        report["smoke_exit"] = smoke.returncode
        try:
            report["smoke"] = json.loads(smoke.stdout)
        except (ValueError, UnicodeError):
            report["smoke"] = {"status": "fail", "detail": "frozen host produced no valid JSON report",
                               "stderr_tail": smoke.stderr.decode(errors="replace")[-2500:]}
        report["status"] = "pass" if smoke.returncode == 0 else "fail"
    else:
        report["status"] = "fail"
        report["build_stderr_tail"] = build.stderr.decode(errors="replace")[-3500:]
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return int(report["status"] != "pass")


def native_ui_host():
    """Actual WebView2 component probe, memory resources only, no HTTP server.

    Native resource interception is a pinned WebView2/pywebview probe seam,
    not an application loader change. Start on about:blank, install handlers,
    then navigate to a synthetic HTTPS document answered entirely in memory.
    """
    payload = json.loads(sys.stdin.buffer.read(8_000_000))
    check(set(payload) == {"bundle", "entry", "html"}, "invalid native fixture")
    area = ROOT / "build/assistant-native-ui-gate-20261012"
    synthetic = {**isolated_environment(area), "Path": r"C:\Windows\System32;C:\Windows", "ProgramFiles": r"C:\Program Files",
        "ProgramFiles(x86)": r"C:\Program Files (x86)", "USERPROFILE": str(area / "profile"),
        "APPDATA": str(area / "appdata"), "LOCALAPPDATA": str(area / "appdata"),
        "TEMP": str(area / "temp"), "TMP": str(area / "temp"),
        # Hidden component tests must retain foreground timer cadence without
        # showing a window. The installed WebView2 runtime contains this switch;
        # timer samples in the page prove its effect, not just its presence.
        "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS": "--disable-background-networking --disable-component-update --disable-domain-reliability --disable-client-side-phishing-detection --no-first-run --disable-sync --disable-breakpad --disable-background-timer-throttling"}
    report = {"probe": "native_webview2_component", "status": "fail", "detail": "native host did not finish"}
    with offline_guard(synthetic) as attempts:
        webview = importlib.import_module("webview")
        check(metadata.version("pywebview") == "6.2.1", "pywebview pin mismatch")
        webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = False
        webview.settings["ALLOW_DOWNLOADS"] = False
        webview.settings["IGNORE_SSL_ERRORS"] = False
        window = webview.create_window("Assistant C1 isolated component", html="<!doctype html><title>probe bootstrap</title>",
                                       hidden=True, width=480, height=350, js_api=None, text_select=True)
        # pywebview 6.2.1 customize.js injects an inline style at assembled
        # line 436 when text_select=False. Use its public option to avoid the
        # injection; strict style-src 'self' and violation checks stay intact.
        customize_source = Path(webview.__file__).parent / "js/customize.js"
        report["host_configuration"] = {
            "hidden": window.hidden, "text_select": window.text_select,
            "browser_arguments": synthetic["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"],
            "pywebview": metadata.version("pywebview"),
            "csp_injection_source": "pywebview/js/customize.js",
            "csp_injection_source_sha256": hashlib.sha256(customize_source.read_bytes()).hexdigest(),
            "avoided_injection": "default text_select=False: document.head.appendChild(css), assembled line 436",
            "bootstrap_deadline_seconds": 15, "result_deadline_seconds": 20,
        }
        done = threading.Event()
        requested = []
        unexpected = []
        failures = []
        # Keep delegates and streams alive until native window closes.
        retained = []
        native_events = {"initialized": None, "navigations": []}

        def observe_native():
            control = window.native.browser.webview

            def initialized(sender, args):
                native_events["initialized"] = bool(args.IsSuccess)

            def navigated(sender, args):
                native_events["navigations"].append({"success": bool(args.IsSuccess),
                    "error": str(args.WebErrorStatus)})

            control.CoreWebView2InitializationCompleted += initialized
            control.NavigationCompleted += navigated
            retained.extend((initialized, navigated))

        window.events.before_show += observe_native
        policy = "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; font-src 'self'; connect-src 'self'; frame-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'"
        resources = {
            "https://native-probe.invalid/index.html": (payload["html"].encode(), "text/html"),
            "https://native-probe.invalid/bundle.mjs": (payload["bundle"].encode(), "text/javascript"),
            "https://native-probe.invalid/entry.mjs": (payload["entry"].encode(), "text/javascript"),
            "https://native-probe.invalid/favicon.ico": (b"", "image/x-icon"),
        }

        def worker():
            try:
                check(window.events.loaded.wait(15), "native bootstrap did not load")
                system = importlib.import_module("System")
                io = importlib.import_module("System.IO")
                edge = importlib.import_module("webview.platforms.edgechromium")

                def install():
                    core = window.native.browser.webview.CoreWebView2
                    check(core is not None, "WebView2 control unavailable")
                    core.AddWebResourceRequestedFilter("*", edge.CoreWebView2WebResourceContext.All)

                    def resource(sender, args):
                        uri = str(args.Request.Uri)
                        requested.append(uri)
                        body, mime = resources.get(uri, (b"", "text/plain"))
                        if uri not in resources:
                            unexpected.append("unapproved resource blocked")
                        stream = io.MemoryStream(system.Array[system.Byte](body))
                        retained.append(stream)
                        headers = "Content-Type: " + mime + "\r\nContent-Security-Policy: " + policy + "\r\nCache-Control: no-store\r\nX-Content-Type-Options: nosniff"
                        args.Response = core.Environment.CreateWebResourceResponse(stream,
                            200 if uri in resources else 403, "OK" if uri in resources else "Forbidden", headers)

                    def message(sender, args):
                        try:
                            result = json.loads(str(args.get_WebMessageAsJson()))
                            if isinstance(result, dict) and result.get("c1"):
                                report.update(result)
                                done.set()
                        except (ValueError, TypeError):
                            pass

                    def navigation(sender, args):
                        if str(args.Uri) != "https://native-probe.invalid/index.html":
                            args.Cancel = True

                    core.WebResourceRequested += resource
                    core.WebMessageReceived += message
                    core.NavigationStarting += navigation
                    retained.extend((resource, message, navigation))
                    core.Navigate("https://native-probe.invalid/index.html")

                window.native.Invoke(system.Action(install))
                check(done.wait(20), "native component result timeout")
            except Exception as exc:
                failures.append(str(exc) if isinstance(exc, AssertionError) else type(exc).__name__)
            finally:
                window.destroy()

        try:
            webview.start(worker, gui="edgechromium", http_server=False, private_mode=True, debug=False,
                          storage_path=str(area / "profile"))
        except Exception as exc:
            failures.append(str(exc) if isinstance(exc, AssertionError) else type(exc).__name__)
        if failures or attempts or unexpected:
            report.update(status="fail", detail="; ".join(failures) or "unexpected outbound resource/socket attempt")
        report.update(requested_resources=requested, python_socket_attempts=len(attempts),
                      unapproved_resources=len(unexpected), window_closed=window.events.closed.is_set(),
                      native_events=native_events, bootstrap_loaded=window.events.loaded.is_set(),
                      limitation="page resources intercepted in memory; browser-process OS telemetry is not an OS egress audit; not full app E")
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return int(report["status"] != "pass" or not report["window_closed"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--isolated", action="store_true", required=True)
    parser.add_argument("--fake-only", action="store_true", required=True)
    parser.add_argument("--python-env", type=Path, default=DEFAULT_ENV)
    parser.add_argument("--freeze-sdk", action="store_true", help="build SDK-only frozen probe into task-specific build directory")
    parser.add_argument("--sdk-host", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--native-ui-host", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.native_ui_host:
        return native_ui_host()
    if args.sdk_host:
        check(bool(getattr(sys, "frozen", False)), "--sdk-host is only for the frozen gate artifact")
        return run_probes(frozen_host=True)
    target = args.python_env.resolve()
    check(target != (ROOT / ".desktop-venv").resolve(), "production SDK environment forbidden")
    check((target / "pyvenv.cfg").is_file(), "preinstalled isolated venv required; no install")
    if Path(sys.prefix).resolve() != target:
        executable = target / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        extra = ["--freeze-sdk"] if args.freeze_sdk else []
        return subprocess.run([str(executable), "-B", str(Path(__file__).resolve()), "--isolated", "--fake-only", "--python-env", str(target), *extra],
            cwd=ROOT, env=isolated_environment(), timeout=300 if extra else 90).returncode
    if args.freeze_sdk:
        return freeze_sdk(target)
    return run_probes()


if __name__ == "__main__":
    sys.exit(main())
