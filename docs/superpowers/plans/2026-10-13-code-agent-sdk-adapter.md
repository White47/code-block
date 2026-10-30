# Code Agent SDK Adapter Implementation Plan

> **For agentic workers:** Use subagent-driven-development or executing-plans under AGENTS.md routing. Parent must read this entire plan before implementation. test_writer writes failure-first tests, core_implementer implements the adapter, and reviewer independently inspects the result. This is execution of the approved runtime design, not a new product/design checkpoint.
>
> **Process amendment (2026-10-15):** process is governed by the repository `AGENTS.md`. Do not load `subagent-driven-development` or `executing-plans`; do not run a per-task spec/quality review pair. `reviewer` runs once at the phase boundary, not per task. Technical requirements below are unchanged.

**Goal:** Implement the missing optional `sdk_provider.py` with bounded internal messages/events and one frozen, explicitly released HTTP send per preparation.

**Architecture:** Preserve the existing stdlib ProviderDraft/PreparedRequest/ProviderEvent ports. Translate explicit service messages through the pinned low-level Model API, suspend at the injected httpx2 transport boundary, and resume the same request only through service-owned dispatch. Service owns consent, freshness, reservations, tool execution, evidence validation and job publication.

**Tech Stack:** Python 3.11+, unittest; isolated pydantic-ai-slim[openai] 2.55.0, OpenAI 3.29.0, httpx2 2.13.1, Pydantic 2.14.1. No new dependencies.

## 1. Authority, inspected facts and scope

Read the COMPLETE approved `docs/superpowers/plans/2026-10-12-code-agent-runtime.md` before execution; C1–C7 govern this slice. C2 SPEC has passed and QUALITY is underway; this document does not relabel that work complete. Per parent evidence, the isolated source SDK probe passed 22 checks with four skips, and the minimal SDK EXE passed independent review. These prove a synthetic seam, not production authorization, full application packaging or API acceptance. No actual API is authorized.

Inspected actual `provider.py` and `config.py`: the factory lazily imports `.sdk_provider`, rejects None key before import, and exposes exactly the four port methods below. ProviderDraft, PreparedRequest and ProviderEvent are frozen but have **no validation**. Config/MemoryKey/TransportPolicy validate their own inputs; their immutability does not validate draft bytes, deadline, handle ownership or wire behavior. Current ProviderError accepts only four codes; unsupported C7 codes silently become AI_INVALID_INPUT.

Inspected `scripts/verify_assistant_dependencies.py`: `make_gate()` derives an AsyncBaseTransport, captures `request.content` and a private `(method, URL, body, raw headers)` fingerprint, pauses the SDK task at an asyncio event, then calls the underlying transport with the identical request. Never read an opaque request stream to try to freeze it. The probe uses actual ModelRequest/ModelResponse parts, ModelRequestParameters, ToolDefinition, OutputObjectDefinition, OpenAIChatModel and OpenAIProvider; it does not run Agent.run. Its 64 KiB synthetic request cap is a fixture constant, not C5's production 256 KiB bound. Its `approve(wire, input_bound, output_bound)` is a harness assertion, not a real consent/budget port.

Parent integration note: C2 final independent QUALITY has now passed. Its fixed table has one coordinated C6 addition, `AI_EVIDENCE_INVALID` with `Assistant evidence validation failed.`, taking the table from four to five codes. This does not add the other SDK/C7 codes or alter this adapter's fixed-error boundary. No SDK implementation acceptance follows from that C2 update.

Installed source also confirms: Model.request_stream is an async context manager; the exhausted StreamedResponse has `get()` and **property** `usage`, not `usage()`. OpenAIChatModel.request does not validate final schema/evidence. SDK profile mapping can change system roles/output-limit fields; capture validation must reject unexpected transformations. OpenAI falls back to environment organization/project when arguments are None. AsyncHTTPTransport exposes verify, trust_env, proxy and retries; AsyncClient's verify flag alone does not secure an injected underlying transport.

