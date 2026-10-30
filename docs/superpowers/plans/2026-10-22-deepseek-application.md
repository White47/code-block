# DeepSeek application assembly and offline delivery plan

Process: AGENTS.md. Prerequisite: 2026-10-22-deepseek-connection.md Task4 passed.
Spec: ../specs/2026-10-22-deepseek-application-design.md. Architecture: Goodall,
read-only, completed. No new dependencies, live API, commit or push.

## Task 1 — Lazy trusted application assembly (done)

Owner: core_implementer for cross-module bindings. Files: _deepseek_admission.py,
sdk_provider.py, ui/desktop.py, new test_assistant_production_assembly.py.

- Failure-first startup/import/registration test, then private app constructor,
  dual frozen registries, closed production factory and extracted SDK helpers.
- Preserve unbound bare runtime, generic refusal and transport TLS/no-proxy/
  no-redirect/no-retry behavior. No SDK loading until active provider creation.
- Verify exact-target refusal before loading SDK; disabled/no-key startup makes
  no request; connection and explanation use the same admitted factory.
- Run new tests and directly affected SDK/runtime/desktop consumers only.

Acceptance: correct immutable trust propagation and production route, lazy/offline
startup, no widening of user consent or budget contracts. No packaging yet.

Task1 receipt (2026-10-23): core implemented only admission/SDK/desktop and new
production-assembly tests. Missing entry points produced four expected RED
errors; new tests then passed 4/4, base direct consumers 23/23, runtime 6/6 with
explicit src/tests PYTHONPATH and pinned SDK consumers 5/5. No full gates or
review were repeated. The earlier quota/role interruption is resolved; production
assembly now exists, but Task2 actual-SDK explanation and packaged/native/live
acceptance remain outstanding. Disabled/no-key startup still sends zero requests.

## Task 2 — Offline production path (done)

Owner: primary for scripts, core_implementer for any required invariant fix.
Files: scripts/verify_assistant_production.py, the directly reused
scripts/verify_assistant_dependencies.py offline guard, and focused tests/fixtures.
The production probe also requires the architect-reviewed root evidence mapping
correction in response_session.py plus new test_assistant_response_session.py,
owned by core_implementer; see the spec's 2026-10-23 correction, not a budget
or provenance relaxation. No service/SDK/public DTO edits are required for it.

- Use offline_guard before SDK load, existing pinned interpreter and only a fake
  secure transport replacement; no provider-adapter substitution.
- Exercise source-free connection and owned synthetic seven-section explanation,
  local citations, request counts, cancellation and resource release.
- Emit safe machine-readable receipt with artifact identity; no body/key dumps.
- Windows socketpair uses a loopback TCP pair to create asyncio's self-pipe.
  Keep all general connect/DNS/UDP denials; allow only a guard-owned socketpair,
  directly connecting its own fresh loopback listener with authenticated peers.
  Never temporarily restore global connect methods or allow arbitrary loopback.
  Regression first proves owned pair works while an unrelated connect is denied.
- Run new verifier and script/direct consumers; no full suite between tasks.

Acceptance: actual app factory -> SDK original captured requests -> fake SSE ->
service/capability/answer, zero external communication. Not real API acceptance.

Task2 receipt (2026-10-23): new verifier tests first failed for the missing script;
Windows socketpair regression then failed under the original shared guard and
passed after the owned/authenticated-pair correction. General connects remain
denied. Failure receipts use only public issue.code, not dependency messages.

The real SDK exposed a genuine root-evidence index mismatch. Lorentz isolated
AI_EVIDENCE_INVALID after three sends; Ampere reviewed the actual-occurrence
intersection design. Core changed only response_session.py and its new tests:
shifted final RED then 6/6 passed, service/runtime consumers16 and SDK attribution1
passed. Exact canonical header/content, full spans/path, inert role, unique
source/metadata intersection and strict bounded JSON remain required.

