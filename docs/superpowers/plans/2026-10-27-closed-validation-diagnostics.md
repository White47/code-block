# Closed validation diagnostics — approved bounded plan

## Decision and evidence

Approve a default-off, explicit local-startup-only private diagnostic sink.
Stdlib only; no public DTO, validator acceptance, repair, budget or API changes.
This approval permits the implementation below after the main agent closes J.
This planning task changes only this file and runs no tests or live requests.

J4 numbers and existing passing checks are caller-provided evidence, not new
acceptance or proof of a unique live cause. No real payload has been inspected.
`response.py:parse_model_answer` (335–358) collapses parse failures to ResponseError.
`response.py:_sections` (182–185) requires seven ordered sections and <=60 claims.
`ModelClaim.__post_init__` (148–157) checks prose, basis, IDs and relation membership.
`validate_model_answer` (635–696) already returns six closed issue codes/indices;
unknown-marker failure specifically becomes invalid_schema at 682–683.
`service.py:validated_answer` (3112–3126) currently discards issue indices and
collapses parsing failure; final/repair call it separately at 3128/3151.
Existing protocol diagnostics are public and protocol-only (2026-10-23 spec,
lines 18–28); do not extend that contract for these private observations.

## Ownership and activation

- New `src/codeviz/assistant/_validation_diagnostics.py`: exact private collector,
  closed primitive record validator, bounded buffer, safe serializer and sink.
- `response.py`: observational taps in existing parse/validation branches only;
  preserve signatures, predicates, check order, exception/result behavior.
  Use a private scoped ContextVar collector, reset in finally; no global raw state.
- `service.py`: scope the collector inside each existing validated_answer call,
  explicitly label final or repair, and preserve the full original issue indices
  before the existing deduplication for repair codes. Publish safe records only.
- `ui/desktop.py:_create_desktop_bridge`: read local startup environment variable
  `CODEVIZ_CLOSED_VALIDATION_DIAGNOSTICS` once; only exact `1` enables the sink.
  Inject the optional private sink through the existing runtime/service creation.
  Missing/other values remain off. No frontend/settings/bridge/request toggle.
- Use the application's existing local data/cache root (`CacheStore.data_dir`;
  platform default from `persistence/store.py:default_data_dir`, 78–83).
  Fixed basename: `closed-validation-diagnostics.json`; no caller-selected path.

## Closed useful record

At most two attempt records per captured job: phase final|repair and stage
parse|evidence. Stage identifies the furthest authoritative validation boundary,
not a claim that an evidence-stage failure proves a receipt defect.
Codes are only invalid_schema, unprovided_id, invalid_evidence, relation_required,
invalid_relation, static_requires_ids. Empty codes mean no returned issue.
Retain at most eight issue entries with section index null|0..6 and claim index
null|0..19; counts for all six codes and an issuesTruncated boolean preserve scope.
Cap counts at 65537, reject bool-as-int; no arbitrary strings or dynamic keys.

Shape facts are REQUIRED when enabled to meet the diagnostic objective. Use only
fixed booleans/null and saturated counts from checks already performed:
payloadSizeValid, utf8Valid, bomAbsent, jsonValid, duplicateKeysAbsent,
finiteNumbersOnly, rootObject, rootKeysExact, sectionsArray, sectionCount,
sectionOrderValid, totalClaimsWithinLimit, sectionKeysExact, claimsArray,
claimKeysExact, textValid, basisEnumValid, evidenceIdsValid, relationIdValid,
unknownMarkerPresent. Facts not reached are null, never assumed false/true.
One bounded shape-failure location uses the same index bounds; oversized-array
failure is conveyed by count/check facts, never an out-of-range index.
Fixed-schema labels may be recorded; observed key names/enum values may not.
Array facts include the bounds enforced by the existing array check. A successful
JSON decode does not prove all floats finite (exponent overflow may produce an
infinity); finiteNumbersOnly stays null unless the existing constant rejection
hook actually observes a failure. Do not add a new parse/acceptance predicate.
unknownMarkerPresent uses the existing _UNKNOWN_MARKER rule at the actual branch,
not a separate literal phrase test. No extra resolver lookups or verification reads.
unprovided_id distinguishes absent provision from invalid_evidence; the latter
can also mean capacity/binding failure, so never label it uniquely stale/revoked.

## Privacy, lifecycle and noninterference

- Observe existing decoded objects/locals during their current function lifetime;
  do not reparse, reserialize, copy or retain raw/decoded response/model objects.
  Existing parser allocations remain unchanged. Collector accepts only validated
  fixed labels, exact booleans, nulls and bounded integers, never exceptions.
- Never record text, IDs, paths, keys, model/provider values, exception messages,
  repr/tracebacks, hashes, previews or lengths of individual strings. Do not log
  to stdout/stderr or general logging. No fallback raw dump on any failure.
- One enabled process captures its first eligible explanation job only. Claim
  that slot atomically; concurrent/later jobs cannot interleave or overwrite it.
  Hold only a <=4096-byte safe snapshot, at most two records; no job IDs on disk.
- Reset the fixed file to an empty record list on enabled startup, before any
  capture, so old contents cannot masquerade as the next attempt. Disabled
  startup performs no diagnostic file access or collector allocation.
- After the captured job reaches its existing terminal/resource-release boundary,
  offer the snapshot to one private sink worker with capacity one and no wait.
  No disk I/O or worker join on validation/dispatch/freshness/deadline paths.
  Writes replace the single file, never append; enforce <=4096 serialized bytes
  by dropping detail entries with truncation flags, not byte-truncating JSON.
