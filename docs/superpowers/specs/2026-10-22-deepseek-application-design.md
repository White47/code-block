# DeepSeek application assembly and offline delivery

## Prerequisites and scope

Run only after the wire-profile and connection phase gates. Goodall reviewed
this architecture read-only before implementation. This is the last bounded
production assembly/delivery slice of the existing Task10 admission queue, not
permission for live requests. No new dependency, provider fallback or public
bridge endpoint. AGENTS.md controls execution; routine choices are autonomous.

## Application-owned registration

_deepseek_admission.py owns _trusted_input_profiles() and
_trusted_connection_profiles(), returning fresh validated frozen records for
the closed profile ID, endpoint and model. Accounting trust and output-protocol
trust remain distinct. Neither user config nor provider records can register
trust. _production_provider_factory(config, key) validates exact configuration
and key before lazily loading the existing pinned SDK; unsupported targets fail
closed, not through the generic unproven provider.

Extract _load_sdk_modules() and _new_secure_transport(modules) from existing SDK
construction without changing security semantics: TLS verification, no proxy or
environment trust, no redirects, no retries. Real transport is constructed only
at dispatch; optional SDK modules are not imported during startup/settings.

_create_application_runtime() lazily imports service and injects the one factory
and both trust tuples. desktop._create_desktop_bridge uses this constructor;
ordinary _AssistantRuntime() stays unbound for base/offline uses. Both connection
and explanation already use the runtime's existing factory seam. Initial state
remains disabled/no key; installation, startup and opening the sidebar send zero
requests. SDK absence yields the existing dependency error only when requested.

## Permission and evidence

Application wiring makes an action available; it does not perform or authorize
it. A user-initiated connection test sends only the two fixed source-free probes.
Explanation additionally requires current configuration, explicit file disclosure
consent and active Send. Keys remain in memory, never code/logs/preferences.
Agent-run acceptance uses owned synthetic fixtures and fake HTTP only. No real
provider request, payment, source upload or credential collection is authorized.

## Offline production-path verification

Add a fixed-purpose scripts/verify_assistant_production.py using the existing
offline_guard before optional SDK loading. Replace only the secure transport
factory with bounded in-memory SSE; use actual application assembly, production
factory, pinned SDK, capture, budget/service and local validation. Prove both
connection capability and the existing seven-section explanation with synthetic
local evidence; record safe counts/cleanup/artifact identity, not prompt/body/key.
An old fake ProviderAdapter smoke alone cannot prove production SDK wiring.

Windows isolation correction: stdlib socketpair creates asyncio's internal
self-pipe using loopback TCP, so the reused guard's blanket connect patch blocks
lazy service-loop construction. The guard may construct only its own socketpair:
fresh loopback listener, direct saved connect on its own new client, authenticated
peer endpoints, bounded construction and full cleanup. All global DNS/TCP/UDP
denials remain active throughout. No arbitrary loopback allowance or global
unpatch window; this is internal test plumbing, not provider transport.

### Root evidence mapping correction (2026-10-23)

The actual production-SDK probe found AI_EVIDENCE_INVALID before final dispatch:
inject_root records a logical message pointer, while the SDK's JSON instructions
insert a system message and correctly remap source/metadata occurrences. The
service verifier still indexed the final body with the old logical pointer.
Debugger isolated this mismatch; Ampere reviewed this bounded core correction.

Only response_session.py and a new focused test file need change. Reconstruct
the canonical root header/content from the exact frozen fragment and anchor;
check input content/source_start against it. Strictly parse the bounded final
JSON, rejecting duplicate fields, non-finite values, malformed UTF-8/BOM and
invalid types/bool offsets. Independently find source and metadata occurrences
with the precise full spans/path. Each must identify one actual /messages/N/content
inert user message whose entire content equals the canonical root. Require one
unique candidate per group and the same actual pointer; reject missing, duplicate,
ambiguous, cross-group or role/content/path mismatches. Historical same source
with a different evidence ID is not the current root. Validate the logical
pointer's canonical shape/bounds but do not use it to index or guess a +1 shift.