This planning turn owns ONLY this new document. Future slice files:

| File | Future change |
| --- | --- |
| `src/codeviz/assistant/sdk_provider.py` | New optional adapter, internal parser/event framing, capture gate, bounded response wrapper and private injection seam |
| `tests/test_assistant_sdk_provider.py` | New SDK-free fake-seam boundary/lifecycle tests and explicitly isolated actual-SDK tests |
| `tests/test_assistant_provider.py` | Extend only lazy factory/no-key/no-extras regression assertions |

Read-only references: provider/config, existing tool schemas, runtime and response-contract plans, isolated installed sources and dependency verifier. No changes to provider dataclasses, config/consent/B contracts, service/jobs/response, frontend, gate script, manifests, locks, packaging, git or network in this slice. No generic provider registry, protocol migration, environment-based fake mode, SDK tool loop, model discovery or tracing framework.

## 2. Exact local signatures and validation ownership

```python
# Public exports of the new module; optional SDK imports occur inside factory.
def create_provider(config: ProviderConfig, key: MemoryKey | None) -> ProviderAdapter: ...

class SDKProviderAdapter:
    async def prepare(self, draft: ProviderDraft) -> PreparedRequest: ...
    def dispatch(self, prepared: PreparedRequest) -> AsyncIterator[ProviderEvent]: ...
    async def cancel(self) -> None: ...
    async def aclose(self) -> None: ...

# Private test seam, never exported by a bridge or selected via environment.
def _create_provider(
    config: ProviderConfig, key: MemoryKey | None, *,
    sdk_loader: Callable[[], object],
    transport_factory: Callable[[], object],
    clock: Callable[[], float] = time.monotonic,
) -> ProviderAdapter: ...
```

The private loader returns a dict with exactly keys `httpx2`, `openai`, `models`, `messages`, `sdk`, `providers`, `profiles`, `output`, `tools`, mapped respectively to httpx2, openai, pydantic_ai.models, pydantic_ai.messages, pydantic_ai.models.openai, pydantic_ai.providers.openai, pydantic_ai.profiles.openai, pydantic_ai.output, pydantic_ai.tools modules or matching fakes. This is our Python injection seam, not a claimed SDK API. The transport factory produces an owned async transport exposing `handle_async_request(request)` and `aclose()`. Test supplies recording fakes; production supplies the secure transport described below. Constructor/factory validates config/key before loading anything. Importing this module or ordinary application modules requires no SDK. Missing optional imports continue through existing lazy factory as AI_DEPENDENCY_MISSING.

Adapter entry validates exact concrete draft type; exact bytes length 1..262144; strict UTF-8/no BOM; max_output_tokens exact int, not bool, 1..2000; finite int/float monotonic deadline, not bool, strictly greater than clock(). Do not reset deadline during prepare, dispatch or iteration. Reject a second prepare while one request is preparing/prepared/in flight; allow sequential preparations after successful per-send cleanup. cancel/aclose permanently disable the instance. Public PreparedRequest is not a capability by value: dispatch requires the **identical returned object**, its stored random opaque ID, all original field values and a live unused private continuation. Recheck exact types/bounds and private request fingerprint before release and again at underlying transport entry. Forged/copied/replaced/foreign/reused handles fail with zero sends.

All these checks belong in the new adapter, not new dataclass __post_init__ methods. Config target validation is reused at capture and handoff. A local `SDKAdapterError(ValueError)` carries only fixed C7 code/message, with codes AI_INVALID_INPUT, AI_PROTOCOL_INCOMPATIBLE, AI_AUTH_FAILED, AI_RATE_LIMITED, AI_NETWORK_TIMEOUT and AI_BUDGET_EXCEEDED. Do not pass unsupported codes through today's ProviderError or widen A errors. No raw exceptions/chains/SDK objects in outward errors; cancellation propagates asyncio.CancelledError. Convert failures outside exception handlers so sensitive exception context is not retained. Service later translates the fixed codes at its own boundary.