- Use bounded atomic replacement, exclusive temporary creation and cleanup;
  refuse symlink/reparse/nonregular destinations and paths outside app cache.
  No source/project/cache-analysis files are modified. Best-effort I/O failure
  drops diagnostics silently; no retries, new requests, public errors or locks
  that can change validation, accounting, authority or resource-release outcomes.
- Off-thread persistence may be lost on exit; absence is not evidence of success.
  Transport failures before validation produce no invented validation diagnosis.

## Implementation queue and minimum acceptance tests

1. Add closed collector/sink and startup injection, then observational taps and
   service phase wiring. Main owns this ordinary diagnostics work inline.
2. Synthetic parse cases distinguish root/keys, section count/order, claim keys,
   basis enum, text/ID constraints, malformed/duplicate/nonfinite JSON and limits.
   Unknown-marker failure must be evidence-stage invalid_schema at correct indices.
3. Real offline issued-evidence baseline: unprovided vs invalid binding/session,
   all six issue codes, both phases, successful repair and invalid second answer.
   Compare enabled/disabled answers, exceptions, dispatches, ledger and authority.
4. Canary strings in every prohibited field/exception never reach file/buffer;
   reject arbitrary record keys/types. Verify null-for-unreached and truncation.
5. Off means no I/O; exact startup opt-in only; <=4096 bytes, latest capture,
   concurrent-job isolation, path/link rejection, full/unwritable disk and worker
   failure preserve terminal result/resources and produce no retry or raw dump.
6. Run affected response/service/issue/bootstrap tests; phase-end gate per AGENTS.
   No new paid/live request is implied by this approval or offline acceptance.

## Review closure details

Evidence-ID syntax validity is recorded true only after strings and uniqueness
have both been checked; an earlier text failure leaves that fact null.
Pathname rechecks alone do not close replacement races. The writer must pin
directories during I/O on Windows, and use rooted directory descriptors with
no-follow traversal and relative atomic replacement on POSIX. Unsupported or
denied native operations drop diagnostics, never relax path checks or AI rules.
The native replacement test showed READ_ATTRIBUTES-only handles do not enforce
the sharing restriction. Request LIST_DIRECTORY plus READ_ATTRIBUTES (0x81),
retain SHARE_READ only, and check attributes on the opened handles.
The debugger's subsequent probe distinguishes successful directory pinning from
the failing pathname replacement: os.replace reopens a pinned parent for WRITE
and receives sharing violation 32. Do not treat that failure as proof that pins
require weaker access/sharing, or change ACLs to make the writer succeed.
The directory-replacement regression must fail against the earlier writer.
Native sharing/rename semantics are checked against Microsoft's
[CreateFileW documentation](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilew);
relative descriptor operations follow the
[Python os documentation](https://docs.python.org/3/library/os.html#paths-relative-to-directory-descriptors).

## K convergence decision: retain file sink, use source-handle rename

Approve the bounded Windows writer correction; retain the original fixed appcache
file channel and all existing collector/format/privacy/lifecycle constraints.
The window-caption alternative is withdrawn and was never written into this plan.
No title/frontend/public-flow change, new channel, dependency or diagnostic API.
Probe source: `build/closed-validation-20261027/windows-rename-probe.py`.
The debugger reports successful same-parent rename and continued swap/WRITE denial;
this architecture task inspected the probe but did not execute it or native tests.

- Pin every ancestor and the appcache directory with 0x81 and SHARE_READ only;
  retain opened-handle attribute/reparse validation through write, rename and
  cleanup. Unsupported/denied operations still drop diagnostics safely.
- Create the temporary source exclusively with CREATE_NEW and the required
  DELETE plus WRITE access. Keep that same source handle open from creation
  through bounded write and rename; never close/reopen it by pathname to rename.
- Replace only the fixed diagnostic basename in that source's same parent using
  NtSetInformationFile, FileRenameInformation=10, RootDirectory=NULL and a fixed
  basename (no separator, traversal or caller input). No relative current-working-
  directory interpretation, absolute destination or cross-directory operation.
- Use FILE_RENAME_INFORMATION's documented layout and byte-counted UTF-16 filename;
  declare ctypes signatures correctly. Require completed native success, including
  I/O status; pending/failure is not a successful write. Never fall back to
  os.replace/path-based rename, broader shares or unpinned directory access.
- Close source/pin handles on every path; clean up only the owned temporary source
  while its parent remains pinned. Preserve fixed-file atomic replacement,
  <=4096 bytes, first-job/two-attempt capture, default-off and nonblocking service.
- Keep all existing FS/canary tests. Add native success for startup reset AND final
  publication, existing-target replacement, swap/WRITE denial while pins are held,
  rename/write failure and handle cleanup; denied swap leaves the canary intact.
  Retain the real directory-swap RED and probe evidence under build; do not replace
  them with mocks or turn an unavailable native check into a pass.
- The evidence-ID early-true fix remains required. The single K review is complete;
  after these fixes, run affected tests and the phase boundary gate per AGENTS,
  reusing unchanged passing evidence. This sink fix needs no second independent
  review and changes no semantic-core invariant or AI acceptance rule.

Native rename structure reference:
[FILE_RENAME_INFORMATION](https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/ntifs/ns-ntifs-_file_rename_information).
