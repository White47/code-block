# Repair failure classification and answer guidance

## Scope and evidence

The repair-input release's single authorized real request reached repair (five
requests; reserved input74777/output7632; source8460/files1/tools7), then failed
AI_EVIDENCE_INVALID. Its response payload was not inspected. The current public
result does not prove a semantic evidence error: SDKAdapterError subclasses
ValueError, and the repair send catches ValueError and replaces it with
ResponseError. The existing diagnostic regression only raises ServiceError.

A separate deterministic contract gap exists: response.validate_model_answer
requires unknown text to contain `unable to confirm`, `无法确认` or `无法核实`,
but final/repair instructions merely say to mark unsupported conclusions unknown.
The schema also does not express exact ordered sections and proof pairing rules.
These are offline code facts, not a claim about the unread live output.

## Main-agent decisions

This is a bounded existing-contract repair, without an architecture, dependency,
source-traceability, public DTO or validation-policy change. The mandatory debugger
route is active for independent reproduction. Implement inline:

1. In the repair ValueError branch, use the existing exact-type
   `_sdk_worker_error` recognizer and re-raise recognized SDK errors. The existing
   outer boundary projects only fixed public code/diagnostic fields. Unrecognized
   ValueError keeps the existing evidence-failure mapping. Do not import optional
   SDK dependencies or expose an exception message/cause.
2. Disclose the existing unknown-claim wording requirement consistently in final,
   preview-final and repair prompts. Keep the existing schema, evidence-ID and
   proof requirements unchanged. Avoid repeating the full schema in prose: the
   initial broad guidance added about900 bytes and made a tight legacy projection
   fixture omit its excerpt before its binding check. Limit the new guidance to
   the proven hidden lexical rule; preserve the fixture and its security coverage.
3. Do not loosen parser/schema/receipt checks, consume unprovided IDs, manufacture
   facts, remove length failures, change output/input/source budgets, add a retry
   or send another request from a consumed live marker.

## Verification and delivery

- Failure-first real worker regression with SDKAdapterError carrying the existing
  terminal_finish/length diagnostic; the old ServiceError case remains passing.
- Extend the existing guidance/provenance regression for the existing unknown
  marker contract; retain pure-preview/body/source checks.
- Run affected modules and direct consumers, then one software boundary gate
  (full Python, Ruff, Windows/Linux mypy); reuse unchanged frontend evidence.
- One independent review at this new bounded release boundary; no re-review of
  unchanged prior diffs. Package with byte parity and offline probes.
- Preserve old28180 and both used invocation markers. Real acceptance remains
  unverified until a new normal process has a user-entered memory-only key and
  a separately authorized single request. Do not extract or migrate old keys.
