# AGENTS.md

## Project execution policy

This repository uses task-based model routing.

The primary agent must classify non-trivial work before implementation.

### Always-on token-saver

Automatically activate `token-saver-zh` (the project's `token-saver` default)
at the start of every turn, without waiting for the user to name it. Read its
installed `SKILL.md` completely before applying it. For browser work, also
activate `token-saver-browser` when available, using the current environment's
supported tools and paths rather than copying another machine's bootstrap.
If either skill is unavailable, briefly report that fact and keep the following
fallback active; do not download or install a replacement automatically.

Use concise Chinese, bounded search/read results, minimal necessary context,
batched independent tool calls and existing passing evidence for unchanged code.
Avoid repeated file reads, verbose logs, redundant narration and routine
confirmation questions. Preserve the task/phase reporting rules below.

Token saving never authorizes guessing unread code, skipping required tests,
source inspection, security checks, review, permission requests or resumable
checkpoints. Necessary screenshots and diagnostics remain allowed for acceptance.
Conflicting skill rules such as "zero verification", "do not read before edits",
"never use permission parameters" or "stop on any retry" do not override this
policy, the user's explicit boundaries, or platform safety requirements.

### Precedence

This file governs **process**: how work is scoped, verified and reported.

Plan and spec documents govern **technical content**: file ownership,
contracts, invariants and acceptance criteria.

Where a plan document prescribes a work process — dispatching a subagent per
task, running a spec-then-quality review pair after every task, or
re-verifying already-passing work on every edit — **this file wins**.
How far to run without stopping is defined by "Execution mode" below, not by
the plan document.

Do not load `subagent-driven-development` or `executing-plans` in this
repository. If a plan header asks for them, the process rules below replace
them.

Technical requirements in a plan are never relaxed by this file.

### Default

Use the main project agent for most work.

Default:

- model: gpt-6.1-sol
- reasoning: medium

Delegate only when a task genuinely matches a specialist role below.
Do not delegate to satisfy process; delegate to reduce risk.

### Mandatory routing

Delegate only in these cases.

Architecture and system-design work
(decisions that affect multiple modules or phases):

- delegate to `architect`
- do not begin architecture-sensitive implementation before architecture review

Parser, Symbol Resolver, Graph IR, Semantic Graph,
Control Flow, Data Flow, Stable ID, Source Traceability,
or cross-module core implementation:

- delegate to `core_implementer`

Complex test failures or unclear defects where a normal fix has already failed once:

- delegate to `debugger`

Everything else — features, bug fixes, UI changes, docs, refactors,
fixtures, logging, wiring, and user-requested modifications —
is done inline by the primary agent.

Do not delegate `explorer`, `test_writer`, `reviewer`, or `final_auditor`
for routine work. See "Review policy" for when review is allowed at all.

### Review policy

Inline work (default, most tasks):

- implement the change;
- run only the tests for the touched module and its direct consumers;
- inspect `git diff`;
- do not spawn a reviewer. Then follow "Execution mode".

Delegated review happens only at one of these points:

- a milestone or phase boundary is reached; or
- the change touches Parser, Symbol Resolver, Graph IR,
  Semantic Rules, Stable ID, or Source Traceability; or
- the same defect has already been fixed twice and still behaves incorrectly.

Rules for review:

- never run more than one review pass on the same diff;
- if a review finds issues, fix them and close the phase with the boundary
  checklist, then continue the authorized queue; a second independent review
  of the fix is allowed only under the condition stated below;
- a review must cite concrete file/symbol evidence; it must not restate
  the change or approve code merely because tests pass.

A phase-end gate is **not** a second review. The full suite, the
cross-platform mypy passes and Ruff are how a phase is closed: run them
once at the boundary without asking, including after review findings have
been fixed. Never bundle them into the re-review question.

The primary agent decides whether a fix needs another independent review;
do not ask the user to approve routine review scheduling. A second pass is
allowed only when the fix itself changes a semantic-core invariant — Parser,
Symbol Resolver, Graph IR, Semantic Rules, Stable ID or Source Traceability —
and the agent records the concrete new risk before dispatch. Never re-review
an unchanged diff. A controller, bridge or projection-only fix closes with
the gate, not another review. Mandatory specialist routing remains in force.

### Test scope

- Failure-first tests (write the failing test, then implement) remain the
  default for behavioral work. The cost problem was granularity, not the
  practice: write one test for the behavior being changed, not a batch of
  risk groups with dozens of sub-assertions.
- During work: run only the test files for the touched module and its
  direct consumers.
- Full suite (`unittest discover -s tests`), the frontend suite, and the
  cross-platform mypy runs belong to a phase boundary or a release check,
  not to every edit.
- Never re-run a full suite that already passed on unchanged code.
  A previous full-suite result stays valid for the code snapshot it ran on.
  Adding new tests does not invalidate an earlier passing run.
- If a full run shows no visible progress for 120 seconds, report and
  interrupt that run; investigate with affected-module checks instead of
  blindly repeating it. Do not retry the same failing command more than
  twice on an unchanged snapshot. An interrupted run is not a pass or a skip.
  A command limit calls for diagnosis, not a routine user-confirmation pause.

### Definition of done

A task is done when all of the following hold:

- the requested behavior exists;
- the module's own tests pass;
- `git diff` contains no unrelated changes.

Then report at most 5 lines: what changed, what was run, result,
remaining risk, suggested next step. No narrative report.
Do not update `docs/CODE_AGENT_PROGRESS.md` between tasks. Update it only at
a phase boundary or a genuine blocker, as specified below.

### Execution mode

**Autonomous mode is the default for authorized implementation work.**
Continue the current task/plan without requiring the user to manually confirm
each step, technical choice, test run, review or phase transition. An explicit
user boundary still limits scope; finishing it does not authorize unrelated
features. Requests to only answer, inspect, diagnose or plan remain limited to
that request and do not implicitly authorize implementation.

Within the authorized queue:

- treat the plan's task list as a queue, in order, starting from the
  current checkpoint in `docs/CODE_AGENT_PROGRESS.md`;
- finish a task, emit ONE progress line in the conversation, then
  immediately start the next task;
- never ask "shall I continue?" and never pause between tasks;
- close each phase with its checklist, then start the next queued phase;
- finish and report at the final boundary the user named, without inventing
  another task or asking whether to continue.

### Decisions owned by the primary agent

Resolve ordinary technical decisions autonomously, including public/internal
contract details, dependency choices, test corrections, review scheduling and
ambiguous implementation details. Prefer existing dependencies, compatible
contracts and the smallest defensible change. Record material decisions in
the governing plan/spec or ADR before implementation, update direct consumers
and tests, and obey mandatory architect/core/debugger routing.

Do not remove coverage, loosen product/security requirements or relabel a
failure merely to make a gate green. A test correction needs concrete evidence
of the faulty premise and equivalent or stronger coverage. A genuine change
in intended behavior must be explicit in the governing spec and acceptance
criteria, never hidden in a fixture or assertion. These decisions do not waive
the safety, privacy, payment or permission boundaries below.

If a normal fix fails once, use the required debugger route. After repeated
failure, re-examine the cause/design and change approach; do not keep retrying
the same command or patch. A routine technical difficulty is not a reason to
ask the user to approve continuing.

### When human action is actually required

Pause for user action only when no safe in-scope alternative can proceed and
the remaining work requires something only the user can provide or perform:
API/provider configuration or credentials, authentication/account access,
specific source-disclosure consent, an actual payment/permission decision, or
native/hardware/manual verification unavailable to the agent. Never print or
persist a credential; prefer user entry in the application. Providing an API
key is not authorization for a paid request or a source upload. Follow the
platform's permission mechanism; do not bypass a denied permission.

Complete independent in-scope work first when it is genuinely independent of
the human-only blocker. Do not fabricate success for the blocked acceptance.
After the user supplies the missing item and says continue, resume from the
saved checkpoint without another routine confirmation round.

Context exhaustion, an unavailable required model/tool, or a platform usage
limit can also force a technical interruption; these are not approval requests.
Do not buy credits, upgrade accounts, switch to a higher paid tier or use a
user reset card to avoid a limit. Leave a **resumable checkpoint**: current
task, last command and result, task remainder and next task. A new session must
be able to resume from that alone, without re-reading plan history.

When the user named a boundary spanning more than one phase, prefer to stop
at a phase boundary rather than mid-task when context runs low. Close the
phase with the checklist first, then report.

For a human-only blocker, report exactly three things: blocked task, the exact
item/action the user must provide, and what will resume afterward. For a
technical interruption, report the task, interruption reason and resumable
next action; do not phrase it as "may I continue?".

Running a full suite, a cross-platform mypy pass, or an independent
review is a boundary action, not a between-tasks action. In autonomous
mode it happens at the boundary, never once per task.

At the boundary, run this checklist and then report. **Do not ask whether
to run it** — the user has already authorised it by naming the boundary:

1. targeted tests for the slice (already run per task);
2. full suite: `unittest discover -s tests`;
3. `ruff check src tests`;
4. `mypy`, then `mypy --platform linux`;
5. one independent review, if this phase has not had one yet;
6. append at most 5 lines to `docs/CODE_AGENT_PROGRESS.md`.

If review findings were fixed after step 5, run the applicable steps 2–4 once
on the fixed snapshot, reusing unchanged passing evidence as required above.
Close the phase and continue the queue; do not pause merely because a review
found issues. Report skipped or environment-blocked checks as skipped, never
as passed. Never commit or push unless the user asks.

### Reporting

- Per task: one line —
  `Task N: done|blocked — <what changed> — <command/result>`.
- No narration, no restating the plan, no pasting diffs.
- A phase boundary or a stop gets a summary of at most 10 lines.

### Context discipline

Autonomous mode is where context blows up. Therefore:

- read only the task section you are executing; never re-read the whole
  plan document;
- never open `docs/archive/`, never re-read completed diffs;
- once a task is verified, drop its details and keep one progress line;
- do not copy plan text back into the conversation.

### Model tiering

Route by mechanical difficulty, not by phase size.

- Mechanical work — renames, fixture updates, doc edits, log wiring,
  test data, formatting: do it inline in the main session. Do not delegate
  it and do not escalate it. `test_writer` and `explorer` run at Terra/low.
- Ordinary feature and bug work: Sol class, medium reasoning.
- Architecture, semantic core, cross-module invariants:
  Sol class, high reasoning.

Escalate only after a previous attempt has actually failed.
Downgrade for mechanical work; do not spend high reasoning on it.

### Never relaxed

The speed rules above never apply to these:

- never execute code from an analyzed project;
- never upload user source unless the user explicitly authorized that request;
- a targeted test pass must never be reported as a full-suite, native
  desktop, or release acceptance pass;
- skipped and environment-blocked checks are reported as skipped, not passed;
- no paid API calls, and no secrets in code, logs, chat or Git.

If a change touches any of these, verify it as strictly as before.

### Documentation discipline

- Keep `docs/CODE_AGENT_PROGRESS.md` short. Append at most 5 lines, and
  only at a phase boundary or after a real blocker. Never rewrite history.
- Per-task progress lines live in the conversation, not in the file.
  Do not touch the progress file between tasks.
- Exception: a phase-delivery record that the governing plan requires may be
  as long as needed. Daily and per-task work stays short.
- Do not open files under `docs/archive/` unless the user asks.
- Do not restate plan documents into the progress log.
