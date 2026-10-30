# Code Agent C1 Dependency Remediation Plan Addendum

> **For agentic workers:** Use subagent-driven-development or executing-plans for later execution, following repository model routing. This turn is architect decision and documentation only. No implementation, dependency installation/download, upstream contact, commit or push is performed.
>
> **Process amendment (2026-10-15):** process is governed by the repository `AGENTS.md`. Do not load `subagent-driven-development` or `executing-plans`; do not run a per-task spec/quality review pair. Technical requirements below are unchanged.

**Goal:** Remove the remaining new UI notice blocker with one independently authored internal compatibility module while retaining the approved assistant-ui API, React 19, desktop runtime and security contracts.

**Architecture:** Replace only the transitive `use-composed-ref` resolution with a private local package, subject to isolated resolver, lifecycle, closure and native acceptance. Keep SDK, native-interface notices and UI gates separately evidenced; overall production acceptance remains conjunctive.

**Tech Stack:** Existing React/React DOM 19.1.1, TypeScript 5.9.2, Vite 7.3.6, Vitest and pywebview/WebView2; assistant-ui/react 0.15.28; existing Python SDK pins. No new framework, provider, runtime service or product feature.

## Decision, authority and current status

Architect disposition: **approve the bounded local compatibility route for subsequent implementation and verification**. It is a disclosed substitution of a small transitive utility, within normal remediation of the already approved component, rather than a material replacement of assistant-ui or its runtime. Its maintainability cost is one owned ref hook and its tests. This decision is conditional on preserving the exact consumed interface and all acceptance criteria below; it is not a compatibility PASS or a legal opinion.

The user authorized continuous execution and self-correction; the main agent proposed the local replacement route and delegated architecture review. The reviewed technical route uses an independently authored internal module marked `private` / `UNLICENSED`; this marking establishes neither project ownership nor distribution rights. The repository has no root LICENSE and no identified project copyright/license declaration; existing third-party notices confer no project-wide license. Do not create such a declaration, infer a rights holder, label the new module MIT, or relicense the repository. This plan does not adjudicate ownership or independently grant distribution rights.

Read in full: [dependency report](../../CODE_AGENT_DEPENDENCIES.md) and [approved runtime plan](2026-10-12-code-agent-runtime.md). Supporting evidence: [notice audit](../../third-party-notices/assistant-dependencies/README.md), its Python/native/npm manifests and the main agent's supplemental delegated research. Earlier FAIL/SKIP records remain historical evidence; they are not silently rewritten by this decision. Only this addendum is changed in this task.

Task classification is architecture-sensitive dependency remediation. The previously delegated architect session could not itself call further agents; this was that sub-session's limitation, not a lack of independent reviewer tools for the main agent. Main-agent independent architecture review approves the bounded addendum after these factual corrections; execution and production reviews remain separate. Later hook/package work belongs to `implementer`, lifecycle fixtures to `test_writer`, cross-subsystem packaging/gate changes to `core_implementer`, unclear failures to `debugger`, and independent clearance to `reviewer`. Size or notice ambiguity alone does not warrant `final_auditor`.

## Evidence and notice disposition

Separate documentary facts, supplied research, engineering acceptance and legal conclusions:

| Item | Evidence and bounded disposition | Remaining acceptance |
| --- | --- | --- |
| SDK runtime | Existing audit PASS: exact 26-package closure, 41 distinct wheel hashes, four offline resolver targets and original runtime notices; SDK probes are distinct from the environment's 41 installed distributions | Independent acceptance/reproduction of applicable SDK gates; no UI/native notice dependency added to this closure |
| proxy_tools 0.1.0 | MIT metadata conflicts with BSD header and immutable official LICENSE matching the installed source. Retain actual BSD terms and original attributions; record the metadata disagreement | Review notice packaging and provenance; do not manufacture MIT text or claim the metadata conflict disappeared |
| react-remove-scroll-bar 2.3.8 | Main-agent delegated research reports 24 compiled files identical to 2.3.7 and a later official LICENSE-only addition. Retain the original later MIT text with an explicit inference/provenance note | Preserve comparison file inventory/hashes and immutable history; reviewer may accept this bounded notice disposition, without claiming the license was in the 2.3.8 release |
| use-composed-ref 1.4.0 | Release and release-source archives have no primary permission/copyright text; metadata says MIT. No verified React 19 alternative version is established | Remove original code from the selected shipped closure using independently authored internal code; metadata alone does not close this blocker |

