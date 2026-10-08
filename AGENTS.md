
# AGENTS.md

## Project execution policy

This repository uses task-based model routing.

The primary agent must classify non-trivial work before implementation.

### Default

Use the main project agent for ordinary tasks.

Default:

- model: gpt-5.6-sol
- reasoning: medium

### Mandatory routing

Architecture and system-design work:

- delegate to `architect`
- do not begin architecture-sensitive implementation before architecture review

Read-only repository exploration:

- delegate to `explorer` when useful

Normal feature or user-feedback implementation:

- delegate to `implementer`

Parser, Symbol Resolver, Graph IR, Semantic Graph,
Control Flow, Data Flow, Stable ID, Source Traceability,
and cross-module core implementation:

- delegate to `core_implementer`

Unit tests, fixtures and repetitive edge cases:

- delegate to `test_writer`

Complex test failures or unclear defects:

- delegate to `debugger`

After any non-trivial implementation:

- delegate an independent review to `reviewer`
- the reviewer must not assume the implementation is correct
- tests passing is necessary but not sufficient

Use `final_auditor` only when:

- the problem remains unresolved after normal Sol-based investigation;
- multiple attempted fixes have failed;
- architecture-level uncertainty remains;
- or a major milestone explicitly requires final audit.

### User-requested changes

When a user supplies modification feedback:

Small isolated UI/text changes:

- Terra-class work is acceptable.

Normal behavioral or feature changes:

- use `implementer`.

Changes affecting Parser, Resolver, Graph IR,
Semantic Rules, View Builder, Search/Source mapping,
or multiple subsystems:

- use `core_implementer`.

After implementation:

- run appropriate tests;
- inspect git diff;
- use `reviewer` for non-trivial changes.

### Never downgrade solely for cost

Do not route a task to a weaker model if doing so materially
increases the risk of incorrect architecture or behavior.

### Never escalate solely because a task is large

Large but mechanical work should use an efficient model.
Escalation is based on reasoning difficulty, uncertainty,
cross-module coupling and failure history.
