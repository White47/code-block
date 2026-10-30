"""Initial adapter RED contract, not service/UI or live-provider acceptance.

SDK-free fakes exercise the approved nine-module injection seam. Actual SDK
tests run only in build/assistant-sdk-probe-20261012 with in-memory transport.
Installed SDK matrices cover bounded streaming and adapter-owned lifecycle only.
"""

import asyncio
import importlib
import importlib.util
import json
import os
import socket
import ssl
import subprocess
import sys
import time
import unittest
from contextlib import ExitStack, asynccontextmanager, contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from textwrap import dedent
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
MODULE = "codeviz.assistant.sdk_provider"
SDK_ENV = ROOT / "build/assistant-sdk-probe-20261012"
SOURCE = 'PUBLIC_FAKE_中文_😀_"quoted"'
KEY = "synthetic-explicit-key"
SDK_MODULES = dict(httpx2="httpx2", openai="openai", models="pydantic_ai.models",
                   messages="pydantic_ai.messages", sdk="pydantic_ai.models.openai",
                   providers="pydantic_ai.providers.openai", profiles="pydantic_ai.profiles.openai",
                   output="pydantic_ai.output", tools="pydantic_ai.tools")


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()


def final_schema():
    """Local structural C6 schema; no nonexistent B5 schema factory."""
    def obj(properties):
        return {"type": "object", "properties": properties, "required": list(properties),
                "additionalProperties": False}

    claim = obj({"text": {"type": "string"},
                 "basis": {"type": "string", "enum": ["static", "inference", "unknown"]},
                 "evidenceIds": {"type": "array", "items": {"type": "string"}},
                 "relationEvidenceId": {"type": ["string", "null"]}})
    section = obj({"kind": {"type": "string", "enum": ["summary", "entry", "steps", "branches",
                                                        "relations", "evidence", "unknowns"]},
                   "claims": {"type": "array", "items": claim}})
    return obj({"sections": {"type": "array", "items": section}})


def envelope(**changes):
    value = dict(version=1, mode="text", messages=[dict(role="user", content=SOURCE)],
                 tools=[], answerSchema=None, sourceTags=[])
    value.update(changes)
    return value


def history():
    return [dict(role="system", content="Explain synthetic saved text."),
            dict(role="user", content=SOURCE),
            dict(role="assistant", content="Synthetic history", toolCalls=[]),
            dict(role="assistant", content=None, toolCalls=[dict(
                id="fake-call", name="search_symbols", arguments='{ "query": "public" }')]),
            dict(role="tool", toolCallId="fake-call", name="search_symbols", content=SOURCE),
            dict(role="user", content="Explain the synthetic return.")]


@contextmanager
def offline_guard():
    """Initialize asyncio plumbing before entry; block outbound DNS/TCP/UDP."""
    attempts = []

    def denied(*args, **kwargs):
        attempts.append("blocked")
        raise AssertionError("Outbound network forbidden in adapter tests")

    environment = {"SystemRoot": r"C:\Windows", "SystemDrive": "C:",
                   "OPENAI_API_KEY": "synthetic-ambient-key",
                   "OPENAI_BASE_URL": "https://ambient.invalid/v9",
                   "OPENAI_ORG_ID": "synthetic-ambient-org",
                   "OPENAI_PROJECT_ID": "synthetic-ambient-project",
                   "OPENAI_WEBHOOK_SECRET": "synthetic-ambient-webhook",
                   "HTTPS_PROXY": "http://ambient-proxy.invalid:9",
                   "HTTP_PROXY": "http://ambient-proxy.invalid:9",
                   "OTEL_EXPORTER_OTLP_ENDPOINT": "https://ambient-telemetry.invalid",
                   "LOGFIRE_TOKEN": "synthetic-ambient-logfire", "PYTHONDONTWRITEBYTECODE": "1"}
    with ExitStack() as stack:
        stack.enter_context(patch.dict(os.environ, environment, clear=True))
        for name in ("getaddrinfo", "gethostbyname", "gethostbyname_ex", "create_connection"):
            stack.enter_context(patch.object(socket, name, denied))
        for name in ("connect", "connect_ex", "sendto"):
            stack.enter_context(patch.object(socket.socket, name, denied))
        yield attempts


@dataclass
class ContentPart:
    content: str


@dataclass
class ToolCallPart:
    tool_name: str
    args: str
    tool_call_id: str


@dataclass
class ToolReturnPart:
    tool_name: str
    content: str
    tool_call_id: str


@dataclass
class ModelMessage:
    parts: list


@dataclass
class Parameters:
    function_tools: list = field(default_factory=list)
    output_mode: str = "text"
    output_object: object = None


@dataclass
class ToolDefinition:
    name: str
    parameters_json_schema: dict
    description: str = ""
    strict: bool = True


@dataclass
class OutputDefinition:
    json_schema: dict
    name: str = "CodeAgentAnswer"
    strict: bool = True


class FakeHeaders(dict):
    @property
    def raw(self):
        return [(key.encode(), value.encode()) for key, value in self.items()]


class FakeSDK:
    """No network/client SDK imports; pause a modeled request at the real adapter gate.

    This deliberately covers preparation only. Streaming fidelity is proved by
    the isolated installed-source tests, not this minimal synthetic encoder.
    """
    def __init__(self, wire_change=None, opaque=False):
        self.clients, self.apis, self.requests, self.calls = [], [], [], []
        self.clock = Mock(return_value=time.monotonic())
        self.wire_change = wire_change
        owner = self

        class RequestNotRead(RuntimeError):
            pass

        class OpaqueRequest:
            def __init__(self, request):
                self.method, self.url, self.headers = request.method, request.url, request.headers
                self.read_attempts = 0

            @property
            def content(self):
                raise RequestNotRead("synthetic opaque body")

            async def aread(self):
                self.read_attempts += 1
                raise AssertionError("Opaque request body must not be read")

        class AsyncBaseTransport:
            async def aclose(self):
                pass

        class AsyncClient:
            def __init__(self, *, transport, **options):
                self.transport, self.options, self.is_closed = transport, options, False
                owner.clients.append(self)

            async def aclose(self):
                self.is_closed = True
                await self.transport.aclose()

        class AsyncOpenAI:
            def __init__(self, **options):
                self.options, self.http = options, options["http_client"]
                owner.apis.append(self)

            async def close(self):
                await self.http.aclose()

        class OpenAIChatModel:
            def __init__(self, model_name, *, provider, profile):
                self.model, self.api, self.profile = model_name, provider.openai_client, profile

            @asynccontextmanager
            async def request_stream(self, messages, model_settings, model_request_parameters):
                owner.calls.append((messages, model_settings, model_request_parameters, self.profile))
                wire_messages = []
                for message in messages:
                    first = message.parts[0]
                    if isinstance(first, ToolReturnPart):
                        wire_messages.append(dict(role="tool", tool_call_id=first.tool_call_id,
                                                  content=first.content))
                    elif isinstance(message, ModelResponse):
                        text = next((p.content for p in message.parts if isinstance(p, TextPart)), None)
                        entry = dict(role="assistant", content=text)
                        calls = [dict(id=p.tool_call_id, type="function", function=dict(
                            name=p.tool_name, arguments=p.args)) for p in message.parts
                            if isinstance(p, ToolCallPart)]
                        if calls:
                            entry["tool_calls"] = calls
                        wire_messages.append(entry)
                    else:
                        wire_messages.append(dict(role="system" if isinstance(first, SystemPromptPart)
                                                  else "user", content=first.content))
                body = dict(model=self.model, messages=wire_messages, stream=True,
                            max_completion_tokens=model_settings["max_tokens"], parallel_tool_calls=False)
                params = model_request_parameters
                if params.function_tools:
                    body["tools"] = [dict(type="function", function=dict(
                        name=t.name, description=t.description, parameters=t.parameters_json_schema,
                        strict=t.strict)) for t in params.function_tools]
                else:
                    body["tool_choice"] = "none"
                if params.output_object:
                    output = params.output_object
                    body["response_format"] = dict(type="json_schema", json_schema=dict(
                        name=output.name, strict=output.strict, schema=output.json_schema))
                if owner.wire_change:
                    owner.wire_change(body)
                raw = encoded(body)
                headers = FakeHeaders({"content-type": "application/json", "content-length": str(len(raw)),
                                       "authorization": "Bearer " + self.api.options["api_key"],
                                       "accept-encoding": "identity"})
                request = SimpleNamespace(method="POST", url=self.api.options["base_url"] + "/chat/completions",
                                          content=raw, headers=headers)
                if opaque:
                    request = OpaqueRequest(request)
                owner.requests.append(request)
                await self.api.http.transport.handle_async_request(request)
                raise AssertionError("Preparation fake must never be dispatched")
                yield  # pragma: no cover

        class SystemPromptPart(ContentPart):
            pass

        class UserPromptPart(ContentPart):
            pass

        class TextPart(ContentPart):
            pass

        class ModelResponse(ModelMessage):
            pass

        self.modules = dict(
            httpx2=SimpleNamespace(AsyncBaseTransport=AsyncBaseTransport, AsyncClient=AsyncClient,
                                  RequestNotRead=RequestNotRead),
            openai=SimpleNamespace(AsyncOpenAI=AsyncOpenAI), models=SimpleNamespace(ModelRequestParameters=Parameters),
            messages=SimpleNamespace(SystemPromptPart=SystemPromptPart, UserPromptPart=UserPromptPart,
                                     TextPart=TextPart, ModelRequest=ModelMessage, ModelResponse=ModelResponse,
                                     ToolCallPart=ToolCallPart, ToolReturnPart=ToolReturnPart),
            sdk=SimpleNamespace(OpenAIChatModel=OpenAIChatModel),
            providers=SimpleNamespace(OpenAIProvider=lambda **kw: SimpleNamespace(**kw)),
            profiles=SimpleNamespace(OpenAIModelProfile=dict),
            output=SimpleNamespace(OutputObjectDefinition=OutputDefinition),
            tools=SimpleNamespace(ToolDefinition=ToolDefinition))


