# L: service-owned evidence-ID guidance

## Approval, evidence and boundaries

Approve this bounded cross-module change for core_implementer after this plan.
This architecture task writes only this file; no implementation, tests, raw/ID
inspection, user-source reads, API calls, marker reuse or new diagnostic feature.
The caller reports K5 final passed schema/sections/types/unknown-marker checks,
then returned 22 unprovided_id issues and no other issue codes. Repair terminated
with dispatch length at its existing 2000 cap, before a validation record.
This establishes those failure boundaries, not the identity/type of cited IDs.
The debugger's synthetic receipt, below, establishes an empty-excerpt registration
gap only: it does not identify the live cited IDs. 22 counts failing claims, not
unique identifiers; symbol-ID misuse is not an established live cause.

Read evidence: service.py:register_forwarded (2764–2795) registers excerpt IDs
only with retained source_spans, while projection.targets populate trusted_targets.
send (2838–2853) registers root only after admit and verify_root_wire.
tool_loop.py:project_tool_result (213–249/354–368) distinguishes ref.id from
excerpt evidenceId and may replace an excerpt with complete omission.
final/repair and preview_completion (499–554) currently share fixed guidance
but receive no service-owned receipt catalog.
`build/closed-validation-20261027/diagnosis/repro-excerpt-registration.json`
reports nonempty excerpt ID visible/providedCount=1/resolver acceptance, versus
empty excerpt ID visible/providedCount=0/unprovided_id. Existing budget omission
already removes the ID. The receipt is synthetic, with zero external attempts.

Keep 100000 input/8192 output, existing source/file/tool/request limits, C5 wire
accounting, freshness/consent, provenance/ID binding, schema, validator predicates
and one-repair limit. No alias, invented receipt, failed-answer replay or fallback
acceptance. Guidance improves generation; it never authorizes a claim.

## File owners

- core_implementer: service.py catalog construction, candidate/committed separation,
  fit_projections, ordinary gate, final/repair construction and source traceability.
- core_implementer: tool_loop.py empty-excerpt gap projection, common guidance
  serializer and matching pure preview/actual APIs; reuse provenance helpers.
- core_implementer: focused tests/test_assistant_retrieval_budget.py and existing
  tool-loop/response consumer tests. Main owns integration and phase reporting.
- response.py, response_session.py, ToolSession/results/registry, budgets, providers
  and K diagnostics retain their contracts. Root empty-range behavior gets only
  its existing fail-closed regression; this slice does not broaden root admission.

## Empty-excerpt projection correction

Before exposing an excerpt evidenceId, project_tool_result must ensure the selected
projection has nonempty source_spans. Empty text/zero-width disclosed source instead
produces a bounded retrieval_gap with a fixed truthful reason: no source characters
were disclosed and no citable evidence is available. Do not call this a budget
omission unless budget caused it; _omitted_projection currently has budget wording.
The empty gap carries no evidenceId, source, path/anchor, targets or provenance
spans. Do not strip whitespace or treat a nonempty whitespace-only excerpt as empty.
Reuse normal maximum_bytes checks: a gap that cannot fit fails honestly. Fitting,
catalog forecasting, transcript forwarding and register_forwarded consume that same
selected projection. Its gap reaches existing coverage_gaps/incomplete handling;
neither provided nor relation subset grows. Original result/receipt/registry and
tool-attempt charges remain intact. Nonempty excerpt registration is unchanged.

## Catalog authority and lifecycle

1. Service builds an immutable, deterministic catalog from real provided entries
   and their existing receipt/binding provenance, never from model/tool-call text,
   graph/symbol IDs, projection.targets alone, registry inventory or failed_evidence.
   Include every registered retained excerpt ID, with no silent catalog truncation.
2. Pending projection candidates are selected from typed ExcerptToolResult values
   whose exact selected projection still retains source_spans/evidenceId. Complete
   omission or the empty-excerpt gap contributes no ID, including relation IDs.
   Use the same eligibility predicate as registration;
   do not scrape arbitrary strings from projected JSON or bind during fitting.
3. Service freezes the final catalog from actual provided keys plus, optionally,
   its typed root receipt first carried by THIS final request. The root exception
   requires the matching existing RootSourceFragment/root_wire/retrieval binding;
   no arbitrary pending result can use this exception. This supersedes the earlier
   conservative exclusion of a root first sent in final. No extra send is needed.
4. The final message and previews include identical catalog bytes before prepare.
   After admit, verify_root_wire and conflict-safe registration must make every
   catalog entry present in real provided with the intended binding BEFORE either
   send_policy.dispatch or provider.dispatch. Any failure means zero transport;
   reservations are not refunded. Never patch prepared bytes after admission.
5. Preview candidates (root or selected excerpts) confer no authority and mutate
   no provided/registry/bindings, chosen targets, disclosure state or transcript.
   Before final mutation, configured budgets recheck final+worst-unique-repair
   with the frozen catalog; legacy checks final only. Repair reuses that catalog,
   adding no IDs. Final admission must already have committed any root exception.
6. The list denotes provided receipt IDs, not a freshness/proof guarantee. Existing
   ActualEvidenceResolver and validators still adjudicate every claimed ID/target.

## Shared guidance and exact budgeting

- Service passes the immutable catalog explicitly into final, repair and every
  corresponding preview helper. A common serializer emits a clearly labelled JSON
  receiptEvidenceIds list inside the EXISTING final user guidance message text,
  not a new logical top-level field/message. ref.id names a graph/symbol target;
  evidenceIds may use only these receipt IDs, subject to the pre-dispatch commit.
