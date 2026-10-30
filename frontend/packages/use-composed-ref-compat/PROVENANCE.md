# Internal compatibility module

Revision: `1.4.0-codeviz.1`, C1 local candidate, authored 2026-10-12.

This is independently authored internal project code. The package name
`use-composed-ref` serves only the requested consumer module specifier. This is
not upstream version 1.4.0, an upstream patch, or a vendored upstream release.
It is private and UNLICENSED; no author, copyright owner, upstream repository,
root license, publication permission, or third-party license is asserted.
This record belongs to the internal module, not upstream third-party notices.

## Authorship process and boundaries

The C1 implementer wrote these files from the main-agent-provided, architect-approved package and
lifecycle contract, the independently specified existing project lifecycle
test, and React's documented APIs. The supplied consumer specification was the
default call `useComposedRef(libRef, userRef)`; no consumer source was needed in
this execution. React documentation consulted:
<https://react.dev/reference/react-dom/components/common#ref-callback>.
The living documentation is not proof of pinned-version acceptance.

No upstream use-composed-ref implementation, declarations, source maps, or
tests were read, copied, translated, reformatted, or used to derive these files
during this execution. No such prior exposure is present in the context
available to this implementer; this is an execution disclosure, not a formal
legal clean-room guarantee or an assertion about other agents' histories.
The supplied contract records a previous agent's inspection of licensed
react-textarea-autosize import/call sites. Independent review must assess the
implementation and this process record separately.

The CJS file contains the sole hook implementation and requires the existing
React peer. The MJS file only forwards its default export. The declarations
use installed React ref types, including its callback return restriction.
No new runtime dependency, bundled React, downloader, fabricated LICENSE,
listener, timer, provider, persistent cache, or network behavior was added.

## Exact file bytes (SHA-256)

Hashes identify this local revision without a Git operation. PROVENANCE.md
does not contain its own hash because inserting it changes the hashed bytes;
its hash can be computed independently for the review handoff.

| File | SHA-256 |
| --- | --- |
| package.json | `3DAEF77A7317CF5C70696D46270CEA34A1113807DFDFF0982D23A5B11BBB1E87` |
| index.cjs | `9E2F3F3746CE3DEE0B09BCFE51FE5BFAA7DB3FE84FDE4077D9BF597AE244F228` |
| index.mjs | `175F971373D12AA0D6DDC2E96E8BDD83C7AA095D1C7E10D906617244CF5D1053` |
| index.d.ts | `DE8FD4AD18BC88AB557FFC596D4ED234053E455C8C3C94362AB7BAD756E50504` |

## Evidence and remaining gates

The unchanged project lifecycle test first reported 13 ordinary missing-
candidate failures and 2 todo entries, with no collection errors. After this
implementation, it reported 13 passing cases and the same 2 todo entries
against React and React DOM 19.1.1. Dynamic Vitest MJS/CJS loading succeeded
without a source fallback or test weakening. Targeted ESLint and frontend
`tsc --noEmit` passed. A separate in-memory strict TypeScript check of this
local declaration accepted generic refs, void callbacks and cleanup callbacks,
and rejected assignment-expression, numeric, null-returning and string-ref
cases. That check is not actual consumer compilation or resolver acceptance.
The CJS require has a single-line lint exception because CommonJS is explicitly
required by the approved contract; the frontend lint configuration was not
changed.

Execution environment: Node 25.7.0, npm 11.10.1, Vitest 4.1.11,
TypeScript 5.9.2. Node 22.23.3 and its bundled npm have not been exercised here.
No dependency installation or root manifest/lock replacement was performed.
Production assistant UI integration remains gated OFF.

Pending: main-task independent review; isolated resolution fixture on the
specified runtime; reachable licensed-consumer inventory and integration;
actual consumer declaration acceptance; native packaging and shipped closure
gates. The two test todo entries remain pending, not passed. This candidate
alone does not establish consumer, resolver, native, or legal acceptance.

Main-task follow-up: Node 22.23.3 independently ran the same lifecycle suite
with 13 passing cases and 2 pending entries. Targeted ESLint and frontend
typechecking also passed using that executable. This does not close the
pending actual-consumer, resolution, native or shipped-closure gates.

## Authorized application integration — 2026-10-19

The user authorized the production frontend dependency/lock integration. The
private package now uses the independently tested stable workspace version
`1.4.1` and the explicit internal-module description. This is not an upstream
release, patch, public license grant, or npm publication. CJS, MJS and declaration
hashes above are unchanged and match the accepted isolated workspace fixture.
The new application package.json hash is
`EDD2436C529C1E78C89078878368C01DEC21190BB66AB282E5C11661048F53C8`.
The previous manifest hash and isolated failures remain historical evidence.

The application lock preserves every pre-existing external version, resolved URL
and integrity. Its 91 assistant-ui subtree entries match the accepted fixture's
external pins/URLs/integrities; the shared React 19.1.1 instance and local ref
workspace are outside that subtree. Offline ignore-scripts installation used the
existing task Node 22.23.3 and unchanged npm, with native sandbox approval solely
for access to the existing npm artifact cache. Actual textarea-consumer resolution
and one-React checks pass in the app; no original upstream ref artifact is used.
This does not prove full application native/packaged acceptance or enable model
requests. Existing lifecycle todo entries remain todo.
