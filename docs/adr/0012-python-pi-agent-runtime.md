# ADR-0012: Python Pi Agent Runtime

- Status: accepted
- Date: 2026-07-25
- Decision owner: FormWeaver Studio
- Source baseline: Pi `8eef62ed3ea62d646a7fad92fa583fc8d71fec17`

## Context

`docs/execution-batches/B03-agent-orchestration.md` specifies a custom,
decision-envelope Agent runtime with a large Run state machine. The migration
plan adopts Pi's native streaming and Tool Calling semantics instead: a
provider emits a complete assistant message, native Tool Calls are validated
and executed, Tool Results are appended, and the next provider turn begins
until there are no Tool Calls.

Maintaining both runtimes would duplicate session, queue, summary, recovery,
and Tool orchestration behavior while giving incompatible meanings to an Agent
turn. This ADR resolves that conflict before Agent code is introduced.

## Decision

The Python Pi Agent implementation described in
`docs/python-pi-agent-migration-plan.md` is the sole runtime specification for
new Agent code. It uses a linear SQLite session, asynchronous native Tool
Calling, `Agent`/`AgentHarness`, steering and follow-up queues, and automatic
context compaction. It deliberately excludes Pi's conversation tree,
coding-agent CLI/TUI, multi-agent behavior, plugin marketplace, and TypeScript
packaging.

### Superseded B03 runtime requirements

The following B03 mechanisms are superseded for the new Agent runtime:

- `AgentDecision={final|needs_user_input|tool_calls}` as the provider response
  protocol (B03 appendix A.3);
- the custom `agent_runs` state machine as the generic Agent control loop
  (B03 sections 1.3 and B03-04/B03-05);
- the B03-specific context builder and summary trigger semantics
  (B03-02);
- B03's custom Agent provider abstraction and fixed four-call envelope.

They are replaced by the phase 1–7 contracts in the migration plan, including
ProviderEvent/AgentEvent streaming, Tool Result feedback, configured limits,
linear session recovery, and compaction at safe save-points.

### Retained B03 and B01 business contracts

The new runtime must retain rather than reimplement these application facts:

- B01 ToolRegistry validation, risk policy, project/asset ownership,
  idempotency, audit records, and `ToolResultV1` contracts;
- B02 Job terminal events, result registration, and unknown-submission safety;
- B03's requirement that queued jobs release the provider turn and resume only
  through an auditable event;
- API/SSE authentication, loopback-only security, opaque asset IDs, event
  replay, secret/path/reasoning redaction, and cancellation safety.

The AIPic ToolRegistry adapter (phase 12) and business extensions own those
application-specific semantics. They do not enter Agent Core.

## Consequences

- No new code may implement both a B03 decision envelope and native Tool
  Calling for the same Agent interaction.
- Agent Core must not branch on provider name or import AIPic, FastAPI, or
  SQLite implementations.
- `B03-agent-orchestration.md` remains historical input during the migration;
  phase 15 must mark its superseded runtime clauses and point to the new
  runtime documentation so that there is one active specification.
- The frozen Pi source list and behavior mapping are maintained in
  `docs/python-pi-agent-source-manifest.md`.