class AdapterAssertions:
    def api(self):
        self.assertIsNotNone(importlib.util.find_spec(MODULE), "Missing approved adapter module: sdk_provider")
        module = importlib.import_module(MODULE)
        for name in ("create_provider", "SDKProviderAdapter", "SDKAdapterError", "_create_provider"):
            self.assertTrue(callable(getattr(module, name, None)), "Missing approved adapter API: " + name)
        return module

    def config(self):
        from codeviz.assistant.config import ProviderConfig
        return ProviderConfig.from_base_url("https://sdk-probe.invalid/v1", "gpt-4o-mini", 1)

    def draft(self, value=None, **changes):
        from codeviz.assistant.provider import ProviderDraft
        arguments = dict(body=encoded(envelope() if value is None else value),
                         max_output_tokens=512, deadline=time.monotonic() + 10)
        arguments.update(changes)
        return ProviderDraft(**arguments)

    def assert_safe_error(self, error, code):
        self.assertIsInstance(error, ValueError)
        self.assertEqual(error.code, code)
        self.assertEqual(str(error), {"AI_INVALID_INPUT": "Invalid assistant input.",
                                     "AI_PROTOCOL_INCOMPATIBLE": "Assistant protocol is incompatible.",
                                     "AI_NOT_CONFIGURED": "Assistant is not configured."}[code])
        self.assertIsNone(error.__cause__)
        self.assertIsNone(error.__context__)


class SDKImportContractTests(AdapterAssertions, unittest.TestCase):
    def test_cold_import_and_exports_without_optional_sdk(self):
        self.api()
        script = dedent("""
            import importlib, importlib.abc, socket, sys
            from unittest.mock import patch
            class BlockSDK(importlib.abc.MetaPathFinder):
                def find_spec(self, fullname, path=None, target=None):
                    if fullname.split('.')[0] in {'pydantic_ai', 'openai', 'httpx2', 'pydantic'}:
                        raise AssertionError('Eager optional dependency')
            sys.meta_path.insert(0, BlockSDK())
            with patch.object(socket, 'getaddrinfo', side_effect=AssertionError('DNS')), \\
                 patch.object(socket, 'socket', side_effect=AssertionError('socket')):
                m = importlib.import_module('codeviz.assistant.sdk_provider')
                assert all(callable(getattr(m, n, None)) for n in
                           ('create_provider', '_create_provider', 'SDKProviderAdapter', 'SDKAdapterError'))
            assert not any(n.split('.')[0] in {'pydantic_ai', 'openai', 'httpx2'} for n in sys.modules)
        """)
        result = subprocess.run([sys.executable, "-B", "-c", script], cwd=ROOT, capture_output=True,
                                text=True, timeout=15, env={**os.environ, "PYTHONPATH": str(ROOT / "src")})
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_factory_validates_before_loader_or_transport_and_missing_extras_are_safe(self):
        api = self.api()
        from codeviz.assistant import provider
        config, key = self.config(), provider.MemoryKey(KEY)
        for config_arg, key_arg, code in ((config, None, "AI_NOT_CONFIGURED"),
                                          (None, key, "AI_INVALID_INPUT"),
                                          (config, KEY, "AI_INVALID_INPUT")):
            with self.subTest(code=code, key_type=type(key_arg).__name__), offline_guard() as attempts:
                loader, transport = Mock(side_effect=AssertionError("loader")), Mock(side_effect=AssertionError("transport"))
                with self.assertRaises(ValueError) as caught:
                    api._create_provider(config_arg, key_arg, sdk_loader=loader, transport_factory=transport)
                self.assert_safe_error(caught.exception, code)
                loader.assert_not_called()
                transport.assert_not_called()
                self.assertEqual(attempts, [])
        with patch("importlib.import_module", side_effect=ModuleNotFoundError("synthetic-secret")), offline_guard():
            with self.assertRaises(provider.ProviderError) as caught:
                provider.create_provider(config, key)
        self.assertEqual(caught.exception.code, "AI_DEPENDENCY_MISSING")
        self.assertIsNone(caught.exception.__context__)
        self.assertNotIn("synthetic-secret", str(caught.exception))


