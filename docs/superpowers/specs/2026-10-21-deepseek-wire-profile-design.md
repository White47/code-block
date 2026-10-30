# DeepSeek SDK wire profile: offline capture proof

## Authority, boundary and choice

This is the next SDK-only slice of Task10/P3. The user approved estimated input
accounting and JSON formatting with local schema validation, not paid requests
or source disclosure. The capability slice is accepted. Goodall's architecture
for this slice is recorded in `../plans/2026-10-21-deepseek-admission.md`.
AGENTS.md governs execution. Preserve the existing dirty workspace and tests.

Reuse the existing suspended SDK transport seam with a closed private profile.
Do not fork a second HTTP stack, weaken the generic capture rules, or patch a
serialized generic request after the SDK has produced it. A private profile
lets the real SDK encode the intended protocol, then verifies those final bytes.
The generic factory continues to return unproven input framing and incomplete
attribution (`None`/`False`), and its existing rejection tests remain unchanged.

This slice ends at prepare/capture/provenance/fingerprint proof with the pinned
SDK and injected fake transport. It does not enable connection/service/desktop,
implement DeepSeek's terminal usage-chunk decoder, rebuild a release artifact,
or establish real API compatibility. Production stays OFF throughout.

## Private factories and destination

Modify `assistant/sdk_provider.py` and add `assistant/_deepseek_admission.py`.
The latter owns application-controlled profile constants and a lazy private
factory; importing it must not import the optional SDK or start a worker.
The SDK module supplies:

```python
def _create_deepseek_provider(config, key, *, sdk_loader,
                              transport_factory, clock=time.monotonic):
    ...
```

Its injected closed frozen `_SDKWireProfile` selects the DeepSeek path. Profile
selection cannot come from a draft/body field, environment bypass or provider
self-report. Configuration must bind exactly to normalized
`https://api.deepseek.com:443/v1/chat/completions` and model `deepseek-flash`.
Reject other hosts, paths (including beta), models and malformed config before
SDK loading or transport creation. Existing config validation is not relaxed.

The captured protocol requires `stream:true`, `max_tokens` equal to the granted
output allocation, and explicit `thinking:{type:"disabled"}`. Final mode uses
`response_format:{type:"json_object"}`, never a remote strict-schema claim.
Standard tool definitions omit remote `strict`; tools remain locally validated
by existing consumers when that later integration is implemented. No undocumented
parallel-tool setting is assumed necessary. Use the real SDK's supported profile
settings rather than removing or changing fields in the captured request.

Fixed JSON instructions and the local answer schema/example are application
scaffolding. Any SDK insertion must be exactly mapped and validated; unexpected
messages, fields, destinations, modes or settings fail closed. Existing body,
header, output, deadline, retry, redirect, TLS, environment/proxy and telemetry
restrictions continue to apply. Secrets, source and raw diagnostics stay out
of errors and logs. No active network transport is used in acceptance tests.

## Complete final-byte attribution

Capture only the SDK's final bounded byte body at the suspended transport seam.
Validate its closed wire shape, exact expected messages/tools/scaffolding and
destination before making a prepared request available. Use deterministic
logical JSON slots and character offsets to derive wire occurrences for source
and permitted metadata. Cover user/system content, tool argument/result/history
slots, duplicate fragments, schema/scaffold insertions and SDK copies.

Substring search can detect an unaccounted duplicate but cannot establish its
origin or grant a source tag. An unsupported transformation or undeclared copy
is a refusal, not a guessed mapping. Validate JSON strings and keys, decoded
escapes, allowed file paths, offsets, duplicate/overlap consistency and both
source and metadata coverage using the existing pure wire-accounting validator
where possible. Do not involve the service or reserve a job's budget in prepare.

Only after complete attribution proof may this private path set
`attribution_complete=True` and supply a frozen `EstimatedInputAccounting`:
`profile_id` bound to an application-owned versioned profile,
`method="wire-bytes-v1"`, `estimated_input_tokens=len(final_body)` and
`body_sha256=sha256(final_body).hexdigest()`. Keep
`input_token_upper_bound=None`; the estimate is not a hidden-framing proof.
The application's dispatch registry is not populated by the adapter itself.

## Frozen SDK handoff

Extend `_fields` to freeze the accounting record's four primitive fields and
each source/metadata occurrence's primitive fields. Never freeze only a reference
to a nested record that can be mutated with `object.__setattr__`.
The handoff retains original request identity, method, exact URL, body bytes
and raw headers. Any changed estimate/hash/profile/method, occurrence, request
object, destination, header or body must be refused before the fake dispatch
transport is entered. Do not reconstruct a semantically equivalent request.

Prepare performs zero underlying sends. Successful handoff is single-use;
cancel before dispatch means zero sends and complete resource cleanup. Reuse
the existing bounded lifecycle rather than adding an independent worker/client.
No real DeepSeek response decoding claim follows from handoff tests using
existing synthetic generic SSE fixtures; actual DeepSeek SSE is the next slice.

## Acceptance and exclusions

Use the pinned SDK in `build/assistant-sdk-probe-20261012` with injected in-memory
transport. Prove common tools/final requests, Unicode/escaped source and metadata,
history/repeated occurrences, correct output/thinking/JSON settings, final-byte
estimate/hash, failure for unmapped copies/transforms, and nested mutation or
request-identity rejection. Keep cases focused, not dozens of unrelated assertions
per test. Existing generic SDK and connection strict tests must still pass.

Base-only imports and invalid-destination rejection must work without optional
SDK loading. In a base environment, actual-SDK checks are skipped, not passed;
their explicit isolated run is required. No installs, downloads, new dependencies,
API credentials, source uploads or provider calls are authorized. One phase
review and full applicable gates occur only at the slice boundary, not per task.
Unchanged frontend/native evidence may be reused with its original limited scope.