The next protocol failure was investigated rather than bypassed: fixture JSON
exceeded the established 512-byte final allocation. Compact JSON and shorter
static claims fixed it; unknowns retain the required 'Unable to confirm' marker.
A focused regression first measured 579 bytes, then proved the representative
long-ID fixture fits the same 512 allocation. The helper also self-checks actual
wire allocation. No core budget or final-event contract was relaxed. The earlier
provisional-text hypothesis was disproved by the real trace.

Final pinned SDK verifier tests: 5/5 passed. Base: 4 passed/1 SDK-dependent skip;
assembly tests4 passed. Source standalone command (with explicit src PYTHONPATH):
`build/assistant-sdk-probe-20261012/Scripts/python.exe -B
scripts/verify_assistant_production.py --fake-only` exited0. Receipt:
build/assistant-production-source-20261023.json; 5 original SDK handoffs,
externalAttempts0, seven locally validated sections, retained source navigation,
cancelled stream and all resources released. assemblySha256 hashes only the six
explicit assemblyFiles in the receipt, not an entire application artifact.
Ruff import-format fixes were mechanical; no existing assertion was removed.

Known hardening issue to check before final delivery: SDKAdapterError budget
failures can surface from service._work as AI_PROTOCOL_INCOMPATIBLE rather than
the underlying AI_BUDGET_EXCEEDED. Lorentz observed this at the fixture overrun;
do not treat it as a reason to enlarge budgets. Verify the admitted SDK error
classification with focused offline cases during Task3/4 and correct the boundary
if needed, preserving unknown/sticky accounting and safe fixed public errors.

Resume at Task3, not at the already-passing source probe. The desktop and SDK
package caches already exist; task-local PYTHONPATH can expose the existing SDK
site-packages to the existing desktop packager without an install/download.
Read-only preflight found PyInstaller6.22.3, pywebview6.2.1, setuptools84.0.0,
slim2.55.0, openai3.29.0, httpx2/httpcore2 2.13.1, pydantic2.14.1, typing4.16.0.
Caches: build/assistant-desktop-wheelhouse and
build/assistant-provenance-20261012/312-win_amd64. No new wheel/exe/native result
exists yet. Phase-wide full tests/mypy and the single new-phase independent
review remain Task4 work; the old connection gates do not cover this new diff.

## Task 3 — Current source/wheel/exe offline delivery

Owner: primary for packaging/docs/harness; core_implementer for semantic fixes.
Files: scripts/assistant_smoke_host.py, verify_assistant_native.py, codeviz.spec,
focused native/packaging tests, guide/dependency/release documentation as needed.

- Add fixed production-DeepSeek fake-HTTP smoke alongside existing fake provider;
  preserve private frozen entry, owned fixture and loopback-only browser control.
- Build new wheel offline, verify base import and isolated installed [ai] using
  existing hash cache. Build exe with narrow SDK metadata/import collection and
  current validated UI, without npm ci or downloads. Do not destroy old receipts.
- Run source/wheel/exe fake-only native verifier where environment permits;
  record exact identities and checks, real GUI exit/cleanup, failures/skips.
- Synchronize user guide/security/cache/limitations with real production state;
  key entry belongs in app. Linux and live API remain explicit evidence items.

Acceptance: current artifacts, honest optional/base behavior, real native smoke
only if actually executed. Do not mark environment-blocked checks as passed.

## Task 4 — Final Task10/E evidence boundary

Owner: primary. One independent review of this new slice, affected then full
Python/Ruff/default+Linux mypy; frontend gates only if snapshot changed. Fix
findings then applicable final gates, without routine second review. Respect
120s quiet-run and unchanged-command retry limits. Append <=5 progress lines.

Report all offline/native/Linux/API outcomes separately. If human-only evidence
remains after independent work, stop with exact item/action and resumable next
step; do not infer E complete. No API key printed/persisted, no paid call or
source upload without explicit scope-specific consent, no commit/push unless asked.
