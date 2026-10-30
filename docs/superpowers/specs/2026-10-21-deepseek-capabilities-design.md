# DeepSeek capabilities: honest local validation

Status: the offline capability slice is accepted; see the implementation
plan's recovery closure for exact gates and retained limitations. This is not
a production DeepSeek transport or packaged desktop acceptance.

## Authority and scope

The user approved distinguishing DeepSeek JSON formatting plus local strict
validation from the existing remote strict-schema capability. Estimated input
accounting was separately approved and implemented. Neither authorization
permits real API calls, uploading project source or choosing the beta endpoint.
AGENTS.md governs routing, test scope and boundary reporting. Existing work in
this workspace must be preserved; no commit or push is part of this slice.

This first capability slice carries a verified connection result through the
existing job/poll/UI lifecycle. It does not introduce a working DeepSeek
transport, change configuration inputs or enable the production factory.
An offline completed result is not evidence of real-provider compatibility.

## Selected approach and alternatives

Selected: a closed, additive success-result union, retaining the old strict
branch without new mandatory fields. A separate branch explicitly declares
JSON formatting and local validation. This is compatible with the user's
approved meaning and does not relabel weaker evidence as remote strictness.

Rejected: silently returning the old two `true` flags for JSON mode, because
local schema checks do not prove server-side schema enforcement. Also rejected:
switching to DeepSeek beta strict tools, which is a different destination and
does not establish strict final-answer support on the standard endpoint.

## Public capability contract

Only completed, successful connection polls carry `connectionResult`:

```typescript
type ConnectionResult =
  | { toolSupported: true; structuredOutputSupported: true }
  | { toolSupported: true; structuredOutputSupported: false;
      jsonObjectSupported: true; outputValidation: 'local-json-schema' };
```

Both branches are closed: reject unknown fields, missing fields, false tool
support, truthy substitutes, or hybrids such as strict `true` plus local-only
fields. `structuredOutputSupported` retains the remote strict-schema meaning.
`toolSupported` means the probe actually observed and validated the expected
tool invocation; it does not assert remote strict-tool enforcement. The local
branch requires a parsed JSON object that passed the application's exact
synthetic schema, not merely a provider's assertion of JSON support.

Failed, cancelled, stale and incomplete polls expose null, never a partial
success. Scope, configuration revision, cancellation, cleanup, usage and
replay requirements remain unchanged. Navigation/explanation polls must reject
connection-only capability state. Resource release and success remain separate.

## UI behavior

The current settings panel displays a fixed translated capability message for
the current configuration's completed connection result. Strict success says
remote strict schema; the DeepSeek branch says JSON output plus local schema
validation, and explicitly states that the server does not guarantee the
schema. Tool support is described independently. No provider-controlled text
or raw diagnostics becomes HTML or a capability label.

Changing configuration immediately hides an old result; late responses cannot
restore it. Opening settings never starts a connection test. The existing
explicit test action remains source-free and can cost money when production
is eventually enabled; no tests in this slice invoke a network provider.

## Protocol and security boundary for subsequent work

The later dedicated profile targets only `deepseek-flash`, non-thinking, the
approved official HTTPS destination, `max_tokens` and `json_object`; JSON
instructions and an example must be app-owned scaffolding, not an unaccounted
user-controlled source channel. Complete final-body attribution and the exact
frozen estimated-accounting fields must be proved at the real SDK transport
seam before production registration. The generic strict capture path must
continue to reject unproven input framing or incomplete attribution.

The standard profile must not claim beta strict tools. Tool arguments and final
answers remain locally validated before use, preserving existing bounded
repair, usage, cancellation, disclosure/freshness and request/output limits.
No automatic retry, beta/model fallback, project execution or upload is added.

## Acceptance for this first slice

1. Old strict success fixtures and tests are unchanged and still pass.
2. A trusted offline completed local-validation result survives job retirement
   and closed public projection without being upgraded to remote strictness.
3. Backend and frontend reject malformed capability variants and cross-kind or
   non-completed capability state; usage and cleanup flags remain unchanged.
4. Settings renders truthful Chinese and English labels, makes no request on
   mount, and continues to hide obsolete configuration results.
5. Affected tests run during tasks; full Python/frontend and static gates plus
   one independent review run at this slice boundary. Existing skips, todo
   cases and environment limitations are reported rather than counted as passes.

## Evidence

Official [JSON Output](https://api-docs.deepseek.com/guides/json_mode/) documents
`json_object` and JSON instructions; official
[Tool Calls](https://api-docs.deepseek.com/guides/tool_calls/) places remote
strict tools at the separate beta endpoint. These documents establish protocol
differences, not real API acceptance. Goodall's P3 architecture consultation
identified the original capability mismatch. Goodall approved the first
slice's internal propagation before implementation: `_connection_values.py`
owns a frozen/slots exact local-capability four-tuple; append optional
`connection_capabilities` fields to the probe result, job and poll state.
`None` preserves legacy strict success. Only guarded successful completion
copies explicit capabilities; other statuses/cross-kind use reject or omit
them. Retired jobs keep the existing frozen poll state, with no duplicated
capability storage. SDK/profile/factory modules stay outside this slice.
