# On-demand ordinary symbol citation navigation

Process follows AGENTS.md autonomous mode. Architecture review:
build/lazy-citation-navigation-20261028/architecture.md, GO.

## Goal and explicit contract change

A verified ordinary function/method/class citation may plan its local Symbol
view without requiring the user to have already opened that view. This extends
C6 target eligibility: a graph target is either an existing trusted delivered
location or the narrowly proven Symbol-root plan below. The plan is not a
delivered-location proof. C3 delivery semantics and public DTOs stay unchanged.

## Exact fallback

Prefer the existing delivered location. Otherwise require all of:
- Bound EntityRef namespace `symbol`, present NavigationFact, and exactly empty
  visible_locations. Missing facts or nonempty untrusted locations fail closed.
- Immutable snapshot definition of kind Function/Method/Class and its same-ID
  CodeNode have equal id, kind, name, qualified_name, scope, file_path and exactly
  one equal source anchor, following controller._same_indexed_definition.
- Existing source freshness, retained receipt/provenance, publication/session/
  config/consent checks, cancellation and lease lifetime remain authoritative.

Return only RevealTarget(Symbol, symbol.id, symbol.id). Symbol code-context
construction starts candidates with its focus, so a successful ordinary view
includes that root. This does not guarantee construction/delivery success.
No mutable graph access, view creation, model-name lookup, context_map mutation,
new receipt, worker, dependency, public API, model request or retry is introduced.
The frontend subsequently uses its existing normal get_view delivery path; only
that successful path may publish real locations. Publication changes may make
the old navigation record stale under the existing policy.

Semantic/code namespaces, relations, variables, external/unresolved identities,
aggregation and detail-only contexts retain their existing behavior. This is
not proof of the historical N native failure's cause.

## Task 1 — core implementation and focused evidence

Owner: core_implementer. Product file: src/codeviz/assistant/service.py, narrowly
around _navigation_reveal and a pure immutable-record identity guard. Do not
import the UI controller from the assistant service or change the graph builder.

Tests: tests/test_assistant_runtime_navigation.py and/or
tests/test_assistant_native_symbol_navigation.py. First demonstrate RED for a
real owned synthetic definition with no delivered view, then GREEN. Verify the
planned root and unchanged publication map, materialize it through normal
get_view and prove the selected ordinary node is present. Assert no additional
provider sends. Add only discriminating rejection controls for eligibility and
identity; preserve all existing grouped/detail/refusal/cancellation assertions,
especially runtime-navigation test at line 209. Do not loosen tests to pass.

Run only these navigation modules plus their navigation/response consumers
during this task. Record exact pass/skip results; no full gate or native GUI run.

## Task 2 — UI guidance and delivery

Owner: primary. Update aiGraphCandidate in frontend/src/app/i18n.ts and the
current navigation section in docs/CODE_AGENT_GUIDE.md. Explain restricted
ordinary definitions can open their Symbol view on demand; other candidates
still require trustworthy locations. Keep the shared verification error neutral.
Record this decision in docs/adr/2026-10-28-on-demand-symbol-citations.md.

At the boundary: inspect only the slice against saved local baselines, one
independent review for source traceability, then applicable full unittest,
Ruff, Windows/Linux mypy and frontend suite/lint/typecheck/build once after
findings. Reuse unchanged passing evidence; do not repeat native automation
commands whose environment/driver limitation remains unresolved. Build a new
isolated wheel/frozen package and verify bytes/TLS and fake-only SDK probes.
Append at most five progress lines at the boundary. No commit/push or real API.

## Acceptance

First click may plan the exact ordinary Symbol root on verified local evidence;
no fake delivered map, guessed identity or source upload results. Existing bad
locations still reject. Source viewing remains available under its original
checks. Package/native/manual/real-provider evidence is reported separately;
new real requests require new specific disclosure/payment consent.
