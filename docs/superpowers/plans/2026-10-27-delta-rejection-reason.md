# N: first delta-rejection reason — approved minimal plan

## Decision and evidence

Approve B: SDK-private first rejection -> service -> existing default-off K sink.
A would extend ProtocolDiagnostic, public DTO/whitelists, frontend types,
translations and consumers. B reuses exact SDK-error recognition and K's existing
first-job, post-release publication; its sole new collected fact is a fixed reason.
No implementation/live acceptance is claimed by this architecture-only task.

Read: build/static-claim-guidance-20261027/live/acceptance-summary.json and
diagnosis/delta-shape-findings.md. M3 failed on ordinary dispatch 3/delta_shape;
no final/architecture, no validation records. Its paid marker is consumed.
Debugger's synthetic legal fragmentation passes; null tool type, bad argument
JSON and unknown tool name fail at this same site. The live predicate is unknown.
No compatibility patch, relaxed predicate or new request follows from this plan.

Concrete source evidence:
- sdk_provider.py:BoundedStream.frame, 1280–1335: compound delta guards and
  terminal tool assembly share delta_shape; terminal_finish/EOF have other sites.
- SDKAdapterError/_error/_reject and dispatch, 72–105/362–371/1102–1117:
  sanitization and fresh raises can lose a new private field unless propagated.
- BoundedStream.__aiter__/aclose, 1408–1422: cleanup can replace a stream failure.
- service.py:_sdk_worker_error, 217–231: only the loaded SDK's exact error type is
  trusted. _release_terminal_job, 2462–2479, waits for provider release and B work.
- _validation_diagnostics.py:encode_records/Capture, 109–176: currently only
  final/repair validation records. DiagnosticsSink, 383–435, already caps delivery
  to the first job, two records/4096 bytes, one nonblocking queue and safe file sink.

## Ownership and smallest change surface

Main owns ordinary diagnostic implementation inline per AGENTS; architecture is
approved here. Mandatory specialist routing still applies if implementation scope
changes; source traceability, semantic core and tool authority are not changed.
- New assistant/_delta_reasons.py: stdlib-only closed enum, exact validator and
  immutable private rejection value with fixed dispatch/delta_shape provenance.
- assistant/sdk_provider.py: first-failing-guard annotation, private SDKAdapterError
  value and propagation through sanitation, fresh raises and cleanup replacement.
- assistant/service.py: capture trusted private value at the failing send boundary,
  using its service-owned phase; public error mapping and _sdk_worker_error's
  existing public tuple remain compatible. No eager optional-SDK import.
- assistant/_validation_diagnostics.py: one strict private transport-record variant
  and first-transport latch. Existing K startup/writer/native safety stay intact.
- Tests: existing SDK stream, service/connection, issues and K diagnostic suites;
  use the existing pinned actual-SDK offline fixtures for propagation evidence.
Do not modify issues.py/public DTOs/frontend/translations/provider event schemas.

## Fixed reason enum and classification

Only these exact ASCII labels; no values, field names, IDs or numeric counts:
delta_object, delta_fields, delta_refusal, delta_role, delta_reasoning;
content_type, content_string_size, content_encoding, content_total_size;
tool_mode, tool_list, tool_count, tool_item_object, tool_item_fields,
tool_index, tool_type, tool_function_object, tool_function_fields;
fragment_type, fragment_string_size, fragment_encoding, fragment_joined_size;
terminal_duplicate_id, terminal_missing_id, terminal_unknown_name;
arguments_json, arguments_object, tool_payload_size.

Split existing short-circuit compound guards only enough to identify their first
failure, in EXACT current evaluation order. Preserve mutations, charge() calls,
exception category, limits, supported profiles and checks before/after each guard.
In particular terminal duplicate-ID check precedes missing-ID then name membership.
content/fragment checks preserve type -> existing string-length -> UTF-8 encoding;
fragment joined-size remains after charge and concatenation. Aggregate content
bytes and tool payload bytes retain separate reasons and unchanged original caps.
arguments_json covers failure of the existing strict parser, including its bounds;
arguments_object means that parser SUCCEEDED but returned a non-dict. Never parse
again to classify, inspect exception messages, or label arbitrary exceptions JSON.
Missing finish/DONE remains terminal_finish/stream_eof behavior, not missing-ID.
Missing role/type/function and null fragments retain their existing distinct
acceptance semantics; truthy refusal rejection is not changed into a type rule.
Set a reason only on actual failure, not before a successful check. Timeout,
cancellation, budget/charge failures, SDK decode or unexpected errors gain none.
Unexpected failure inside a helper is not automatically that helper's reason.

