# Calls view completion

Execution follows AGENTS.md autonomous mode. Architecture GO and exact technical
contract: build/calls-view-20261029/architecture.md. The user requested repair;
the architect's read-only scope does not require another user approval.

Problem: Calls is already exposed in the PC UI/enum but build_visible_graph has
no Calls dispatcher. A two-file static fixture already has six CALLS yet the
bridge returns INVALID_REQUEST. It is not an instruction to change user operation.

## Task V1 — real source-backed projection (core_implementer)

Owned Python files: views/calls.py (new), views/builder.py, views/layout.py,
ui/controller.py and ui/presenter.py; focused Python tests and the obsolete
Calls-unsupported assertion in tests/test_views.py. Preserve all invalid root,
budget and stale/navigation coverage; unsupported Gradient remains out of scope.
Do not modify parser/resolver/IR, Stable ID, source_handles, DTOs or dependencies.

Keep real caller scope, CallSite and target IDs. Verify scope_symbol_id and actual
CONTAINS ownership; keep the real CallSite→target CALLS, confidence and call-site
anchor. No caller→callee invented CALLS or guessed dynamic targets; CodeEdge has
no ID, so do not invent code_edge_ids. Bound overview/focus/hops/direction and
recursion; 100/300 primary and18/40 details, bounded aggregates <=20 member IDs.
Empty success is distinct from invalid explicit identity. Controller detail,
Inspector, saved-state validation and real C3 delivery must work for Calls.
Failure-first tests, affected/direct consumers, scoped Ruff/mypy only in task.

## Task V2 — usable PC entry (primary)

Files: frontend/src/app/App.tsx, components/QueryToolbar.tsx, i18n.ts;
CallsView.test.tsx and direct consumers. No frontend state schema change.
Sidebar Calls opens an overview with no incidental selection or old Calls root;
explicit Search/C3 reveal retains its validated requested root/selection.
Selecting a node only selects/inspects; Focus selected call explicitly sets root,
and Call overview clears root/selected. Focused direction/hops use root as the
query center; stale selection must not shift it. Disable focus for aggregation
or stale graph. Show source-backed caller→CallSite→target explanation and a
specific successful-empty notice. Native failure remains an error, never empty.
Retain source editing guards, Inspector, detail/source actions and independent AI.

## Task V3 — boundary and desktop delivery (primary)

Boundary test correction: the first full suite found one obsolete assertion in
tests/test_structure_exploration.py requiring Calls to remain unsupported.
That premise contradicts this approved feature. Preserve its existing control
mode assertions, add focused Calls to the supported relations cases, and use
still-unsupported Gradient for the negative check. No coverage is removed.

The single phase review found budget ordering could disconnect a focused root:
choose records in BFS order and require their selected predecessor path before
admitting deeper calls. This changes projection selection only; original call
facts, source authority and Parser/Resolver/IR remain unchanged. Also preserve
selection when clicking the already-active direction, and replace the UI's
invented caller-to-callee stub with captured native overview/focused/upstream
fixtures and a CallSite-to-source regression. Close with gates; no second review.

Inspect owned deltas and one independent phase code review. Re-run backend full
unittest/Ruff/Windows+Linux mypy for changed Python; final frontend suite/lint/
typecheck/build once. Real production-browser scene checks overview, focus,
upstream/downstream/hops, return, empty state, call-site source and detail;
reuse static actual bridge payloads, not fake relation semantics. Build a new
isolated package, verify source/assets/TLS and fake-only SDK/frozen probes.
Record skipped/todo and unperformed native/live API separately. Update delivery,
guide and at most five progress lines only at phase boundary; no commit/push,
API/source upload, key migration or analyzed-project execution.