## 3. ProviderDraft.body v1 (internal only)

Canonical service JSON: UTF-8, ensure_ascii=False, separators=(',', ':'), allow_nan=False. Exact root keys, all required:

```json
{"version":1,"mode":"final","messages":[{"role":"user","content":"Explain this saved function."}],"tools":[],"answerSchema":{},"sourceTags":[]}
```

`answerSchema:{}` above illustrates the slot only; final mode requires the exact schema described below and rejects an empty schema. No model, URL, key, headers, max tokens, deadline, arbitrary SDK settings or evidence authority inside this envelope; those are existing config/draft fields or service-owned state. Modes are `tools`, `final`, `text`. `text` supports the approved source-free synthetic probe/provisional text; it never represents a validated final answer.

Closed message forms (no extra fields):

* system/user: `{role, content}` with nonempty UTF-8 string content; system only at the beginning.
* assistant: `{role:"assistant", content:string|null, toolCalls:[{id,name,arguments}]}`; text-only has an empty toolCalls array, call-only has null content. arguments is a strict JSON **object encoded as a string**, retained verbatim. Reject null content with no calls.
* tool: `{role:"tool", toolCallId, name, content}`; content is a string holding the service's filtered canonical ToolResult JSON or synthetic source-free return. Require an earlier outstanding assistant call with exactly matching ID/name, one return per call, no unresolved calls at end of request.

