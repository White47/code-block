# Project understanding hierarchy recovery

Execution follows AGENTS.md autonomous mode, inline UI work, affected tests and
one phase-boundary review. No commit/push, provider request or source upload.

Goal: preserve distinct ProjectStructure and Architecture graphs and their real
edges/hierarchy while displaying complete available AI explanation in the same
primary editor. Architecture GO: build/project-understanding-hierarchy-20261029/architecture.md.
Existing React/React Flow, static Graph and EffectiveUnderstanding are sufficient;
no backend, DTO, cache, dependency or provenance changes are required.

## Task H1 — behavioral regression and primary graph composition

Files: frontend/src/app/App.tsx, ProjectUnderstandingIntegration.test.tsx.
First change the existing integration fixture to return different static graphs
for the two views; assert both graphs, real edges and toolbars remain visible
while AI is enabled. Assert toggling the explanatory layer does not query or
replace the same static graph. Keep source navigation, cross-chat typed Ask and
original-artifact retention assertions. Static node Enter/detail remains valid
because it now operates on real static nodes in the main graph.

Remove the enhanced-state get_view short circuit and its effect dependency.
Retain view/root/depth/revision request identity and existing late-response cleanup.
Use the existing GraphEditor branch for both modes. Mount the AI panel separately
inside the primary editor; preserve inspector, static source guards and camera.
Remove AI position Map and standalone-canvas import/wiring. Run this integration
file and direct App/keyboard consumers with the installed Node22 Vitest.

## Task H2 — all sections and honest coverage

Files: frontend/src/app/components/ProjectUnderstanding.tsx, i18n.ts, styles.css;
migrate the two ProjectUnderstandingCanvas tests into explanation-panel tests.
Add a collapsible primary AI panel around the existing seven-section summary.
Every available claim retains its itemId, evidence ownership and explicit Ask;
verified relation facts remain a bounded text/evidence list, never invented edges.
Explain representative_sample, retrieval_gap and unverified from original codes.
Keep incomplete true and raw gap text accessible; show an honest unspecified-cause
fallback when no concrete reason exists. Panel height is bounded and the graph
retains operating space at desktop minimum size.

Retire ProjectUnderstandingCanvas.tsx and projectUnderstandingGraph. Their old
tests asserted the intentionally replaced standalone renderer/AI positions; migrate
evidence ownership, source-only rejection, no automatic Ask, no fake edges and
visibility coverage to the primary panel/App/real-browser composition tests.
Do not keep unused production code solely to retain implementation-specific tests.
Use failure-first panel tests and only affected/direct-consumer tests while editing.

## Task H3 — actual rendering and boundary delivery

Use real production assets with a local synthetic bridge and saved static graph
metadata (no source bodies). Verify distinct view node/edge identities, visible
cards and real paths, top-down hierarchy, expansion controls, static/AI toggle,
panel collapse, item evidence/Ask and incomplete reasons. At 1000x640 and 1800x1000,
fit must show the graph without the explanation panel consuming the whole canvas.
No real model request is needed to prove this UI behavior.

Inspect the owned delta against before/, one independent phase review, frontend
suite/lint/typecheck/build, then the AGENTS boundary backend suite/Ruff/Windows and
Linux mypy once in the normal Windows permission context. Unchanged backend
evidence may be reused only for the identical snapshot, with explicit attribution.
Create a new isolated wheel/frozen package, verify source/assets/TLS and fake-only
probes. Update delivery guide and append at most five progress lines at the boundary.
Final report must distinguish local graph coverage, AI sampled coverage, native
rendering evidence and unperformed live-model/API acceptance.