class SDKBoundaryContractTests(AdapterAssertions, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.module = self.api()  # Missing module is an assertion failure, never collection error.
        self.guard = offline_guard()
        self.attempts = self.guard.__enter__()
        self.addCleanup(self.guard.__exit__, None, None, None)

    def adapter(self, fake=None):
        from codeviz.assistant.provider import MemoryKey
        fake = fake or FakeSDK()
        transport = Mock(side_effect=AssertionError("Underlying transport created before dispatch"))
        loader = Mock(return_value=fake.modules)
        adapter = self.module._create_provider(self.config(), MemoryKey(KEY), sdk_loader=loader,
                                               transport_factory=transport, clock=fake.clock)
        self.addAsyncCleanup(adapter.aclose)
        return adapter, fake, loader, transport

    async def rejected_draft(self, draft):
        adapter, fake, _, transport = self.adapter()
        with self.assertRaises(self.module.SDKAdapterError) as caught:
            await asyncio.wait_for(adapter.prepare(draft), 2)
        self.assert_safe_error(caught.exception, "AI_INVALID_INPUT")
        self.assertEqual(fake.requests, [])
        transport.assert_not_called()
        await adapter.aclose()

    async def test_draft_types_json_and_numeric_bounds(self):
        from codeviz.assistant.provider import ProviderDraft
        valid = self.draft()
        bodies = [b"", b"x" * 262145, bytearray(valid.body), b"\xff", b"\xef\xbb\xbf" + valid.body,
                  valid.body + b"{}", valid.body.replace(b'"version":1', b'"version":1,"version":1'),
                  valid.body.replace(b'"version":1', b'"version":NaN'),
                  valid.body.replace(b'"version":1', b'"version":Infinity'),
                  b"[" * 17 + b"0" + b"]" * 17]
        cases = [("body-" + str(i), replace(valid, body=body)) for i, body in enumerate(bodies)]
        cases += [("tokens-" + repr(n), replace(valid, max_output_tokens=n))
                  for n in (True, 0, -1, 2001, 1.0, "1")]
        cases += [("deadline-" + repr(n), replace(valid, deadline=n))
                  for n in (True, 0, time.monotonic() - 1, float("nan"), float("inf"), "future")]
        cases.append(("lookalike", SimpleNamespace(**{n: getattr(valid, n)
                                                    for n in ("body", "max_output_tokens", "deadline")})))
        class DraftSubclass(ProviderDraft):
            pass
        cases.append(("subclass", DraftSubclass(valid.body, 512, valid.deadline)))
        for name, draft in cases:
            with self.subTest(risk=name):
                await self.rejected_draft(draft)

    async def test_closed_messages_tool_history_and_mode_schema_rejections(self):
        call = dict(role="assistant", content=None, toolCalls=[dict(
            id="fake-call", name="search_symbols", arguments='{"query":"public"}')])
        tool = dict(role="tool", toolCallId="fake-call", name="search_symbols", content="{}")
        cases = [envelope(version=True), envelope(url="https://override.invalid"), envelope(messages=[]),
                 envelope(messages=[dict(role="user", content="", extra=1)]),
                 envelope(messages=[dict(role="user", content="x"), dict(role="system", content="late")]),
                 envelope(messages=[dict(role="assistant", content=None, toolCalls=[])]),
                 envelope(messages=[tool]), envelope(messages=[call]), envelope(messages=[call, tool, tool]),
                 envelope(messages=[call, {**tool, "name": "read_source_excerpt"}]),
                 envelope(messages=[dict(role="user", content=[{"type": "image_url"}])]),
                 envelope(messages=[dict(role="user", content="x")] * 41),
                 envelope(mode="final", answerSchema={}), envelope(mode="text", answerSchema=final_schema()),
                 envelope(mode="tools"), envelope(mode="final", answerSchema={**final_schema(), "title": "extra"})]
        for arguments in ('[]', '{"x":1,"x":2}', '{"x":NaN}', '{"x":"' + "x" * 8192 + '"}'):
            bad_call = {**call, "toolCalls": [{**call["toolCalls"][0], "arguments": arguments}]}
            cases.append(envelope(messages=[bad_call, tool]))
        for field_name in ("role", "content", "toolCalls"):
            missing = dict(call)
            del missing[field_name]
            cases.append(envelope(messages=[missing, tool]))
        for call_change in (dict(id=True), dict(id="x" * 513), dict(name="execute_code"), dict(extra=1)):
            cases.append(envelope(messages=[{**call, "toolCalls": [{**call["toolCalls"][0], **call_change}]}, tool]))
        for field_name in ("version", "mode", "messages", "tools", "answerSchema", "sourceTags"):
            missing = envelope()
            del missing[field_name]
            cases.append(missing)
        for addition in (dict(title="extra"), dict(default={}), dict(**{"$ref": "https://remote.invalid/schema"})):
            cases.append(envelope(mode="final", answerSchema={**final_schema(), **addition}))
        definition = dict(name="search_symbols", description="Synthetic search.", parameters={
            "type": "object", "properties": {"query": {"type": "string"}},
            "required": ["query"], "additionalProperties": False})
        for tools in ([definition, definition], [{**definition, "strict": True}],
                      [{**definition, "description": "x" * 2001}],
                      [{**definition, "parameters": {"type": "object", "$ref": "https://remote.invalid"}}],
                      [{**definition, "parameters": {"type": "object", "enum": list(range(1001))}}]):
            cases.append(envelope(mode="tools", tools=tools))
        for index, value in enumerate(cases):
            with self.subTest(risk=index):
                await self.rejected_draft(self.draft(value))

    async def test_source_tags_are_bounded_closed_sorted_and_disjoint(self):
        tag = dict(pointer="/messages/0/content", start=0, end=len(SOURCE), file="public_fake.py")
        invalid = [[{**tag, "start": True}], [{**tag, "end": len(SOURCE) + 1}],
                   [{**tag, "end": 0}], [{**tag, "pointer": "/model"}],
                   [{**tag, "evidenceId": "forged"}], [tag, tag],
                   [{**tag, "start": 2, "end": 3}, {**tag, "end": 1}],
                   [tag] * 1001, [{**tag, "file": "x" * 2049}],
                   [{**tag, "pointer": "/messages/0/role"}], [{**tag, "end": False}],
                   [dict(pointer="/messages/0/content", start=i, end=i + 1, file=f"fake_{i}.py")
                    for i in range(9)]]
        for index, tags in enumerate(invalid):
            with self.subTest(risk=index):
                await self.rejected_draft(self.draft(envelope(sourceTags=tags)))

    async def test_capture_preserves_closed_history_schema_tools_and_repeated_unicode(self):
        tool_schema = {"type": "object", "properties": {"query": {"type": "string"}},
                       "required": ["query"], "additionalProperties": False}
        definition = dict(name="search_symbols", description="Search synthetic symbols.", parameters=tool_schema)
        for mode in ("text", "tools", "final"):
            with self.subTest(mode=mode):
                tags = [dict(pointer=f"/messages/{i}/content", start=0, end=len(SOURCE), file="public_fake.py")
                        for i in (1, 4)]
                value = envelope(mode=mode, messages=history(), sourceTags=tags,
                                 tools=[definition] if mode == "tools" else [],
                                 answerSchema=final_schema() if mode == "final" else None)
                adapter, fake, loader, transport = self.adapter()
                draft = self.draft(value)
                prepared = await asyncio.wait_for(adapter.prepare(draft), 2)
                wire = json.loads(prepared.body)
                self.assertEqual(prepared.deadline, draft.deadline)
                self.assertEqual(prepared.body, fake.requests[0].content)
                self.assertNotEqual(prepared.body, draft.body)
                self.assertEqual(
                    tuple((item.pointer, item.start, item.end, item.file)
                          for item in prepared.source_occurrences),
                    (("/messages/1/content", 0, len(SOURCE), "public_fake.py"),
                     ("/messages/4/content", 0, len(SOURCE), "public_fake.py")))
                self.assertEqual(wire["messages"][1]["content"], SOURCE)
                self.assertEqual(wire["messages"][4], dict(role="tool", tool_call_id="fake-call", content=SOURCE))
                self.assertEqual(wire["messages"][3]["tool_calls"][0]["function"]["arguments"],
                                 '{ "query": "public" }')
                self.assertNotIn("sourceTags", wire)
                self.assertNotIn("public_fake.py", prepared.body.decode())
                self.assertEqual(wire["max_completion_tokens"], 512)
                self.assertNotIn("max_tokens", wire)
                self.assertEqual(wire["model"], self.config().model)
                if mode == "tools":
                    self.assertEqual(wire["tools"][0]["function"]["parameters"], tool_schema)
                if mode == "final":
                    self.assertEqual(wire["response_format"]["json_schema"]["schema"], final_schema())
                loader.assert_called_once_with()
                transport.assert_not_called()
                await adapter.aclose()
                self.assertTrue(all(c.is_closed for c in fake.clients))
        self.assertEqual(self.attempts, [])

    async def test_repeated_source_in_untagged_message_fails_preparation(self):
        value = envelope(messages=[dict(role="user", content=SOURCE),
                                   dict(role="user", content=SOURCE)],
                         sourceTags=[dict(pointer="/messages/0/content", start=0,
                                          end=len(SOURCE), file="public_fake.py")])
        adapter, _, _, transport = self.adapter()
        with self.assertRaises(self.module.SDKAdapterError) as caught:
            await adapter.prepare(self.draft(value))
        self.assert_safe_error(caught.exception, "AI_PROTOCOL_INCOMPATIBLE")
        transport.assert_not_called()

    async def test_metadata_only_span_keeps_file_identity(self):
        value = envelope(messages=[dict(role="user", content="symbol: calculate")],
                         metadataTags=[dict(pointer="/messages/0/content", start=8,
                                            end=17, file="module.py")])
        adapter, _, _, _ = self.adapter()
        prepared = await adapter.prepare(self.draft(value))
        self.assertEqual(prepared.source_occurrences, ())
        self.assertEqual(tuple((item.pointer, item.start, item.end, item.file)
                               for item in prepared.metadata_occurrences),
                         (("/messages/0/content", 8, 17, "module.py"),))

    async def test_unproven_hidden_framing_is_explicit(self):
        adapter, _, _, _ = self.adapter()
        prepared = await adapter.prepare(self.draft())
        self.assertIsNone(prepared.input_token_upper_bound)

    async def test_source_text_in_schema_key_is_unattributable(self):
        parameters = {"type": "object", "properties": {"query": {"type": "string"}},
                      "required": [], "additionalProperties": False}
        value = envelope(mode="tools", messages=[dict(role="user", content="query")],
                         tools=[dict(name="search_symbols", description="Search.",
                                     parameters=parameters)],
                         sourceTags=[dict(pointer="/messages/0/content", start=0,
                                          end=5, file="module.py")])
        adapter, _, _, transport = self.adapter()
        with self.assertRaises(self.module.SDKAdapterError) as caught:
            await adapter.prepare(self.draft(value))
        self.assert_safe_error(caught.exception, "AI_PROTOCOL_INCOMPATIBLE")
        transport.assert_not_called()

    async def test_exact_draft_ceiling_and_token_endpoints_prepare_without_transport(self):
        # Canonical input reaches the exact ceiling using synthetic message text.
        # A complete tag keeps the large source span attributable.
        tag = dict(pointer="/messages/0/content", start=0, end=1, file="public_fake.py")
        value = envelope(sourceTags=[tag], messages=[dict(role="user", content="x")])
        for _ in range(4):
            padding = 262144 - len(encoded(value))
            value["messages"][0]["content"] = "x" * (
                len(value["messages"][0]["content"]) + padding)
            tag["end"] = len(value["messages"][0]["content"])
        raw = encoded(value)
        self.assertEqual(len(raw), 262144)
        for limit in (1, 2000):
            with self.subTest(limit=limit):
                adapter, fake, _, transport = self.adapter()
                prepared = await asyncio.wait_for(adapter.prepare(self.draft(body=raw, max_output_tokens=limit)), 2)
                self.assertEqual(prepared.max_output_tokens, limit)
                self.assertEqual(len(fake.requests), 1)
                transport.assert_not_called()
                await adapter.aclose()
        await self.rejected_draft(self.draft(body=raw + b" "))

    async def test_capture_rejects_mapping_drift_before_external_transport(self):
        for mutate in (lambda wire: wire["messages"].append(dict(role="user", content=SOURCE)),
                       lambda wire: wire.update(model="unexpected-model"),
                       lambda wire: wire.update(max_tokens=512)):
            with self.subTest(mutation=mutate):
                adapter, _, _, transport = self.adapter(FakeSDK(mutate))
                with self.assertRaises(self.module.SDKAdapterError) as caught:
                    await asyncio.wait_for(adapter.prepare(self.draft()), 2)
                self.assert_safe_error(caught.exception, "AI_PROTOCOL_INCOMPATIBLE")
                transport.assert_not_called()
                await adapter.aclose()
        adapter, fake, _, transport = self.adapter(FakeSDK(opaque=True))
        with self.assertRaises(self.module.SDKAdapterError) as caught:
            await asyncio.wait_for(adapter.prepare(self.draft()), 2)
        self.assert_safe_error(caught.exception, "AI_PROTOCOL_INCOMPATIBLE")
        self.assertEqual(fake.requests[0].read_attempts, 0)
        transport.assert_not_called()
        await adapter.aclose()


class ActualSDKCaptureTests(AdapterAssertions, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        if Path(sys.prefix).resolve() != SDK_ENV.resolve():
            absent = all(importlib.util.find_spec(name) is None for name in ("pydantic_ai", "openai", "httpx2"))
            if absent and Path(sys.prefix).resolve() == (ROOT / ".desktop-venv").resolve():
                self.skipTest("SDK absent in base: actual SDK checks require explicit isolated interpreter; not acceptance")
            self.fail("Actual SDK tests require build/assistant-sdk-probe-20261012/Scripts/python.exe")
        self.guard = offline_guard()
        self.attempts = self.guard.__enter__()
        self.addCleanup(self.guard.__exit__, None, None, None)
        self.module = self.api()
        self.sdk = {key: importlib.import_module(name) for key, name in SDK_MODULES.items()}
        from importlib.metadata import version
        for name, pin in (("pydantic-ai-slim", "2.55.0"), ("openai", "3.29.0"),
                          ("httpx2", "2.13.1"), ("pydantic", "2.14.1")):
            self.assertEqual(version(name), pin)

    async def test_actual_capture_same_request_single_handoff_and_owned_closure(self):
        from codeviz.assistant.provider import MemoryKey
        httpx2 = self.sdk["httpx2"]
        captured, received, clients, streams, transports = [], [], [], [], []
        handoff_order = []

        class FakeSSE(httpx2.AsyncByteStream):
            closed = False

            async def __aiter__(self):
                for delta, finish in (({"role": "assistant", "content": "ok"}, None), ({}, "stop")):
                    yield b"data: " + encoded(dict(id="fake-stream", object="chat.completion.chunk", created=0,
                        model="gpt-4o-mini", choices=[dict(index=0, delta=delta, finish_reason=finish)])) + b"\n\n"
                yield b"data: [DONE]\n\n"

            async def aclose(self):
                self.closed = True

        def handler(request):
            received.append(request)
            stream = FakeSSE()
            streams.append(stream)
            return httpx2.Response(200, headers={"content-type": "text/event-stream"}, stream=stream)

        class RecordingTransport(httpx2.MockTransport):
            closed = False

            async def handle_async_request(self, request):
                # This synchronous prefix defines actual underlying entry.
                handoff_order.append("ENTRY")
                return await super().handle_async_request(request)

            async def aclose(self):
                self.closed = True
                await super().aclose()

        def transport_factory():
            transport = RecordingTransport(handler)
            transports.append(transport)
            return transport

        def client_factory(**options):
            gate = options["transport"]
            original = gate.handle_async_request

            async def observe(request):
                captured.append((request, request.content, tuple(request.headers.raw)))
                return await original(request)

            gate.handle_async_request = observe
            client = httpx2.AsyncClient(**options)
            clients.append(client)
            return client

        modules = dict(self.sdk)
        modules["httpx2"] = SimpleNamespace(**{n: getattr(httpx2, n) for n in dir(httpx2) if not n.startswith("__")})
        modules["httpx2"].AsyncClient = client_factory
        adapter = self.module._create_provider(self.config(), MemoryKey(KEY), sdk_loader=lambda: modules,
                                               transport_factory=transport_factory)
        self.addAsyncCleanup(adapter.aclose)
        prepared = await asyncio.wait_for(adapter.prepare(self.draft()), 5)
        self.assertEqual(len(captured), 1)
        self.assertEqual(received, [])
        self.assertEqual(transports, [])
        self.assertEqual(prepared.body, captured[0][1])
        wire = json.loads(prepared.body)
        self.assertEqual(wire["messages"], [dict(role="user", content=SOURCE)])
        self.assertEqual(wire["max_completion_tokens"], 512)
        self.assertTrue(wire["stream"])

        async def consume(handle):
            return [event async for event in adapter.dispatch(handle)]

        dispatch = adapter.dispatch(prepared)
        self.addAsyncCleanup(dispatch.aclose)
        loop = asyncio.get_running_loop()
        # Watchdog cancels this consuming task; it does not queue another task
        # between the service guard and underlying entry as wait_for would.
        watchdog = loop.call_later(5, asyncio.current_task().cancel)
        try:
            loop.call_soon(handoff_order.append, "QUEUED")
            first = await anext(dispatch)
            self.assertEqual(handoff_order[:1], ["ENTRY"],
                             "Dispatch must enter transport before yielding to queued callbacks; "
                             "Event release followed by SDK-task wake is not direct handoff")

            async def remaining_events():
                return [event async for event in dispatch]

            # Scheduling/timeouts are allowed only after actual transport entry.
            events = [first, *await asyncio.wait_for(remaining_events(), 5)]
        finally:
            watchdog.cancel()
        self.assertEqual(handoff_order, ["ENTRY", "QUEUED"])
        self.assertEqual(b"".join(e.payload for e in events if e.kind == "text"), b"ok")
        self.assertEqual(events[-1].kind, "usage")
        self.assertEqual(len(received), 1)
        self.assertIs(received[0], captured[0][0])
        self.assertIs(received[0].content, captured[0][1])
        self.assertEqual(tuple(received[0].headers.raw), captured[0][2])
        self.assertEqual(received[0].headers.get("authorization"), "Bearer " + KEY)
        self.assertFalse(received[0].headers.get("openai-organization"))
        self.assertFalse(received[0].headers.get("openai-project"))
        self.assertEqual(received[0].headers.get("accept-encoding"), "identity")
        with self.assertRaises(self.module.SDKAdapterError):
            await consume(prepared)
        self.assertEqual(len(received), 1)
        await adapter.aclose()
        self.assertTrue(all(c.is_closed for c in clients))
        self.assertTrue(all(s.closed for s in streams))
        self.assertTrue(all(t.closed for t in transports))
        self.assertEqual(self.attempts, [])

    async def test_actual_iterator_close_reports_transport_cleanup_failure(self):
        from codeviz.assistant.provider import MemoryKey
        httpx2 = self.sdk["httpx2"]
        received = []

        class PendingSSE(httpx2.AsyncByteStream):
            closed = False

            async def __aiter__(self):
                yield b"data: " + encoded(dict(id="fake-close", object="chat.completion.chunk", created=0,
                    model="gpt-4o-mini", choices=[dict(index=0, delta={"role": "assistant", "content": "ok"},
                                                      finish_reason=None)])) + b"\n\n"
                await asyncio.Event().wait()

            async def aclose(self):
                self.closed = True

        stream = PendingSSE()

        def handler(request):
            received.append(request)
            return httpx2.Response(200, headers={"content-type": "text/event-stream"}, stream=stream)

        class CloseFailureTransport(httpx2.MockTransport):
            closed = False
            fail_close = True
            close_attempts = 0

            async def aclose(self):
                self.close_attempts += 1
                if self.fail_close:
                    raise RuntimeError("SENSITIVE-CLOSE-MARKER")
                await super().aclose()
                self.closed = True

        transport = CloseFailureTransport(handler)
        adapter = self.module._create_provider(self.config(), MemoryKey(KEY), sdk_loader=lambda: self.sdk,
                                               transport_factory=lambda: transport)
        iterator = None

        async def release_fixture():
            # Remove the injected fault only during teardown, after the assertion.
            transport.fail_close = False
            try:
                if iterator is not None:
                    await iterator.aclose()
                await adapter.aclose()
            except self.module.SDKAdapterError:
                # Permanent failure reporting may survive successful retry cleanup.
                pass
            finally:
                await transport.aclose()

        self.addAsyncCleanup(release_fixture)
        prepared = await asyncio.wait_for(adapter.prepare(self.draft()), 5)
        self.assertEqual(received, [])
        send = adapter._send
        sdk_task, response_future = send.sdk_task, send.response_future
        iterator = adapter.dispatch(prepared)
        first = await asyncio.wait_for(anext(iterator), 5)
        self.assertEqual((first.kind, first.payload), ("text", b"ok"))
        self.assertEqual(len(received), 1)
        with self.assertRaises(self.module.SDKAdapterError) as caught:
            await asyncio.wait_for(iterator.aclose(), 5)
        self.assert_safe_error(caught.exception, "AI_PROTOCOL_INCOMPATIBLE")
        self.assertEqual(caught.exception.diagnostic.to_public(), {
            "boundary": "dispatch", "checkSite": "cleanup"})
        self.assertNotIn("SENSITIVE-CLOSE-MARKER", str(caught.exception))
        self.assertGreaterEqual(transport.close_attempts, 1)
        self.assertFalse(transport.closed, "Failed cleanup must not report transport release")
        self.assertTrue(sdk_task.done(), "SDK activity must be joined even when resource close fails")
        self.assertTrue(response_future.done())
        self.assertIs(adapter._send, send, "Unreleased resources must remain owned")
        self.assertIs(send.transport, transport)
        # Closing the adapter must likewise report unresolved owned cleanup.
        with self.assertRaises(self.module.SDKAdapterError) as caught:
            await asyncio.wait_for(adapter.aclose(), 5)
        self.assert_safe_error(caught.exception, "AI_PROTOCOL_INCOMPATIBLE")
        self.assertEqual(caught.exception.diagnostic.to_public(), {
            "boundary": "dispatch", "checkSite": "cleanup"})
        self.assertFalse(transport.closed)
        self.assertEqual(len(received), 1)
        self.assertEqual(self.attempts, [])


class ActualSDKRiskMatrixTests(AdapterAssertions, unittest.IsolatedAsyncioTestCase):
    """Installed SDK only; adapter ownership never implies service/B-worker release.

    The public 2000-byte allocation dominates the 8192/65536 framing caps.
    Higher framing boundaries are checked separately without raising allocations.
    """

    asyncSetUp = ActualSDKCaptureTests.asyncSetUp

    async def test_protocol_http_status_has_dispatch_diagnostic_without_body_read(self):
        for status in (400, 402, 404, 500):
            with self.subTest(status=status):
                adapter, state = self.fixture(
                    [b"error body must not be consumed"],
                    status=status,
                )
                handle = await asyncio.wait_for(adapter.prepare(self.draft()), 5)
                with self.assertRaises(self.module.SDKAdapterError) as caught:
                    await asyncio.wait_for(self.consume(adapter, handle, []), 5)
                self.assertEqual(caught.exception.code, "AI_PROTOCOL_INCOMPATIBLE")
                self.assertEqual(caught.exception.diagnostic.to_public(), {
                    "boundary": "dispatch",
                    "httpStatus": status,
                })
                self.assertEqual(state.reads, 0)
                await adapter.aclose()

    async def test_local_transport_factory_failure_is_pre_send(self):
        adapter, state = self.fixture()
        handle = await asyncio.wait_for(adapter.prepare(self.draft()), 5)
        state.factory.side_effect = RuntimeError("SENSITIVE-LOCAL-FACTORY")
        with self.assertRaises(self.module.SDKAdapterError) as caught:
            await asyncio.wait_for(self.consume(adapter, handle, []), 5)
        self.assertEqual(caught.exception.code, "AI_PROTOCOL_INCOMPATIBLE")
        self.assertEqual(caught.exception.diagnostic.to_public(), {
            "boundary": "pre_send",
        })
        self.assertEqual((len(state.sent), state.reads), (0, 0))
        await adapter.aclose()

    async def test_rejected_response_headers_have_no_header_values_in_diagnostic(self):
        adapter, state = self.fixture(headers={"content-type": "private-header-value"})
        handle = await asyncio.wait_for(adapter.prepare(self.draft()), 5)
        with self.assertRaises(self.module.SDKAdapterError) as caught:
            await asyncio.wait_for(self.consume(adapter, handle, []), 5)
        self.assertEqual(caught.exception.diagnostic.to_public(), {
            "boundary": "dispatch", "checkSite": "response_headers"})
        self.assertEqual(state.reads, 0)
        self.assertNotIn("private-header-value", repr(caught.exception))
        await adapter.aclose()

    async def test_generic_unsuccessful_finish_retains_only_closed_reason(self):
        for reason in ("length", "content_filter", "insufficient_system_resource", "aborted"):
            adapter, state = self.fixture([
                self.frame(dict(content="ok")), self.frame(finish=reason), b"data: [DONE]\n\n"])
            handle = await asyncio.wait_for(adapter.prepare(self.draft()), 5)
            events = []
            with self.assertRaises(self.module.SDKAdapterError) as caught:
                await asyncio.wait_for(self.consume(adapter, handle, events), 5)
            self.fixed_error(caught.exception, "AI_PROTOCOL_INCOMPATIBLE")
            self.assertEqual(caught.exception.diagnostic.to_public(), {
                "boundary": "dispatch", "checkSite": "terminal_finish", "finishReason": reason})
            self.assertEqual(len(state.sent), 1)
            self.assertFalse(any(event.kind in ("answer", "tool", "usage") for event in events))
            self.assertTrue(state.stream.closed)
            await adapter.aclose()

    async def test_generic_sdk_context_entry_failure_after_response_is_dispatch(self):
        original = self.sdk["sdk"].OpenAIChatModel
        entered = []

        class Model:
            def __init__(self, *args, **kwargs):
                self.real = original(*args, **kwargs)

            @asynccontextmanager
            async def request_stream(self, *args, **kwargs):
                async with self.real.request_stream(*args, **kwargs) as response:
                    entered.append(response)
                    raise RuntimeError("SENSITIVE-SDK-CONTEXT-ENTRY")
                    yield response

        loaded = dict(self.sdk)
        loaded["sdk"] = SimpleNamespace(OpenAIChatModel=Model)
        adapter, state = self.fixture(modules=loaded)
        handle = await asyncio.wait_for(adapter.prepare(self.draft()), 5)
        with self.assertRaises(self.module.SDKAdapterError) as caught:
            await asyncio.wait_for(self.consume(adapter, handle, []), 5)
        self.fixed_error(caught.exception, "AI_PROTOCOL_INCOMPATIBLE")
        self.assertEqual(caught.exception.diagnostic.to_public(), {
            "boundary": "dispatch",
            "checkSite": "sdk_decode",
        })
        self.assertEqual(len(entered), 1)
        self.assertEqual((len(state.sent), state.reads), (1, 1))
        self.assertTrue(state.stream.closed)
        await adapter.aclose()

    async def test_sdk_context_exit_failure_after_valid_result_is_dispatch(self):
        original = self.sdk["sdk"].OpenAIChatModel

        class Model:
            def __init__(self, *args, **kwargs):
                self.real = original(*args, **kwargs)

            @asynccontextmanager
            async def request_stream(self, *args, **kwargs):
                async with self.real.request_stream(*args, **kwargs) as response:
                    yield response
                    raise RuntimeError("SENSITIVE-SDK-CONTEXT-EXIT")

        loaded = dict(self.sdk)
        loaded["sdk"] = SimpleNamespace(OpenAIChatModel=Model)
        adapter, state = self.fixture(modules=loaded)
        handle = await asyncio.wait_for(adapter.prepare(self.draft()), 5)
        with self.assertRaises(self.module.SDKAdapterError) as caught:
            await asyncio.wait_for(self.consume(adapter, handle, []), 5)
        self.fixed_error(caught.exception, "AI_PROTOCOL_INCOMPATIBLE")
        self.assertEqual(caught.exception.diagnostic.to_public(), {
            "boundary": "dispatch",
            "checkSite": "context_exit",
        })
        self.assertEqual((len(state.sent), state.reads), (1, 3))
        await adapter.aclose()

    async def test_malformed_initial_sse_is_dispatch(self):
        first = self.frame(dict(content="ok"))
        malformed = first.replace(b'"index":0', b'"index":0,"index":0')
        adapter, state = self.fixture([malformed, *self.success()[1:]])
        handle = await asyncio.wait_for(adapter.prepare(self.draft()), 5)
        with self.assertRaises(self.module.SDKAdapterError) as caught:
            await asyncio.wait_for(self.consume(adapter, handle, []), 5)
        self.assertEqual(caught.exception.code, "AI_PROTOCOL_INCOMPATIBLE")
        self.assertEqual(caught.exception.diagnostic.to_public(), {
            "boundary": "dispatch",
            "checkSite": "sse_syntax",
        })
        self.assertGreaterEqual(state.reads, 1)
        await adapter.aclose()

    async def test_malformed_poststream_tool_result_is_result_validation(self):
        original = self.sdk["sdk"].OpenAIChatModel
        messages = self.sdk["messages"]

        class Model:
            def __init__(self, *args, **kwargs):
                self.real = original(*args, **kwargs)

            @asynccontextmanager
            async def request_stream(self, *args, **kwargs):
                async with self.real.request_stream(*args, **kwargs) as response:
                    class Response:
                        def __aiter__(self):
                            return response.__aiter__()

                        @property
                        def usage(self):
                            return response.usage

                        def get(self):
                            return SimpleNamespace(parts=[messages.ToolCallPart(
                                tool_name="undeclared_tool",
                                args='{"query":"x"}',
                                tool_call_id="call-1",
                            )])

                    yield Response()

        loaded = dict(self.sdk)
        loaded["sdk"] = SimpleNamespace(OpenAIChatModel=Model)
        definition = dict(name="search_symbols", description="Synthetic search", parameters={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        })
        tools = envelope(mode="tools", tools=[definition])
        call = dict(index=0, id="call-1", type="function", function={
            "name": "search_symbols",
            "arguments": '{"query":"x"}',
        })
        chunks = [
            self.frame(dict(tool_calls=[call])),
            self.frame(finish="tool_calls"),
            b"data: [DONE]\n\n",
        ]
        adapter, _state = self.fixture(chunks, modules=loaded)
        handle = await asyncio.wait_for(adapter.prepare(self.draft(tools)), 5)
        with self.assertRaises(self.module.SDKAdapterError) as caught:
            await asyncio.wait_for(self.consume(adapter, handle, []), 5)
        self.assertEqual(caught.exception.code, "AI_PROTOCOL_INCOMPATIBLE")
        self.assertEqual(caught.exception.diagnostic.to_public(), {
            "boundary": "result_validation",
        })
        await adapter.aclose()

    def frame(self, delta=None, finish=None, *, choices=None, usage=None):
        value = dict(id="synthetic-matrix", object="chat.completion.chunk", created=0,
                     model="gpt-4o-mini", choices=choices if choices is not None else
                     [dict(index=0, delta=delta or {}, finish_reason=finish)])
        if usage is not None:
            value["usage"] = usage
        return b"data: " + encoded(value) + b"\n\n"

    def success(self, text="ok", usage=None):
        frames = [self.frame(dict(role="assistant", content=text)), self.frame(finish="stop")]
        if usage is not None:
            frames.append(self.frame(choices=[], usage=usage))
        return frames + [b"data: [DONE]\n\n"]

    def fixture(self, chunks=None, *, status=200, headers=None, handler=None,
                modules=None, clock=None, stream_barrier=None, after_barrier=(), register_cleanup=True):
        from codeviz.assistant.provider import MemoryKey
        httpx = self.sdk["httpx2"]
        state = SimpleNamespace(sent=[], clients=[], captures=[], reads=0,
                                entered=asyncio.Event(), stream_entered=asyncio.Event())
        chunks = self.success() if chunks is None else chunks

        class FakeSSE(httpx.AsyncByteStream):
            closed = False

            async def __aiter__(self):
                for chunk in chunks:
                    state.reads += 1
                    yield chunk
                if stream_barrier is not None:
                    state.stream_entered.set()
                    await stream_barrier.wait()
                    for chunk in after_barrier:
                        state.reads += 1
                        yield chunk

            async def aclose(self):
                self.closed = True

        state.stream = FakeSSE()

        async def respond(request):
            if handler is not None:
                return await handler(request, state)
            return httpx.Response(status, headers=headers or {"content-type": "text/event-stream"},
                                  stream=state.stream)

        class RecordingTransport(httpx.MockTransport):
            closed = False

            async def handle_async_request(self, request):
                state.sent.append(request)
                state.entered.set()
                return await super().handle_async_request(request)

            async def aclose(self):
                self.closed = True
                await super().aclose()

        state.transport = RecordingTransport(respond)
        loaded = dict(self.sdk if modules is None else modules)
        http_module = SimpleNamespace(**{n: getattr(httpx, n) for n in dir(httpx)
                                        if not n.startswith("__")})

        def client_factory(**options):
            gate = options["transport"]
            original = gate.handle_async_request

            async def capture(request):
                state.captures.append(request)
                return await original(request)

            gate.handle_async_request = capture
            client = httpx.AsyncClient(**options)
            state.clients.append(client)
            return client

        http_module.AsyncClient = client_factory
        loaded["httpx2"] = http_module
        state.factory = Mock(return_value=state.transport)
        adapter = self.module._create_provider(self.config(), MemoryKey(KEY), sdk_loader=lambda: loaded,
                                               transport_factory=state.factory,
                                               **({"clock": clock} if clock else {}))

        async def close_owned():
            await asyncio.wait_for(adapter.aclose(), 5)

        if register_cleanup:
            self.addAsyncCleanup(close_owned)
        return adapter, state

    def fixed_error(self, error, code):
        self.assertIs(type(error), self.module.SDKAdapterError)
        self.assertEqual(error.code, code)
        self.assertEqual(str(error), {
            "AI_INVALID_INPUT": "Invalid assistant input.",
            "AI_PROTOCOL_INCOMPATIBLE": "Assistant protocol is incompatible.",
            "AI_AUTH_FAILED": "Assistant authentication failed.",
            "AI_RATE_LIMITED": "Assistant rate limit exceeded.",
            "AI_NETWORK_TIMEOUT": "Assistant network request timed out.",
            "AI_BUDGET_EXCEEDED": "Assistant budget exceeded."}[code])
        self.assertIsNone(error.__cause__)
        self.assertIsNone(error.__context__)
        for marker in (KEY, SOURCE, "SENSITIVE", "ambient"):
            self.assertNotIn(marker, repr(error))

    async def consume(self, adapter, handle, events):
        iterator = adapter.dispatch(handle)
        try:
            async for event in iterator:
                events.append(event)
        finally:
            await iterator.aclose()

    async def run_case(self, chunks=None, *, code=None, value=None, allocation=512, **options):
        self.assertTrue(type(allocation) is int and 1 <= allocation <= 2000,
                        "Actual-send fixtures must preserve the production allocation bound")
        adapter, state = self.fixture(chunks, **options)
        handle = await asyncio.wait_for(adapter.prepare(self.draft(value, max_output_tokens=allocation)), 5)
        send = adapter._send  # Private capture/future/task ownership, never a service API.
        task, future = send.sdk_task, send.response_future
        self.assertFalse(task.done())
        self.assertFalse(future.done())
        self.assertEqual(send.queue.maxsize, 1)
        self.assertEqual(state.sent, [])
        state.factory.assert_not_called()
        events = []
        failure = None
        if code:
            with self.assertRaises(self.module.SDKAdapterError) as caught:
                await asyncio.wait_for(self.consume(adapter, handle, events), 5)
            failure = caught.exception
            self.assertFalse(any(e.kind in ("answer", "tool", "usage") for e in events))
        else:
            await asyncio.wait_for(self.consume(adapter, handle, events), 5)
            self.assertEqual(events[-1].kind, "usage")
            self.assertEqual(sum(e.kind == "usage" for e in events), 1)
            self.assertTrue(all(type(e.payload) is bytes and len(e.payload) <= 8192 for e in events))
        self.assertEqual(len(state.sent), 1)
        self.assertEqual(len(state.captures), 1)
        self.assertIs(state.sent[0], state.captures[0])
        self.assertTrue(task.done())
        self.assertTrue(future.done())
        self.assertTrue(state.stream.closed)
        self.assertTrue(state.transport.closed)
        self.assertTrue(all(c.is_closed for c in state.clients))
        self.assertIsNone(adapter._send)
        self.assertIsNone(send.request)
        self.assertIsNone(send.consumer)
        self.assertIsNone(send.fingerprint)
        self.assertIsNone(send.prepared)
        self.assertEqual(send.attribution, [])
        self.assertEqual(send.data, {})
        self.assertEqual(send.draft.body, b"")
        self.assertEqual(self.attempts, [])
        await adapter.aclose()
        if failure is not None:
            self.fixed_error(failure, code)
        return events

    async def test_status_encoding_and_transport_failures(self):
        cases = [(str(status), dict(status=status, headers={"content-type": "text/event-stream",
                  "location": self.config().endpoint}),
                  "AI_AUTH_FAILED" if status in (401, 403) else "AI_RATE_LIMITED" if status == 429
                  else "AI_PROTOCOL_INCOMPATIBLE") for status in (301, 302, 307, 308, 401, 403, 429, 500)]
        cases += [("gzip", dict(headers={"content-type": "text/event-stream", "content-encoding": "gzip"}),
                   "AI_PROTOCOL_INCOMPATIBLE"),
                  ("wrong-content-type", dict(headers={"content-type": "application/json"}),
                   "AI_PROTOCOL_INCOMPATIBLE")]
        for name, options, code in cases:
            with self.subTest(risk=name):
                await self.run_case(code=code, **options)
        for exception in (self.sdk["httpx2"].ReadTimeout, self.sdk["httpx2"].ConnectError):
            with self.subTest(risk=exception.__name__):
                async def failed(request, state, exception=exception):
                    # A returned Response does not exist on this path.
                    await state.stream.aclose()
                    raise exception("SENSITIVE-TRANSPORT")
                await self.run_case(code="AI_NETWORK_TIMEOUT" if exception.__name__ == "ReadTimeout"
                                    else "AI_PROTOCOL_INCOMPATIBLE", handler=failed)

    async def test_poisoned_environment_secure_underlying_constructor(self):
        from codeviz.assistant.provider import MemoryKey
        httpx, openai = self.sdk["httpx2"], self.sdk["openai"]
        adapter, state = self.fixture()
        await adapter.aclose()
        client_constructor, api_constructor = httpx.AsyncClient.__init__, openai.AsyncOpenAI.__init__

        def client_init(client, *args, **options):
            client_spy(**options)
            client_constructor(client, *args, **options)

        def api_init(api, *args, **options):
            api_spy(**options)
            api_constructor(api, *args, **options)

        client_spy, api_spy = Mock(), Mock()
        with patch.object(httpx, "AsyncHTTPTransport", return_value=state.transport) as transport_spy, \
             patch.object(httpx.AsyncClient, "__init__", client_init), \
             patch.object(openai.AsyncOpenAI, "__init__", api_init):
            adapter = self.module.create_provider(self.config(), MemoryKey(KEY))
            self.addAsyncCleanup(adapter.aclose)
            handle = await asyncio.wait_for(adapter.prepare(self.draft()), 5)
            transport_spy.assert_not_called()
            await asyncio.wait_for(self.consume(adapter, handle, []), 5)
            transport_spy.assert_called_once()
            options = transport_spy.call_args.kwargs
            self.assertEqual((options["trust_env"], options["proxy"], options["retries"]), (False, None, 0))
            self.assertEqual(options["verify"].verify_mode, ssl.CERT_REQUIRED)
            self.assertTrue(options["verify"].check_hostname)
            client_options = client_spy.call_args.kwargs
            self.assertFalse(client_options["trust_env"])
            self.assertFalse(client_options["follow_redirects"])
            api_options = api_spy.call_args.kwargs
            self.assertEqual([api_options[n] for n in ("api_key", "base_url", "max_retries", "organization",
                                                       "project", "webhook_secret")],
                             [KEY, self.config().base_url, 0, "", "", ""])
            self.assertEqual(len(state.sent), 1)
            self.assertEqual(state.sent[0].headers["authorization"], "Bearer " + KEY)
            self.assertEqual(state.sent[0].headers["accept-encoding"], "identity")
            self.assertNotIn(KEY, repr(MemoryKey(KEY)))
            self.assertEqual(self.attempts, [])
            await adapter.aclose()

    async def test_raw_aggregate_and_strict_frame_matrix(self):
        # Comments consume raw bytes, not generated output. A nonempty normal
        # text/finish/DONE tail avoids empty-SDK-stream incompatibility.
        valid = b"".join(self.success("😀"))
        for size in (262144, 262145):
            padding = size - len(valid)
            raw = b":" + b"x" * (padding - 3) + b"\n\n" + valid
            self.assertEqual(len(raw), size)
            with self.subTest(risk="aggregate", bytes=size, proof="actual-send-raw-cap"):
                events = await self.run_case([raw[i:i + 997] for i in range(0, len(raw), 997)],
                                            code="AI_BUDGET_EXCEEDED" if size > 262144 else None)
                if size == 262144:
                    self.assertEqual(b"".join(e.payload for e in events if e.kind == "text"), "😀".encode())
        first = self.frame(dict(content="ok"))
        duplicate = first.replace(b'"index":0', b'"index":0,"index":0')
        nan = first.replace(b'"created":0', b'"created":NaN')
        depth = first.replace(b'"created":0', b'"created":' + b"[" * 17 + b"0" + b"]" * 17)
        boolean = first.replace(b'"index":0', b'"index":true')
        for name, frame in (("duplicate", duplicate), ("NaN", nan), ("depth17", depth), ("bool-index", boolean)):
            with self.subTest(risk=name):
                await self.run_case([frame, *self.success()[1:]], code="AI_PROTOCOL_INCOMPATIBLE")
        tools = envelope(mode="tools", tools=[dict(name="search_symbols", description="Synthetic search",
            parameters={"type": "object", "properties": {"query": {"type": "string"}},
                        "required": ["query"], "additionalProperties": False})])
        for field_name in ("content", "id", "name", "arguments"):
            for bad_value in (True, {"marker": "SENSITIVE-REMOTE-TYPE"}, 17):
                with self.subTest(risk="remote-field-type", field=field_name, value_type=type(bad_value).__name__):
                    if field_name == "content":
                        malformed = self.frame(dict(content=bad_value))
                        value, finish = None, "stop"
                    else:
                        call = dict(index=0, id="c", type="function",
                                    function=dict(name="search_symbols", arguments='{"query":"x"}'))
                        if field_name == "id":
                            call["id"] = bad_value
                        else:
                            call["function"][field_name] = bad_value
                        malformed = self.frame(dict(tool_calls=[call]))
                        value, finish = tools, "tool_calls"
                    await self.run_case([malformed, self.frame(finish=finish), b"data: [DONE]\n\n"],
                                        value=value, code="AI_PROTOCOL_INCOMPATIBLE")

    async def test_stream_terminal_extensions_and_utf8_matrix(self):
        for newline in (b"\n", b"\r\n"):
            raw = b"".join(self.success("中文😀")).replace(b"\n", newline)
            with self.subTest(risk="split-utf8-newline", newline=newline):
                events = await self.run_case([raw[i:i + 1] for i in range(len(raw))])
                self.assertEqual(b"".join(e.payload for e in events if e.kind == "text"), "中文😀".encode())
        cases = [("missing-DONE", self.success()[:-1]),
                 ("incomplete-EOF", [*self.success()[:-1], b"data: [DO"]),
                 ("length", [self.frame(dict(content="ok")), self.frame(finish="length"), b"data: [DONE]\n\n"]),
                 ("filter", [self.frame(finish="content_filter"), b"data: [DONE]\n\n"]),
                 ("refusal", [self.frame(dict(refusal="SENSITIVE-refusal")), *self.success()[1:]]),
                 ("reasoning", [self.frame(dict(reasoning_content="hidden")), *self.success()[1:]]),
                 ("audio", [self.frame(dict(audio={"data": "hidden"})), *self.success()[1:]]),
                 ("multichoice", [self.frame(choices=[dict(index=i, delta={}, finish_reason=None)
                                                     for i in range(2)]), *self.success()[1:]]),
                 ("tool-bool-index", [self.frame(dict(tool_calls=[dict(index=True, id="c", type="function",
                                        function=dict(name="search_symbols", arguments="{}"))])), *self.success()[1:]])]
        for name, chunks in cases:
            with self.subTest(risk=name):
                value = None
                if name == "tool-bool-index":
                    value = envelope(mode="tools", tools=[dict(name="search_symbols", description="Synthetic",
                        parameters={"type": "object", "properties": {"query": {"type": "string"}},
                                    "required": ["query"], "additionalProperties": False})])
                events = await self.run_case(chunks, code="AI_PROTOCOL_INCOMPATIBLE", value=value)
                if name in ("missing-DONE", "incomplete-EOF", "length"):
                    self.assertEqual(b"".join(e.payload for e in events if e.kind == "text"), b"ok",
                                     "Previously emitted text remains provisional after terminal failure")

    async def test_generated_budget_usage_tool_and_answer_framing(self):
        definition = dict(name="search_symbols", description="Synthetic search", parameters={
            "type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"],
            "additionalProperties": False})
        tools = envelope(mode="tools", tools=[definition])
        # Narrow parser cap checks: whitespace makes a byte-exact argument string
        # whose canonical completion fits the event cap. The public path below
        # still rejects generated bytes > allocation; no allocation is relaxed.
        for size in (8192, 8193):
            with self.subTest(risk="argument-parser-bytes", bytes=size, proof="internal-parser-only"):
                raw = b'{"query":"x"}' + b" " * (size - len(b'{"query":"x"}'))
                if size == 8192:
                    self.assertEqual(self.module._parse(raw, 8192), {"query": "x"})
                else:
                    with self.assertRaises(self.module.SDKAdapterError) as caught:
                        self.module._parse(raw, 8192)
                    self.fixed_error(caught.exception, "AI_INVALID_INPUT")
        for size in (8192, 8193):
            arguments = encoded(dict(query="x" * (size - len(encoded(dict(query="")))))).decode()
            self.assertEqual(len(arguments.encode()), size)
            with self.subTest(risk="tool-argument-cap", bytes=size, proof="actual-send-budget-precedence"):
                call = dict(index=0, id="call", type="function", function=dict(name="search_symbols", arguments=arguments))
                await self.run_case([self.frame(dict(tool_calls=[call])), self.frame(finish="tool_calls"),
                                     b"data: [DONE]\n\n"], value=tools, allocation=2000, code="AI_BUDGET_EXCEEDED")
        for allocation, size in ((1, 1), (1, 2), (2000, 2000), (2000, 2001)):
            with self.subTest(risk="generated-bytes", allocation=allocation, bytes=size):
                await self.run_case(self.success("x" * size), allocation=allocation,
                                    code="AI_BUDGET_EXCEEDED" if size > allocation else None)
        usage_cases = [("absent", None, None), ("zero", dict(prompt_tokens=0, completion_tokens=0), None),
                       ("actual", dict(prompt_tokens=19, completion_tokens=2), None),
                       ("allocation-over", dict(prompt_tokens=0, completion_tokens=513), "AI_PROTOCOL_INCOMPATIBLE"),
                       ("int-over", dict(prompt_tokens=2**63, completion_tokens=0), "AI_PROTOCOL_INCOMPATIBLE"),
                       ("bool", dict(prompt_tokens=True, completion_tokens=0), "AI_PROTOCOL_INCOMPATIBLE"),
                       ("hidden-audio", dict(prompt_tokens=0, completion_tokens=0,
                                             completion_tokens_details={"audio_tokens": 1}), "AI_PROTOCOL_INCOMPATIBLE")]
        for name, usage, code in usage_cases:
            with self.subTest(risk="usage-" + name):
                events = await self.run_case(self.success(usage=usage), code=code)
                if not code:
                    self.assertEqual(json.loads(events[-1].payload), dict(
                        inputTokens=usage["prompt_tokens"] if usage else None,
                        outputTokens=usage["completion_tokens"] if usage else None,
                        basis="actual" if usage else "unknown"))
        for size in (65536, 65537):
            with self.subTest(risk="answer-public-budget", bytes=size, proof="actual-send-budget-precedence"):
                await self.run_case(self.success("x" * size), value=envelope(mode="final", answerSchema=final_schema()),
                                    allocation=2000, code="AI_BUDGET_EXCEEDED")
        candidate = '{"sections":[]}'
        events = await self.run_case(self.success(candidate), value=envelope(mode="final", answerSchema=final_schema()))
        answers = [json.loads(e.payload) for e in events if e.kind == "answer"]
        self.assertEqual([a["index"] for a in answers], list(range(len(answers))))
        self.assertEqual([a["last"] for a in answers], [False] * (len(answers) - 1) + [True])
        self.assertEqual("".join(a["content"] for a in answers), candidate)
        # Pure helper proves UTF-8 joining and <=8192 encoded framing only.
        # Even the 65537-byte input is structurally split: this helper does not
        # enforce the joined-answer ceiling or establish actual-send acceptance.
        for size in (65536, 65537):
            raw = "😀" * (size // 4) + "x" * (size % 4)
            fragments = list(self.module._answer_chunks(raw))
            self.assertEqual("".join(fragments).encode(), raw.encode())
            self.assertTrue(all(fragment and len(encoded(dict(index=i, last=i == len(fragments) - 1,
                                                             content=fragment))) <= 8192
                                for i, fragment in enumerate(fragments)))
        call = dict(index=0, id="c", type="function", function=dict(name="search_symbols", arguments='{"query":"x"}'))
        tool_frame = self.frame(dict(tool_calls=[call]))
        payload = encoded(dict(id="c", name="search_symbols", arguments={"query": "x"}))
        for allocation in (len(payload) - 1, len(payload)):
            with self.subTest(risk="tool-framing-overhead", allocation=allocation):
                events = await self.run_case([tool_frame, self.frame(finish="tool_calls"), b"data: [DONE]\n\n"],
                                            value=tools, allocation=allocation,
                                            code="AI_BUDGET_EXCEEDED" if allocation < len(payload) else None)
                if allocation == len(payload):
                    self.assertEqual([e.payload for e in events if e.kind == "tool"], [payload])
        fragments = [self.frame(dict(tool_calls=[dict(index=0, id="c", type="function",
                     function=dict(name="search_symbols", arguments='{"query":'))])),
                     self.frame(dict(tool_calls=[dict(index=0, function=dict(arguments='"x"}'))])),
                     self.frame(finish="tool_calls"), b"data: [DONE]\n\n"]
        events = await self.run_case(fragments, value=tools, allocation=len(payload))
        self.assertEqual([e.payload for e in events if e.kind == "tool"], [payload])

    async def test_original_deadline_and_prepare_failure_barriers(self):
        now = Mock(return_value=time.monotonic())
        original = self.sdk["sdk"].OpenAIChatModel
        for phase in ("prepare-failure", "prepare-deadline", "headers", "stream", "stream-data"):
            with self.subTest(risk=phase):
                release, entered = asyncio.Event(), asyncio.Event()
                loaded = dict(self.sdk)
                if phase.startswith("prepare"):
                    class Model:
                        def __init__(self, *args, **kwargs):
                            self.real = original(*args, **kwargs)

                        @asynccontextmanager
                        async def request_stream(self, *args, entered=entered, release=release, phase=phase, **kwargs):
                            entered.set()
                            await release.wait()
                            if phase == "prepare-failure":
                                raise RuntimeError("SENSITIVE-PREPARE")
                            async with self.real.request_stream(*args, **kwargs) as response:
                                yield response
                    loaded["sdk"] = SimpleNamespace(OpenAIChatModel=Model)

                async def headers(request, state, entered=entered, release=release):
                    entered.set()
                    await release.wait()
                    return self.sdk["httpx2"].Response(200, headers={"content-type": "text/event-stream"},
                                                        stream=state.stream)

                adapter, state = self.fixture(modules=loaded, clock=now,
                    handler=headers if phase == "headers" else None,
                    chunks=[] if phase.startswith("stream") else None,
                    stream_barrier=release if phase.startswith("stream") else None,
                    after_barrier=self.success() if phase == "stream-data" else ())
                draft = self.draft(deadline=now() + 1)
                events = []
                if phase.startswith("prepare"):
                    activity = asyncio.create_task(adapter.prepare(draft))
                    await asyncio.wait_for(entered.wait(), 5)
                    send = adapter._send
                else:
                    handle = await asyncio.wait_for(adapter.prepare(draft), 5)
                    self.assertEqual(handle.deadline, draft.deadline)
                    send = adapter._send
                    activity = asyncio.create_task(self.consume(adapter, handle, events))
                    await asyncio.wait_for(entered.wait() if phase == "headers" else state.stream_entered.wait(), 5)
                sdk_task, future = send.sdk_task, send.response_future
                if phase != "prepare-failure":
                    now.return_value = draft.deadline + 0.01
                release.set()
                with self.assertRaises(self.module.SDKAdapterError) as caught:
                    await asyncio.wait_for(activity, 5)
                self.assertTrue(activity.done())
                self.assertTrue(sdk_task.done())
                self.assertTrue(future.done())
                self.assertEqual(len(state.sent), 0 if phase.startswith("prepare") else 1)
                self.assertFalse(any(e.kind in ("usage", "tool", "answer") for e in events))
                await adapter.aclose()
                self.assertIsNone(adapter._send)
                self.assertTrue(all(c.is_closed for c in state.clients))
                if not phase.startswith("prepare"):
                    self.assertTrue(state.transport.closed)
                if phase.startswith("stream"):
                    self.assertTrue(state.stream.closed)
                now.return_value = time.monotonic()
                self.fixed_error(caught.exception, "AI_PROTOCOL_INCOMPATIBLE" if phase == "prepare-failure"
                                 else "AI_NETWORK_TIMEOUT")

    async def test_cancel_close_backpressure_and_both_owned_activities(self):
        for phase in ("predispatch", "headers", "stream", "explicit-close", "consumer-cancel", "backpressure"):
            with self.subTest(risk=phase):
                release, entered = asyncio.Event(), asyncio.Event()

                async def blocked(request, state, entered=entered, release=release):
                    entered.set()
                    await release.wait()
                    return self.sdk["httpx2"].Response(200, headers={"content-type": "text/event-stream"},
                                                        stream=state.stream)

                chunks = [self.frame(dict(content="x")) for _ in range(30)]
                adapter, state = self.fixture(chunks, handler=blocked if phase == "headers" else None,
                                              stream_barrier=release)
                handle = await asyncio.wait_for(adapter.prepare(self.draft()), 5)
                send, events = adapter._send, []
                sdk_task, future = send.sdk_task, send.response_future
                iterator = adapter.dispatch(handle)
                consumer = None
                if phase == "predispatch":
                    async def delayed(entered=entered, release=release, iterator=iterator):
                        entered.set()
                        await release.wait()
                        return await anext(iterator)
                    consumer = asyncio.create_task(delayed())
                    await asyncio.wait_for(entered.wait(), 5)
                elif phase in ("explicit-close", "backpressure"):
                    self.assertEqual((await asyncio.wait_for(anext(iterator), 5)).kind, "text")
                    for _ in range(20):
                        await asyncio.sleep(0)
                    self.assertEqual(send.queue.maxsize, 1)
                    self.assertLessEqual(send.queue.qsize(), 1)
                    self.assertLess(state.reads, len(chunks), "Abandoned iterator must backpressure the SDK")
                    self.assertFalse(sdk_task.done())
                    await asyncio.wait_for(iterator.aclose(), 5)
                else:
                    consumer = asyncio.create_task(self.consume(adapter, handle, events))
                    await asyncio.wait_for(entered.wait() if phase == "headers" else state.stream_entered.wait(), 5)
                    self.assertFalse(sdk_task.done())
                    self.assertFalse(consumer.done())
                    if phase == "consumer-cancel":
                        consumer.cancel()
                        with self.assertRaises(asyncio.CancelledError):
                            await asyncio.wait_for(consumer, 5)
                await asyncio.wait_for(asyncio.gather(adapter.cancel(), adapter.aclose(), adapter.cancel()), 5)
                release.set()
                if consumer is not None:
                    result = await asyncio.wait_for(asyncio.gather(consumer, return_exceptions=True), 5)
                    if phase == "predispatch":
                        self.fixed_error(result[0], "AI_INVALID_INPUT")
                    else:
                        self.assertIsInstance(result[0], asyncio.CancelledError)
                    self.assertTrue(consumer.done())
                await asyncio.wait_for(iterator.aclose(), 5)
                self.assertTrue(sdk_task.done())
                self.assertTrue(future.done())
                self.assertIsNone(adapter._send)
                self.assertIsNone(adapter._key)
                self.assertEqual(len(state.sent), 0 if phase == "predispatch" else 1)
                self.assertTrue(all(c.is_closed for c in state.clients))
                if phase != "predispatch":
                    self.assertTrue(state.transport.closed)
                if phase not in ("predispatch", "headers"):
                    self.assertTrue(state.stream.closed)
                with self.assertRaises(self.module.SDKAdapterError) as caught:
                    await asyncio.wait_for(anext(adapter.dispatch(handle)), 5)
                self.fixed_error(caught.exception, "AI_INVALID_INPUT")
                with self.assertRaises(self.module.SDKAdapterError) as caught:
                    await asyncio.wait_for(adapter.prepare(self.draft()), 5)
                self.fixed_error(caught.exception, "AI_INVALID_INPUT")
                self.assertEqual(self.attempts, [])

        for mutation in ("copy", "foreign", "body", "deadline", "fingerprint", "second-prepare", "second-entry"):
            with self.subTest(risk="handle-" + mutation):
                adapter, state = self.fixture()
                handle = await asyncio.wait_for(adapter.prepare(self.draft()), 5)
                send = adapter._send
                candidate = handle
                if mutation == "copy":
                    candidate = replace(handle)
                elif mutation == "foreign":
                    foreign, _ = self.fixture()
                    candidate = await asyncio.wait_for(foreign.prepare(self.draft()), 5)
                elif mutation in ("body", "deadline"):
                    object.__setattr__(handle, mutation, b"{}" if mutation == "body" else True)
                elif mutation == "fingerprint":
                    send.request.headers["authorization"] = "Bearer SENSITIVE-MUTATION"
                if mutation == "second-prepare":
                    operation = adapter.prepare(self.draft())
                elif mutation == "second-entry":
                    operation = send.http._transport.handle_async_request(send.request)
                else:
                    operation = anext(adapter.dispatch(candidate))
                with self.assertRaises(self.module.SDKAdapterError) as caught:
                    await asyncio.wait_for(operation, 5)
                self.fixed_error(caught.exception, "AI_PROTOCOL_INCOMPATIBLE" if mutation in
                                 ("fingerprint", "second-entry") else "AI_INVALID_INPUT")
                self.assertEqual(state.sent, [])
                state.factory.assert_not_called()
                sdk_task, future = send.sdk_task, send.response_future
                await asyncio.wait_for(adapter.aclose(), 5)
                self.assertTrue(sdk_task.done())
                self.assertTrue(future.done())
                self.assertIsNone(adapter._send)

        await self.assert_stream_close_failure_retains_ownership()

    async def assert_stream_close_failure_retains_ownership(self):
        """Normal SDK stop/DONE; only raw stream close fails, then recovers.

        Response.is_closed is not proof that its underlying stream was released.
        All assertions use public prepare/dispatch/aclose, observing private
        ownership without replacing any adapter state or cleanup implementation.
        """
        async def respond(request, state):
            state.response = self.sdk["httpx2"].Response(200, headers={"content-type": "text/event-stream"},
                                                       stream=state.stream)
            return state.response

        adapter, state = self.fixture(handler=respond, register_cleanup=False)
        raw_close = state.stream.aclose
        fault = SimpleNamespace(active=True, attempts=0)

        async def faulty_stream_close():
            fault.attempts += 1
            if fault.active:
                raise RuntimeError("SENSITIVE-RAW-STREAM-CLOSE")
            await raw_close()

        async def release_fixture():
            # Fault removal/direct raw close are teardown only, after assertions.
            fault.active = False
            try:
                await asyncio.wait_for(adapter.aclose(), 5)
            except self.module.SDKAdapterError as error:
                self.fixed_error(error, "AI_PROTOCOL_INCOMPATIBLE")
            finally:
                await asyncio.wait_for(raw_close(), 5)

        self.addAsyncCleanup(release_fixture)
        with patch.object(state.stream, "aclose", side_effect=faulty_stream_close):
            handle = await asyncio.wait_for(adapter.prepare(self.draft()), 5)
            send = adapter._send
            sdk_task, future = send.sdk_task, send.response_future
            events = []
            with self.subTest(risk="raw-stream-close", stage="dispatch-ownership"):
                with self.assertRaises(self.module.SDKAdapterError) as caught:
                    await asyncio.wait_for(self.consume(adapter, handle, events), 5)
                self.fixed_error(caught.exception, "AI_PROTOCOL_INCOMPATIBLE")
                self.assertFalse(any(e.kind in ("tool", "answer", "usage") for e in events))
                self.assertEqual(len(state.sent), 1)
                self.assertGreater(fault.attempts, 0)
                self.assertTrue(sdk_task.done())
                self.assertTrue(future.done())
                self.assertFalse(state.stream.closed)
                self.assertIs(adapter._send, send,
                              "Response.is_closed=%s must not erase ownership of the unclosed raw stream"
                              % state.response.is_closed)
                self.assertTrue(adapter._cleanup_failed)
                self.assertIsNotNone(send.stream)
            for attempt in range(2):
                with self.subTest(risk="raw-stream-close", stage="persistent-failure", attempt=attempt):
                    with self.assertRaises(self.module.SDKAdapterError) as caught:
                        await asyncio.wait_for(adapter.aclose(), 5)
                    self.fixed_error(caught.exception, "AI_PROTOCOL_INCOMPATIBLE")
                    self.assertFalse(state.stream.closed)
                    self.assertIs(adapter._send, send)
                    self.assertTrue(adapter._cleanup_failed)
                    self.assertIsNotNone(send.stream)
                    self.assertEqual(len(state.sent), 1)
            with self.subTest(risk="raw-stream-close", stage="recover-actual-release"):
                # Only remove the injected external close fault. The adapter must
                # retry the raw close itself, even if Response already says closed.
                fault.active = False
                try:
                    await asyncio.wait_for(adapter.aclose(), 5)
                except self.module.SDKAdapterError as error:
                    # Existing permanent safe failure reporting may remain sticky.
                    self.fixed_error(error, "AI_PROTOCOL_INCOMPATIBLE")
                self.assertTrue(state.stream.closed, "Public aclose must actually release the raw stream")
                self.assertIsNone(adapter._send)
                self.assertIsNone(send.stream)
                self.assertIsNone(send.response)
                self.assertIsNone(send.request)
                self.assertIsNone(send.fingerprint)
                self.assertIsNone(send.prepared)
                self.assertEqual(send.data, {})
                self.assertTrue(state.transport.closed)
                self.assertTrue(all(client.is_closed for client in state.clients))
                self.assertEqual(len(state.sent), 1)
                self.assertEqual(self.attempts, [])


if __name__ == "__main__":
    unittest.main()