At most 40 messages total (C5 recent-six history selection is the service's responsibility); at most three tools, unique names. At most eight historical calls across messages. IDs 1..512 printable UTF-8 chars, names only search_symbols/read_source_excerpt/get_local_relations; arguments <=8192 UTF-8 bytes. No SDK retry parts, multimedia, external URLs/files, native tools, reasoning parts or instructions outside these messages. Tools are exactly `{name, description, parameters}`; description <=2000 chars, parameters a bounded local object JSON schema. Reject external references, executable callbacks and supplied strict/extra SDK options. tools mode requires 1..3 definitions and null answerSchema; final/text require tools=[]; text requires null answerSchema. Service supplies the existing B5 argument schemas; adapter sends them unchanged and does not execute or expand them.

Actual B5 dispatch shapes, not invented exported JSON-schema APIs: search requires query, optional limit; relations requires seed={kind,id}, optional hops/max_nodes/max_edges; excerpt requires file_path/start_line/end_line, optional start_column/end_column. Service constructs provider schemas from these actual forms and owns any handling of optional/default/null values before B5 dispatch. Adapter only bounds and preserves definitions/argument strings; it never imports a nonexistent B5 schema factory or changes B5 admission rules.

Final schema is a fixed local schema assembled from C6, not arbitrary caller output:

* root object: only required `sections`, array of section objects;
* section object: only required `kind` (enum summary/entry/steps/branches/relations/evidence/unknowns) and `claims` (array of claims);
* claim object: only required `text:string`, `basis` enum static/inference/unknown, `evidenceIds` array of strings, `relationEvidenceId` string-or-null;
* every object has additionalProperties=false. No definitions, refs, defaults, titles, navigation or extension fields. Use this single structural schema for SDK output; compare supplied answerSchema against this local schema exactly.

C6's seven ordered sections, <=20 claims/section, <=60 total, <=2000 characters/claim, <=8 unique IDs and <=64 KiB answer remain service/response validation, not proof from remote schema. Adapter emits an **unvalidated** answer candidate; no evidence/card/action is created. This avoids duplicating the authoritative response validator.

2026-10-19 accepted C2 connection-profile integration: the sole exception to the B5-name/seven-section grammar is the exact pair produced by internal `connection_profile.connection_draft`. They use the existing `tools` and `final` modes, one fixed user message each, no history/source/metadata, a synthetic `p` tool with empty-object parameters and a strict empty-object final schema (wire output name `p`). The complete logical request must match a fixed profile; changing its messages, tags, definitions or schema loses the exception and cannot introduce arbitrary schemas/tools. No new mode, envelope field, provider dataclass or public bridge contract is introduced. The two requests are independent and remain bounded by 2 sends, 1024 cumulative reserved input, 128 reserved output and 15 seconds. This grammar exception is not a budget/disclosure proof: the real adapter's absent framing bound and incomplete attribution remain fail-closed until separately evidenced, and no connection is sent on an unproved reservation. No budget, test or transport safeguard is relaxed.

`sourceTags` is a list of exactly `{pointer,start,end,file}`. pointer is a JSON pointer to a specific message content string or assistant toolCalls arguments string; start/end are strict integers indexing Unicode code points in that decoded string, `0 <= start < end <= len(string)`. file is the service-validated normalized relative file identity, <=2048 chars. Tags must be sorted by pointer/start/end/file, disjoint within a string, <=1000 total and <=8 distinct file strings. These are internal disclosure-accounting hints only, with no evidenceId, hash, target, confidence or authority. They are never mapped into SDK messages or sent as a wire field. Service alone obtains raw saved source, validates consent/provenance/whole-file secrets and tags every occurrence, including history quotations and retransmissions. Adapter cannot detect arbitrary untagged source supplied by trusted Python service; do not claim it can.

Freeze attribution algorithm: derive the expected OpenAI message array from the closed forms (assistant calls become type=function/function{name,arguments}; tool name is matched locally but omitted on wire). Compare the complete captured messages array exactly to this expected array; require the configured model, unchanged tool definitions and fixed final schema. Thus string slots/order/content/argument encodings cannot silently merge, split, transform or duplicate. Map each tagged slice to its corresponding exact captured string slot and count **each tagged occurrence**, not distinct snippet text. JSON escaping is decoded for source-character accounting; byte/token budget uses the original final body bytes, including escapes/schema/framing. Tags never add a second copy of source. If a tagged string appears in an unexpected wire string field or mapping adds source-bearing material, fail preparation rather than guess attribution. Adapter retains this mapping privately only while the handle lives; service already has tags and may inspect the frozen body to perform C5 reservation. No new PreparedRequest field/accessor/evidence registry is needed.

For every inbound JSON boundary, limit bytes before decoding and pre-scan nesting <=16 before json.loads; reject duplicate keys, NaN/Infinity, invalid UTF-8, trailing data and bool-as-int. Bound each arbitrary array/object to <=1000 members before retaining it, with narrower counts above. Source tags and SDK buffers count toward the service's memory ownership when retained; no global cache/history in adapter.

## 4. Actual SDK path and final request freeze

Map one service message at a time: system/user -> ModelRequest(SystemPromptPart/UserPromptPart); assistant -> ModelResponse(TextPart and/or ToolCallPart with string args); tool -> ModelRequest(ToolReturnPart with **string** content). No dict conversion of a tool return that would change serialization. Instantiate ToolDefinition(name=..., description=..., parameters_json_schema=..., strict=True). Final parameters: ModelRequestParameters(output_mode='native', output_object=OutputObjectDefinition(json_schema=..., name='CodeAgentAnswer', strict=True)); tools mode uses function_tools; text uses empty parameters.

Use OpenAIChatModel(config.model, provider=OpenAIProvider(openai_client=api), profile=OpenAIModelProfile(supports_json_schema_output=True, openai_chat_supports_max_completion_tokens=True)). This deliberately supports one pinned chat-completions profile; reject incompatible providers rather than discover models or silently switch profiles. Settings: max_tokens=draft.max_output_tokens, parallel_tool_calls=False; tool_choice='none' for final/text. Use one `async with model.request_stream(messages, settings, parameters)` task per prepare and preserve that task/context manager through dispatch. Never call model.request a second time to reconstruct the frozen request. Profile flags are encoding choices, not claims about arbitrary remote capability.

Client construction matches the proven source: httpx2.AsyncClient(transport=capture_gate, follow_redirects=False, trust_env=False, timeout=remaining); openai.AsyncOpenAI(api_key=key.value, base_url=config.base_url, http_client=http, max_retries=0, organization='', project='', webhook_secret=''). No event hooks, custom auth, default query overrides, fallback clients, env org/project/key/base URL, instrumentation or logs. Keep SDK imports inside loader; do not import/use Agent, infer_provider or automatic price/telemetry services.

CaptureGate inherits the loaded httpx2.AsyncBaseTransport. At its first handle_async_request:

1. Require materialized exact bytes from request.content, <=262144, POST and exact config endpoint, no query. Validate content-type application/json, content-length equal byte length, authorization exactly the explicit Bearer key, no organization/project/proxy authorization or ambient extra headers. SDK's fixed ordinary metadata headers may remain, retained only privately. Set Accept-Encoding=identity on client construction and verify it; reject compressed responses.
2. Parse bounded final JSON, verify model/messages/tools/schema/profile output limit (exactly one of max_tokens/max_completion_tokens, strict int equal draft allocation; this profile expects max_completion_tokens), stream=true, no unexpected source fields/settings. Freeze PreparedRequest with these final bytes and the original deadline. Keep captured request, raw-header fingerprint and suspended continuation private, repr excluded.
3. Signal readiness and await a private loop-owned **response future**. CaptureGate never calls the underlying transport: its sole continuation is to return the owned Response supplied by dispatch to the already suspended SDK call. prepare waits for readiness **or task failure**, within original deadline, and returns without constructing external transport/DNS/sockets. An opaque/unread request body, premature task exit or second capture entry fails; do not call aread on an unknown request stream.
4. On the same worker loop, service performs C4's final state-lock guard, marks the permit dispatched, releases the lock, then directly awaits the first `anext(adapter.dispatch(prepared))`. Calling dispatch alone merely constructs an iterator. No create_task, scheduling barrier, wait-for task wrapper or other scheduling await may intervene before underlying entry. The iterator's synchronous prefix consumes the identical handle once, rechecks clock/cancel/state/private final fingerprint, synchronously constructs the secure transport if needed, and **directly awaits underlying.handle_async_request(the_same_captured_request)**. Python executes this nested coroutine through entry before its first suspension; DNS/connect/read can then await outside locks. Do not release an Event to make the SDK task perform the send: Event release -> SDK task wake -> transport entry is a scheduling gap, and the adapter's local cancel bit cannot substitute for service cancelRequested/revision checks there. No new AsyncClient.send, SDK serialization, auth replay, request rebuild or redirect middleware. A second SDK capture entry always fails, even after an SDK error.

After underlying entry returns, dispatch owns the Response, checks/wraps it with section 5's bounded stream policy and completes the response future exactly once; the preserved SDK task resumes **only response processing**, not another send. Installed httpx2 `_send_single_request` awaits transport.handle_async_request, then attaches the original request, wraps the returned async stream and processes response metadata; OpenAI's original send and OpenAIChatModel.request_stream then continue their existing response/context-manager/parser path. Return the actual httpx2 Response through that boundary, not a parsed ModelResponse or reconstructed request. Retain the same body and raw-header fingerprint privately through handoff. This uses the actual injected transport contract plus an ordinary asyncio future, with no new public method/field or SDK freeze API. Source inspection establishes the path; the isolated implementation tests must still prove identical request/body/auth, one underlying entry and preserved parser behavior.

The underlying await now belongs to the dispatch consumer task, while the original SDK task waits on the response future. Cancellation/close must therefore stop and join **both** owned activities (never self-join), invalidate/cancel the future and close any returned Response not transferred to the live SDK continuation. If future/SDK task was cancelled before delivery, close the Response rather than set a completed/cancelled future. Underlying failure/deadline must cancel and drain the waiting SDK task so it cannot hang, retry or resume after closure. Once delivered, existing SDK context-manager cleanup plus adapter-owned idempotent response/client/transport cleanup applies. Service gate closure and B-worker release remain separate C4 obligations. Extend the planned isolated handoff/cancellation assertions to cover these two tasks; the script's Event-based synthetic probe is not proof of this corrected no-gap service handoff.

Production external transport: httpx2.AsyncHTTPTransport(verify=ssl.create_default_context(), trust_env=False, proxy=None, retries=0). Explicitly check CERT_REQUIRED and check_hostname; client flags do not replace this guarantee. Permit only 2xx responses; 301/302/307/308 fail even same origin, 401/403 map auth failure, 429 rate limit, other statuses protocol incompatibility. Transport timeout/deadline maps fixed timeout. No automatic resend/repair on any failure.

The **service** issues budget reservation and checks complete context/config/consent/conversation revisions before calling dispatch. This port cannot authenticate consent or carry a permit record: invoking dispatch is a trusted service operation, not bridge/model access. Releasing a synthetic test handle does not authorize a live API. Real service gate remains disabled until approved runtime integration and explicit user permit. A cancelled undispatched reservation is not refunded and is not reported as actual disclosure.

## 5. Bounded ProviderEvent and response framing

All payloads exact bytes, UTF-8. These are internal events, never public bridge DTOs:

| kind | Exact payload and emission |
| --- | --- |
| text | Raw UTF-8 plain text chunk <=8192 bytes, split on code-point boundaries; provisional, never actionable. Emit PartStartEvent(TextPart).content and PartDeltaEvent(TextPartDelta).content_delta exactly once, not a second snapshot replay. |
| tool | Canonical JSON `{"id":str,"name":str,"arguments":object}` <=8192 bytes including framing; original argument string also <=8192. Emit only a fully assembled, valid call at normal end of the stream, never argument deltas. No execution. |
| answer | Canonical JSON `{"index":int,"last":bool,"content":str}` <=8192 bytes including escaping/framing. Contiguous indexes from zero; nonempty code-point-safe slices of the final raw JSON candidate, exactly one last=true. Joined content <=65536 UTF-8 bytes. Final mode only, after normal termination; service assembles and validates. |
| usage | Exactly `{"inputTokens":int|null,"outputTokens":int|null,"basis":"actual"|"unknown"}` canonical JSON <=8192 bytes, at most once at normal end. Strict nonnegative counts <=2**63-1; actual only when raw provider usage was present and valid, otherwise both null/unknown. Never treat SDK default zeros as reported actual usage. |

Every successful tools/text stream ends with usage; final ends with answer fragments then usage. Text may precede tools or final candidate. No answer outside final, no tools outside tools mode; tools must be declared names, unique IDs, at most eight per response (parallel tool calls remain disabled). Reject mixed final/tool/unsupported reasoning/audio/file/refusal parts, incomplete EOF, length/content-filter stop or malformed terminal framing; no final/tool/usage completion events on failure. Text already yielded remains provisional. End-of-stream uses exhausted response.get().parts and finish_reason/state to avoid emitting tool deltas twice; `response.usage` is inspected only with raw presence information.

Before SDK parsing, wrap the underlying response AsyncByteStream: require SSE, validate status/content encoding, count **aggregate raw UTF-8 response bytes <=262144** including all SSE frames/usage/DONE, not per-chunk resets. Reject any next chunk crossing the ceiling before accumulating/yielding it to SDK. Maintain <=262144 bounded frame buffer; pre-validate each complete data JSON frame using the strict JSON/depth/member rules. Track fragmented tool arguments by call index with an 8192-byte cap before SDK accumulation. Require one choice/index zero, normal terminal finish and DONE; EOF without these fails. Do not accept arbitrary provider extensions as actionable events. Close underlying response stream on limit, parse failure, deadline or cancellation.

Separate decoded generated-output byte counter includes all text/final JSON, tool names/IDs/argument strings and tool-call JSON framing, counted once despite provisional text plus answer-fragment emission. For this deliberately conservative unknown-tokenizer slice, stop when that count exceeds max_output_tokens bytes, as well as 256 KiB aggregate raw response. Byte-per-token is a conservative bound for this supported byte-based text/tool profile, not a cost estimate; reasoning/hidden-output profiles are unsupported. Enforce reported outputTokens <= allocation too; discrepancies fail compatibility and prohibit another implicit send. Service owns full input framing bound, cumulative <=2000 output allocation/<=6 sends and any verified model tokenizer later; adapter never raises limits automatically. Keep deadline checks during capture/frame parse/event waits as well as transport, using the original monotonic deadline. Bounded one-event backpressure queue; no unbounded token tasks or detached producer after iterator abandonment.

## 6. Resource and cancellation boundary

Per preparation retain only bounded draft mapping, captured request/fingerprint, one SDK task/context manager, one response and a bounded event queue. After completion/failure discard draft/source tags/body/header references, close response/client/transport, join the SDK task and clear handle state before another preparation. No adapter history or evidence. Drop the adapter's key references on permanent close; service separately drops MemoryKey/config/history references on revoke/replacement/shutdown. Dropping references is not physical erasure.

cancel/aclose are idempotent: synchronously close the adapter release gate first, invalidate every handle, cancel suspended/in-flight SDK work, drain/join it, close stream/response/OpenAI client/HTTP client/transport and clear retained sensitive references. Successful return guarantees **this adapter's** gate is permanently closed and its owned resources released; cleanup failure raises a fixed safe issue and never reports success. Pre-dispatch close then releasing an old barrier gives zero underlying entries; post-dispatch close may give one entry/billing and never a second. Iterator exhaustion/failure/explicit aclose and cancelled consuming tasks run cleanup; service must close abandoned iterators/adapters explicitly, not rely on destructor/GC.

Adapter return is insufficient for C4 cancellation confirmation: service first sets cancelRequested and queues its loop command, closes its own actual dispatch gate/permits, invalidates publication/tool execution, awaits all adapter and B-worker cleanup, then publishes cancelAccepted/dispatchClosed/resourcesReleased. If any B worker/OS work is still running, retain requested/closing and its admission slot. Adapter emits no job status/cancel confirmation, never closes evidence or clears conversations, and cannot prove actual service gate closure. Parent must implement these separate lifecycle requirements in the runtime tasks.

## 7. Failure-first execution and review

Execute only after parent has read the complete document. All fixtures use synthetic source/keys and fake transports with outbound DNS/TCP/UDP guards. Base suite requires no SDK; isolated suite runs against existing installed sources, without acquisition or real API. Required actual-SDK tests cannot be silently counted as accepted skips.

- [ ] **test_writer:** Create test file with SDK-free loader/recording-transport fakes, async barriers and controllable clock. First assertions: normal module import adds no pydantic_ai/openai/httpx2 modules; None key loads nothing; adapter API exists. Run base command below and record expected assertion RED, not an unexplained collection error.
- [ ] **test_writer:** Add protocol cases: each closed message form/tool history; strict schema identity; source repeated in two messages/Unicode escaping/quoted history; tags never sent; wrong types/bool/NaN/deadline/duplicate keys/depth17/extra fields; 256 KiB exact/one-over body, tag overlap/unattributable mapping, opaque request body. Assert validation occurs before transport creation and service tags do not establish evidence authority.
- [ ] **core_implementer:** Implement only module, parser/mapping and lazy private injection seam to turn that group GREEN. Preserve existing four frozen ports and C2 tests. No production dependency adoption.
- [ ] **test_writer:** Add capture/handoff cases: zero sends and zero transport construction at prepare; frozen real wire differs from draft; correct profile/tool/schema/output envelope; same request identity/body/raw auth headers at underlying entry; copied/foreign/mutated/reused handles; second prepare/second SDK entry; poisoned env key/base/org/project/proxies/tracing; status 301/302/307/308/401/403/429/500 and transport errors each <=one underlying entry/no retry.
- [ ] **core_implementer:** Implement capture gate and lazy secure external transport. Run previous groups plus new RED-to-GREEN tests. Verify the underlying secure transport flags, not merely AsyncClient flags; fake constructor spies never open a socket.
- [ ] **test_writer:** Add stream groups: split UTF-8/SSE/frame/tool args, missing DONE/incomplete/length/refusal/unsupported parts, duplicate keys before SDK parser, tool args 8192/8193, tool event framing overhead, final answer 65536/65537, answer chunk index/last correctness, raw aggregate 262144/262145 over many chunks, generated-output byte cap, actual/unknown usage, reported overflow, elapsed original deadline while blocked. Assert no partial tool/answer completion, bounded backpressure and no extra SDK send on invalid schema.
- [ ] **core_implementer:** Implement response wrapper/event framing/cleanup; run groups GREEN. Service still performs semantic answer/evidence validation and one optional newly prepared/budgeted repair; adapter performs none.
- [ ] **test_writer:** Add pre-dispatch cancellation barrier proof, deadline during prepare, post-dispatch blocked response, concurrent close/cancel/repeated calls, consumer cancellation/explicit iterator close, SDK task failure before ready and resource-close failure. Assert zero/one underlying entries as applicable, joined tasks/closed streams/clients, permanent unusable handles and cleared private references. A test service harness demonstrates adapter closure alone is not B-worker/job confirmation.
- [ ] **core_implementer:** Run isolated actual-SDK tests proving the same facts with MockTransport/FakeSSE and source classes from the gate. Reuse its logic as reference, not script's synthetic approve API as production consent. Extend base factory tests only as needed and retain missing-extra behavior.
- [ ] **reviewer:** Independently inspect scoped diff and actual pinned seams, challenge request mutation/ambient defaults/unbounded response accumulation/cancellation races. Tests passing is necessary but insufficient. Record SPEC and QUALITY separately; failed required SDK fact stops adapter adoption, with a concrete incompatibility report to parent. Do not silently substitute a provider or escalate to final_auditor solely because this task is large.

Commands from repository root (future execution; not run by this planning turn):

```powershell
.desktop-venv/Scripts/python.exe -B -m unittest discover -s tests -p test_assistant_sdk_provider.py -v
.desktop-venv/Scripts/python.exe -B -m unittest discover -s tests -p test_assistant_provider.py -v
build/assistant-sdk-probe-20261012/Scripts/python.exe -B -m unittest discover -s tests -p test_assistant_sdk_provider.py -v
.desktop-venv/Scripts/python.exe -m ruff check src/codeviz/assistant/sdk_provider.py tests/test_assistant_sdk_provider.py tests/test_assistant_provider.py
.desktop-venv/Scripts/python.exe -m mypy
.desktop-venv/Scripts/python.exe -m mypy --platform linux
```

Tests insert the repository src path using existing test conventions; actual-SDK group is explicitly skipped only in SDK-absent base runs and must execute in isolated runs. Record counts separately. No rerun of frozen/native gate, package install/build or public metadata/API fetch is needed for this bounded slice. Parent handles final broader runtime checks and inspected git diff under its authorization.

## 8. Architecture review outcome

**Approved for the bounded failure-first adapter implementation above.** This architect review resolves the previously unspecified draft/event framing without changing existing port fields or approved service responsibilities. Exact-wire accounting occurs after SDK mapping, release uses one unchanged underlying request, optional imports remain lazy, source tags have no evidence authority, and local adapter cleanup is explicitly separated from service cancellation confirmation.

Acceptance is conditional on actual isolated-SDK tests and independent implementation review; the existing synthetic SDK/EXE passes are supporting evidence only. Service authorization/source gate, full memory accounting, cumulative budgets, real evidence validation, two-phase cancellation and application packaging remain their approved C/D/E tasks. No live API permission or production-runtime acceptance is granted. Parent must read this entire plan before dispatching implementation.