## Trusted propagation, record and lifecycle

SDKAdapterError carries only the immutable private closed value, never the
original exception/arguments. Copy the already-captured FIRST value through all
owned rethrows. If cleanup supersedes the public issue/site, keep that public
precedence unchanged and retain the private value's original delta provenance;
it describes the earlier guard, not the cleanup failure. Pure cleanup failure
cannot create it. No exception/context/body retained to recover it retrospectively.
An owned per-send latch may hold that single immutable value so SDK masking or
cleanup cannot lose the first guard reason. It retains no exception, arguments or
provider value, adds no persistence channel, and supplies no new public authority.
Service accepts only the exact loaded SDK error type and exact private value,
validates the enum/provenance again, and never reads duck-typed properties.

Private record exact shape: {"phase":"ordinary|final|repair",
"stage":"transport","reason":"<one enum label>"}. The phase value is one
literal, supplied by send(), not provider metadata. dispatch/delta_shape provenance
is checked internally; omit redundant fields and counts from the record.
Retain K validation-record format unchanged; only this discriminated union branch
allows ordinary/transport. Unknown fields/types/reasons reject without coercion.
One first transport record per captured job, no per-delta/per-ordinary records.
Share the existing TOTAL two-record/4096-byte cap; do not allocate two extra slots.
Final validation plus repair transport fits two slots; a full capture drops extra
records without replacing earlier evidence. Mixed-record size reduction must touch
only validation issue details, never fabricate issues/shape fields for transport.

On failure, store only the safe private value in the claimed capture in memory.
Persist only after existing terminal/resources_released checks, including provider
stream/client closure and B-worker release; reuse _retire_job_locked publication.
If close remains blocked, hold the same tiny capture until normal cleanup completes;
never mark release, publish early, retry dispatch or skip cleanup to obtain a log.
No callback/disk I/O/wait/join on dispatch or error paths. K's one worker performs
the existing bounded safe write; sink errors never replace the application error.
Default-off/exact local startup flag, first explanation job, [] reset, native path
protections, cancellation and shutdown ownership stay unchanged. No connection
capture, new switch/file/channel/stdout, raw copies, IDs/names/args/keys/text,
provider exceptions, hashes or user-source inspection. Existing K facts only.

## Failure-first tests and completion boundary

1. Offline stream matrix proves every reason at its existing guard and first-fail
   precedence for compound cases; accepted legal DeepSeek fragments still pass.
   Test omitted vs null fields, argument malformed JSON vs valid non-object,
   duplicate vs missing ID, unknown advertised name and exact size boundaries.
2. Pinned actual-SDK tests prove private propagation through _error, stream worker,
   queue, fresh _reject and cleanup overrides; successful cleanup and failed cleanup
   retain first reason without changing public precedence or resource closure.
3. Real service offline failure on ordinary 3 emits ONE transport record after
   release; final-validation/repair-transport uses two slots. Defaults produce no
   diagnostic I/O. No retries/final/repair are invented after a rejected ordinary.
4. Reject forged/subclass errors, dynamic properties, nonexact primitive labels,
   wrong provenance and extra fields. Canary secrets in rejected inputs/exceptions
   never reach records/public DTOs/logs; unknown failures have no guessed reason.
5. Compare enabled/disabled issues, dispatches, tools, ledger/unknown usage, C5,
   deadlines/cancellation and cleanup; preserve K writer/no-source-write tests.
   Assert public issue/poll/connection/frontend contracts are byte/shape unchanged.
6. Run affected-module consumers, inspect diff, then AGENTS phase boundary gate
   once with existing passing evidence reused. Report environment skips honestly.
   This plan authorizes no API/payment/key/raw/marker operation or source upload;
   M3's consumed permission is not reusable. Keep all validation/tool/budget caps.