No substring grant, provider capability trust or weaker SourceProof. Service
accounting still precedes root verification; only then can the evidence ID enter
provided. Parser/IR/SDK mapping and public DTOs are unchanged. Test shifted final
and unchanged generic success plus hostile attribution refusal before rerunning
the original production-path probe. This fix enters the phase's single upcoming
independent review; it is not a second review of an already-closed diff.

Reuse verify_assistant_native.py --source/--wheel-dir/--exe --fake-only and its
owned fixture/lifecycle. Add a fixed DeepSeek smoke scenario alongside existing
fake-provider coverage, no arbitrary module/project/endpoint arguments. Only
owned loopback asset/CDP communication is permitted in native smoke. Frozen
desktop_entry already has a private smoke dispatch seam; do not add public API.

## Build and delivery

Task3 architecture (Pascal): keep the native network allowance inside the owned
host, default empty; allow only two strict integer TCP ports at literal IPv4
loopback, deterministic local name results without OS DNS, and the existing
owned socketpair. This is Python socket isolation, not an OS/WebView2 firewall.
A host-only BottleServer adapter fixes the candidate asset port and refuses
fallback or duplicate startup; check the actual WSGI listening address as well
as the returned origin and window URL. Restore the adapter after cleanup.
Only explicit source verification may bootstrap its own adjacent src directory;
wheel and exe never fall back to source.

Task3 debugger evidence (Carson): the failed frozen build mixed SDK probe
site-packages ahead of the desktop environment, collecting pythonnet 3.2.1
instead of the previously verified 3.2.0. Pin the existing Windows pythonnet
dependency to 3.2.0 and build in a single isolated environment, with critical
module origins checked before freezing. Existing local installed packaging
tools may be copied unchanged into that task-only environment when no cached
wheel exists; SDK installs still use the audited hash lock. Keep the 45-second
native readiness limit and strict owned URL checks; rebuilding is not acceptance.

Task4 single-review corrections: the Python offline guard also denies reverse
DNS entrypoints (including getnameinfo), with no uncounted OS resolver fallback.
Native host proof is bounded strict UTF-8 JSON, duplicate/non-finite/extra fields
rejected, exact boolean flags and integer counters required for each fixed mode.
The native verifier may publish passed/return zero only after owned-process
cleanup; force, escalation or cleanup errors invalidate a candidate success.
These are harness/projection corrections, not semantic-core invariant changes;
close with affected tests and applicable gates, without another independent review.

Use existing exact pins, hash lock and local package cache. codeviz.spec collects
only required dynamic SDK modules and metadata, never every provider. Build a
fresh wheel and isolated base/ai environments offline; -I proves installed origin.
For exe reuse validated frontend output and existing dependencies rather than
the build script's unconditional npm ci download. Use a clearly scoped offline
build option or direct existing PyInstaller invocation; preserve user artifacts.

Close with affected tests, one independent phase review and full applicable
gates on the final snapshot. Reuse unchanged frontend results, but build artifacts
from current backend code. Native smoke needs actual WebView2 evidence, not a
browser substitute. Report unavailable GUI/Linux/live tests as skipped, never
passed. Do all independent offline work before asking for genuinely human-only
credentials/paid request/disclosure/manual action. Do not claim final E release
acceptance until required evidence exists; leave an exact resumable checkpoint.

### SDK worker error boundary (2026-10-23)

The service preserves auth, rate-limit, network-timeout and budget codes only
for the exact `SDKAdapterError` type exported by an already-loaded local
`sdk_provider` module and only through a service-owned fixed allow-list. It does
not import or load the optional SDK to classify failures. Arbitrary exceptions,
subclasses, unknown codes and forged `.code` attributes remain protocol failures;
existing service/response/agent handling and all budget accounting are unchanged.