For proxy_tools use the [immutable official LICENSE at db43f1e35d4f90a65c5a4d56d9e9af88212ec6e6](https://raw.githubusercontent.com/jtushman/proxy_tools/db43f1e35d4f90a65c5a4d56d9e9af88212ec6e6/LICENSE.txt) and the [already recorded Git blob](https://api.github.com/repos/jtushman/proxy_tools/git/blobs/078411c7399fbc0454f36cbe8c8cdbaaba7ebc95). Preserve all wording, both actual copyright holders and the source-header attribution. Werkzeug ancestry may justify an additional older BSD notice only when its exact original text and ancestry are traced; it supplements, never replaces, the proxy_tools notice. No new author/year is invented. This is a conservative notice-retention decision, not a determination of which conflicting declaration legally controls.

For react-remove-scroll-bar preserve the [2.3.8 release metadata](https://registry.npmjs.org/react-remove-scroll-bar/2.3.8), [2.3.7 release metadata](https://registry.npmjs.org/react-remove-scroll-bar/2.3.7), [official LICENSE-only commit](https://github.com/theKashey/react-remove-scroll-bar/commit/8ca9ba5ea52de03308fe8ced94f7b159a44d28ff) and [immutable later LICENSE](https://raw.githubusercontent.com/theKashey/react-remove-scroll-bar/8ca9ba5ea52de03308fe8ced94f7b159a44d28ff/LICENSE). The viewed commit changes only LICENSE. The 24-file equality and history relationship are supplied research, not a comparison re-executed in this turn. Treat application of the later permission to identical earlier compiled content as an inference requiring recorded review; do not assert retroactive legal certainty, an exact release license, or complete source-tree equality. Do not downgrade the installed package to 2.3.7 on this evidence.

For use-composed-ref retain [release metadata](https://registry.npmjs.org/use-composed-ref/1.4.0) and the recorded `ea2c9d969f2f90a349f4c99338b806423824cea2` source provenance in the historical audit. Neither a missing standalone notice nor this internal policy establishes that upstream code is legally unusable; our safety disposition is to avoid shipping it without sufficient primary attribution. No contacting authors, filing issues, publishing a package or making external coordination commitments is authorized here.

## Minimal local package and resolution contract

Future local files, created only in the execution task:

| File | Responsibility |
| --- | --- |
| `frontend/packages/use-composed-ref-compat/package.json` | Local identity, private/license fields, exports, React peer |
| `frontend/packages/use-composed-ref-compat/index.cjs` | Single independently authored hook implementation using the existing React peer |
| `frontend/packages/use-composed-ref-compat/index.mjs` | Thin authored ESM export wrapper over that implementation |
| `frontend/packages/use-composed-ref-compat/index.d.ts` | Authored generic ref/cleanup contract for existing React types |
| `frontend/packages/use-composed-ref-compat/PROVENANCE.md` | Internal status, independent authorship process and exact revision/file hashes; no rights-holder invention |
| `frontend/src/app/assistant/use-composed-ref-compat.test.tsx` | Hook lifecycle and actual licensed-consumer integration fixtures |
| `frontend/package.json`, `frontend/package-lock.json` | Verified root dependency replacement and reproducible resolution |
| `frontend/scripts/verify-assistant-dependencies.mjs` | Existing isolated gate extended for resolution/closure/lifecycle evidence |
| `docs/CODE_AGENT_DEPENDENCIES.md`, `docs/third-party-notices/assistant-dependencies/README.md`, `npm-provenance.json`, `interface-provenance.json` in that notice directory | Subsequent evidence/status corrections with historical scope retained |

Production notice destinations remain those selected by the approved runtime packaging task. Keep internal provenance separate from upstream third-party notices; ship required upstream text with distributed assets. Do not add a fabricated LICENSE to the internal package merely to satisfy a filename check.

Preferred candidate manifest: package name `use-composed-ref` solely for the consumed module specifier, distinct internal version `1.4.0-codeviz.1`, `private: true`, `license: "UNLICENSED"`, React peer exactly `19.1.1`, default ESM export and CJS require export, plus `types`. No bundled React copy, registry publication, upstream author/repository impersonation, postinstall downloader or new runtime dependency. An internal README/provenance record must explain that this is independent local code, not upstream 1.4.0 or an upstream patch. Omit author/copyright fields unless their values are actually established.

First resolver candidate in the consuming frontend root: `overrides: { "use-composed-ref": "file:./packages/use-composed-ref-compat" }`. Test against the actual Node 22.23.3 and bundled npm version, recording both. npm documents root overrides, local replacement and restrictions on overriding direct dependencies; documentation does not prove this repository's resolver behavior. See [npm package.json documentation](https://docs.npmjs.com/cli/v11/configuring-npm/package-json/). A same-name local workspace is a bounded fallback only if that npm version reliably links every transitive consumer to it. If a direct local dependency is needed, its spec must agree with the override (or use the documented dependency-reference form); demonstrate this rather than guessing around EOVERRIDE or invalid-comparator failures.

Test override first, then at most the local workspace fallback. No blind lock editing, node_modules patch, registry alias/public fork, bundler-only alias or React/framework migration. A bundler alias alone can leave the original package in the install/shipped closure and is insufficient. If both local resolutions fail, retain UI gate OFF and report exact npm error plus resolved tree; recommend deferring UI while core proceeds. A custom React chat implementation is a material component substitution requiring a separate user decision, not this fallback.

Independence boundary: implement from this contract, React documentation and licensed consumer call sites. Do not read/copy/translate/reformat upstream use-composed-ref source, declarations, maps or tests into the new implementation. Do not vendor its release or derive output by modifying it. Prior source exposure must be disclosed to the independent reviewer; do not describe this as a formal legal clean-room guarantee. This turn inspected only licensed react-textarea-autosize consumer import/call sites: default `useComposedRef(libRef, userRef)` occurs in ESM/CJS variants. Later inventory must find every reachable consumer, export and subpath without expanding the compatibility module beyond what those callers require.

## Explicit React 19 lifecycle contract

Reference basis: [React ref callback documentation](https://react.dev/reference/react-dom/components/common#ref-callback) specifies returned cleanup, null fallback when no cleanup is returned, replacement on ref identity change and an extra development StrictMode cycle. It is living documentation; actual acceptance is against pinned 19.1.1 and its installed types, not the currently documented version alone.

The following is our compatibility contract, not a transcription of upstream behavior:

1. A default hook composes two refs: the consumer's writable internal object ref and its optional forwarded object/callback ref. Support null/undefined forwarded ref, generic node types and callback return `void` or a cleanup function. No string refs or non-function cleanup values. Type checks must reject accidental assignment-expression callback returns against the installed React types.
2. Attachment is commit-driven. Update internal ref before notifying the forwarded ref, so the consumer can observe its node. Do not assign refs, call callbacks or release resources during render, including abandoned concurrent renders. Preserve callback identity while both input ref identities remain unchanged; render-only parent changes must not detach/attach or resize/reset the input.
3. Each non-null attachment owns a distinct record of the exact node, participating refs and returned cleanup. The composed callback supplies a cleanup to React. On release, clear internal/object ref state and call each callback's captured cleanup exactly once if one was returned; otherwise call that callback with null exactly once. A callback that returned cleanup must not also receive a synthesized null for that attachment. Empty forwarded refs produce no callback work.
4. Explicit composed-callback null input uses the same release path and returns no new cleanup. Calling null and then the captured cleanup, or repeating cleanup/null, must not duplicate release. Null before any attachment causes no invented setup/cleanup. A consumed release record drops node/ref/cleanup references.
5. When either input ref identity changes on the same DOM node, React releases the old composed callback before attaching the new one. Release uses old captured identities, not refs from the latest render. New internal/object/callback refs receive the current node after old release; old refs are cleared, old cleanup is not lost, and unrelated renders do not churn refs.
6. Node replacement releases the old attachment before establishing the next. A delayed/duplicate old cleanup must not null the currently active internal/object refs or release new resources. Unmount and remount, including StrictMode setup/release/setup, have independent attachment records; count setup and release per attachment rather than expecting only one setup globally.
7. If inputs are the same ref identity, notify/clear that unique ref once. If one callback setup throws, roll back refs already attached; if cleanup throws, attempt remaining releases and clear owned state, then surface the failure. Do not swallow errors into a passing probe, leak an attachment or duplicate later release.

Internal-before-forwarded setup and clear-before-forwarded release are deliberate project requirements and must be checked against actual consumers. Avoid render-time mutable "latest callback" dispatch that reassigns ownership across abandoned renders. No listener, timer, network, provider action, history store or persistent cache is part of this hook.

## Later execution sequence and acceptance

### 1. Record bounded notice and SDK disposition — core_implementer + reviewer

- [ ] Record the supplied immutable proxy_tools/scroll-bar evidence, preserving actual text/hashes and conflicting declarations. Retain separate fact/inference fields and old audit results.
- [ ] Have reviewer validate SDK's exact closure/acquisition/notices and existing or required rerun evidence separately from native/UI. Record `SDK acquisition/notices PASS` independently; close the SDK integration subgate only when applicable SDK compatibility/review evidence is accepted. No change to overall production authorization.
- [ ] Leave proxy_tools/scroll-bar disposition review pending until evidence is accepted. A later-license inference rejected by reviewer remains an explicit native/UI notice blocker, with core work still allowed.

### 2. Prove local resolution in isolation — implementer

- [ ] Inventory all reachable consumer imports from licensed consumers and manifests; do not use upstream utility source as the implementation specification. Record npm version and candidate package/override identity.
- [ ] Build the small independent module in an isolated frontend fixture first; verify ESM default import, CJS require interoperability and generic types through the real consumer. Observe explicit assertion/type/resolution failures before completing the module.
- [ ] Generate the fixture lock with actual npm; run two fresh `npm ci --offline --ignore-scripts` installations from that lock and available audited cache, in separate task directories. Record exit codes and package-tree/resolution evidence. Missing cache is SKIP, never PASS or permission for a new download in this task.
- [ ] For every consumer, resolve `use-composed-ref` from that consumer's location and hash the resolved files against the committed local implementation. Assert one React 19.1.1 instance and no remote/nested original utility artifact. Inspect `npm ls --all --json` as well as paths; name-only checks cannot distinguish the intentionally same-name internal module.
- [ ] Workspace fallback, if necessary, must pass the same two-install proof. Repeated install/lock regeneration must not drift pins, fetch original utility or silently change unrelated dependencies. Preserve all unaffected manifest/lock entries. A file link lacking npm integrity needs committed content hashes, repository revision and path provenance rather than invented registry integrity.

### 3. Lifecycle and actual input tests — test_writer + implementer

- [ ] Implement the table below as behavioral fixtures in `frontend/src/app/assistant/use-composed-ref-compat.test.tsx`, using React 19.1.1 createRoot/act and the actual react-textarea-autosize/assistant-ui consumers. Run `npm --prefix frontend test -- src/app/assistant/use-composed-ref-compat.test.tsx` and `npm --prefix frontend run typecheck` in the prepared fixture. Required tests must pass with no ref-return warnings or invalid-hook errors.

| Fixture | Required observation |
| --- | --- |
| Object + ordinary callback | Internal ref has node before callback; release clears objects and gives callback one null |
| Object + cleanup callback | One node notification, one cleanup per attachment; no additional null for cleanup callback |
| Missing forwarded ref / duplicate ref identity | Internal ref attaches/releases; no invented callback; duplicate identity notified once |
| Same input refs on rerender | Same composed callback; no setup/release, selection reset or focus loss |
| Forwarded A to B; internal object A to B | Old refs release once, new refs see same node; no old-identity resource retained |
| DOM A to B; stale cleanup of A | B stays attached; old release cannot clear B or invoke B cleanup |
| Null before attach / repeated null / cleanup plus null | No false setup; no double release |
| StrictMode and unmount/remount | Each setup balanced by its own release; final objects null and zero retained resources |
| Setup/cleanup throw | Already acquired refs released; remaining cleanup attempted; observable failure, no success masking |
| Abandoned render before changed-ref commit | Committed refs keep their node/resources until actual commit/release |
| Actual textarea composer | User object/callback refs, typing, focus, selection and autosize work; ref swap and panel remount preserve approved behavior |

- [ ] Test CJS and ESM entrypoints as selected by actual package resolution, not by importing the internal module directly alone. Compile the generic declarations through the consuming component and React cleanup callback type fixtures.

### 4. Native and shipped closure proof — core_implementer + reviewer

- [ ] Extend the existing isolated gate using the proven replacement fixture. Use its existing command with Node 22.23.3, `--isolated --fake-only --node-env <prepared-fixture> --native-python-env build/assistant-sdk-probe-20261012`; concrete fixture path and immediate exit code go into the report. Run native fixtures sequentially at the already approved execution boundary; sandbox navigation failures remain failures, never silent retries or skipped acceptance.
- [ ] In WebView2 verify actual composer ref setup/cleanup, object/callback ref changes, typing/focus/selection, autosize, unmount/reopen, explicit onNew/onCancel and no send on mount/remount. Exercise development StrictMode ref cycling and the production bundle separately; production StrictMode alone cannot prove development's extra cycle. Retain original native load/result timeouts.
- [ ] Preserve strict self CSP, eval blocking, no CDN/remote loaders, fixed NODE_ENV substitution, local resource allowlist, zero runtime network/eval attempts, zero guarded Python socket attempts and window closure. Report each failed/skipped field separately; JS/Python guards remain distinct from an OS egress audit.
- [ ] Trace bundle modules/metafile and source maps to committed local hashes. Assert original upstream implementation is absent from every selected runtime installation, packaged assets, source maps, shipped notices inventory and final distribution's reachable closure. Name disappearance is not the criterion: the local package retains the consumed specifier. Original probe artifacts may remain unshipped historical evidence; do not delete them or copy them into the release.
- [ ] Update the current npm closure/provenance and notices to distinguish the removed original package from internal code. A license checker must allow only this exact reviewed private path/revision/hash as internal, not globally allow all UNLICENSED or missing-license packages. Inventory remaining dependency/peer notices afresh for the selected graph; all other gates stay mandatory.
- [ ] Independent reviewer checks authorship/provenance, resolution, callback ownership, failures/concurrency, API/CSP preservation, lock reproduction and original-code exclusion beyond passing tests. Only then port the proven local package/override/lock into production manifests. Final source/wheel/EXE acceptance still follows runtime Task 10; the earlier native component probe is not full packaging evidence.

## Gate separation and stop conditions

### 2026-10-13 root-cause-driven workspace correction

The two earlier resolver routes stopped FAIL. Diagnosis traced npm 10.9.9's
relative file override construction against the consumer path while validation
uses the project root; the resulting nested link targets a missing directory.
Workspace initialization also conflicts with the retained override's different
absolute/relative spec. This is not a license, Unicode path or effective
file-edge semver failure. Preserve the failed fixtures and diagnostics.

Architect approves exactly one isolated correction of the existing workspace
route: root workspaces include packages/use-composed-ref-compat, the direct
dependency is use-composed-ref version 1.4.1, and that package's override is
removed. The local private/UNLICENSED package uses stable version 1.4.1 and
explicit internal Codeviz description/provenance; implementation, declarations,
exports and exact React peer remain unchanged. Stable semver permits ordinary
workspace selection for the actual consumer's ^1.3.0, not a repair of npm's
file-override defect. No workspace:* protocol, npm patch, global adjustment,
validation bypass, absolute committed file spec or unrelated pin drift.

Generate the portable lock using unchanged npm and the audited offline cache,
then perform two fresh offline ignore-scripts installs at different root paths,
including a relocated Chinese Windows path. Both installs and complete npm ls
must exit zero; each real consumer must resolve the internal hashes and one
React 19.1.1 instance, with no original artifact or dangling nested link.
Preserve external versions/resolved URLs/integrities, record changed metadata
separately from unchanged code hashes, and require independent review before
promotion. Consumer/native/CSP/shipped-closure gates still apply. This bounded
diagnostic alone authorizes no production edit, new download, publication or
license change. If it fails, stop with exact evidence; no fourth speculative
attempt. Absolute-spec staging is not approved without portable proof.

SDK acquisition/notice closure can be accepted independently; it excludes proxy_tools and npm UI packages. Its compatibility/review subgate can close using its own required evidence while UI remains OFF. B5 pure tools and C core/config/jobs/authorization/evidence work against reviewed typed/fake ports can proceed now under the approved plan. No assistant-ui production dependency, enabled AI UI, real disclosure or complete-agent release follows from that separation. Real B adapters still require coordinated B4/B5 review and C acceptance; production sends require all authorization/service/packaging gates.

The overall C1/native/UI production gate remains OFF until the accepted notice dispositions, replacement resolution/lifecycle/native proof and independent review are all recorded. This addendum closes no executed gate by itself. SDK-only EXE evidence is not full application EXE evidence, and acquisition on four targets is not execution on all four.

Stop the local route and report an exact blocker if local resolution cannot exclude original artifacts, a consumer needs an incompatible or larger API, lifecycle/native/CSP behavior regresses, independent authorship cannot be supported, or the notice dispositions lack accepted evidence. Continue unaffected B5/C fake-domain work. Do not rewrite consumer libraries or introduce a custom chat/framework as an automatic remedy.

If a dependency, distribution rule or review requires this internal module to carry a public package LICENSE/open-source grant, identify the exact requirement and why the private exception cannot satisfy it. A new project/module license, rights-holder decision, public publication, upstream coordination or material component substitution then requires an explicit user choice. Do not invent that requirement in advance or ask for phase-by-phase confirmation within the approved internal route.

Architect self-check: scope is one internal transitive hook; independent authorship, private licensing boundary, two resolver attempts, React 19 ownership/null/cleanup/change/StrictMode contracts, real-consumer tests, native proof, committed reproducibility, shipped closure exclusion, exact internal-policy exception and SDK/core gate separation are all explicit. No implementation/test/native PASS is claimed in this turn. Further implementation requires independent reviewer clearance, not repetition of this architect's self-check.