- Alongside it, emit relationEvidenceIds: the subset of these same receipt IDs
  whose existing proof has status=bound and target type RelationKey. A typed root
  exception needs the same proof and dispatch gate. Never emit RelationKey/graph
  target IDs as receipt IDs. Empty subset means relational claims use inference
  and relationEvidenceId=null. Existing resolver/_fact remains authoritative.
- Empty list: use inference/unknown only, evidenceIds=[], relationEvidenceId=null;
  unknown text includes the existing required marker. Do not invent static proof.
- Repair: preserve all seven required sections in order, empty claims permitted;
  at most ONE concise claim in total, at most ONE distinct receipt ID, no evidence prose
  repetition. Request text <=80 UTF-8 bytes and retain unknown-marker requirements.
  If a citation cannot be expressed compactly, produce an honest inference/unknown
  claim without IDs. These are prompt constraints, not new validator acceptance.
  The unchanged output cap is authoritative; compact guidance is no guarantee that
  a real model will comply, and a length-terminated repair stays a failure.
- Before condensed repair, service adds one fixed bounded coverage gap describing
  condensed/incomplete coverage; existing validation then sets incomplete=true.
  Preserve prior gaps and their caps, fail honestly if they cannot be represented.
  Do not ask the model to add incomplete/coverageGaps keys to its closed schema.
- Catalog serialization emits original escaped IDs, never aliases or target IDs.
  Preserve attribution to supporting receipt files for each new ID occurrence
  through existing metadata tags; do not add source text, paths or target details
  to visible guidance. Repeated IDs still consume full wire input/metadata capacity.
- Ordinary gate budgets current + final + worst legal unique repair, with the
  prospective catalog for those future drafts. fit_projections recomputes that
  catalog for EACH candidate/floor/omission and budgets both completion drafts.
  Actual commit uses the identical serializer/eligible set; before final send the
  exact frozen catalog is checked again. Include both ID lists and attribution;
  never use a smaller list for preview or omit pending retained excerpt IDs.
- Repair preview includes the catalog already carried by final and its exact
  repair guidance/repetition policy. Use all six unique codes for the worst prompt;
  never include the failed response. Do not append a second accidental catalog.
- Preserve the legacy budget=None single-final routing policy; its previews still
  include the same new guidance as actual drafts. Configured final+repair planning
  remains enabled. Final strict prepare/admit and frozen-wire checks never weaken.
- A catalog that cannot fit existing limits fails honestly or causes earlier
  retrieval/projection reduction; no cap increase, free metadata or hidden omission.

## Failure-first execution and minimum tests

1. Distinct synthetic symbol/ref and issued receipt IDs: guidance lists only real
   provided receipts; copying a ref ID still fails unprovided_id. No alias repair.
2. Retained excerpt included; completely omitted excerpt excluded; search/relations
   targets alone excluded; candidate preview never registers/binds any evidence.
   Empty text and same-position zero-width fixtures yield an honest non-budget gap,
   no visible ID/path/source or provenance spans, no catalog ID and no registration.
   Preserve original ToolSession result/registry and attempt charge. Nonempty and
   whitespace-only controls remain visible, registered and resolvable. Repeat at
   projection floor/fit pressure; gap-too-large still fails, never leaks the ID.
3. Root admitted earlier and root-first-final both work; failed admit, wire verify,
   registration/conflict or catalog binding check cause zero dispatch. Preview
   never registers; final/repair inventory is identical, including relation subset.
   Empty root fixture keeps existing fail-closed/zero-transport behavior; no new
   successful root case or automatic retry is introduced to accommodate emptiness.
4. Empty catalog yields valid inference/unknown examples without invented proof.
   Both phases retain schema order, enum and unknown-marker expectations.
5. Preview and actual draft bytes/tags match for the same catalog and repair codes;
   fitting accounts for catalog growth, escaping and repeated final/repair input.
   Preserve the previous repair-headroom regression and no-starvation controls.
6. Synthetic compact repair with seven sections and one short claim fits the
   existing 2000-byte SDK output control; overlong/invalid repair still fails with
   no second repair. Test exact original IDs, not shortened fixture aliases.
   Assert condensed repair is incomplete with the fixed gap; ordinary source
   receipts never appear in relation subset or bypass relation-proof validation.
   Empty-excerpt service integration exposes the gap/incomplete without provided
   growth; a synthetic attempt to cite the removed receipt still fails unprovided_id.
7. Retain unprovided/stale/foreign-binding, C5 attribution, frozen-wire, consent and
   budget regressions. Run affected consumers, inspect diff, then AGENTS boundary
   gate; reuse unchanged passing evidence. No new independent review is requested.

## Full-gate consumer fixture correction

The first L full gate ran 1708 tests: six failures, 102 skipped. Independent
debugger reproduction (build/evidence-guidance-20261027/diagnosis/) established
two obsolete fixture premises, not a production relaxation:

- RootWireProvider assumed the last message was root source; final guidance now
  follows it. Main may read the root header via its sourceTags pointer instead,
  asserting one root header and the same exact receipt ID in all final/repair
  catalogs. Preserve original validation, retention, revocation and send assertions.
- SDK repair-error doubles lacked preview_final/preview_completion/final and used the old repair
  signature, failing before their intended injection point. Main may delegate
  preview/final to a real ToolLoopTranscript and inject only the intended repair
  failure, checking that repair receives the final catalog unchanged.

Only tests/test_assistant_service_response.py and
tests/test_assistant_sdk_service_errors.py are additionally affected. No test or
assertion is removed, no source/validator/budget contract changes, and no new
independent review of unchanged production source. Run these fixtures and their
retention consumer, then the corrected-snapshot boundary gate. Preserve the first
failed receipt and reuse unchanged SDK/frontend/source-review evidence.
