# Python Agent Runtime

This is the operational specification for AIPicToModel's Python Pi Agent
runtime. ADR-0012 records the architectural decision; the migration ledger and
phase evidence record verification. The historical B03 decision-envelope
runtime is not an implementation contract.

## Runtime model

`AgentLoop` consumes provider stream events, persists complete messages through
`AgentHarness`, executes only registered tools, appends each Tool Result, and
continues until the assistant stops requesting tools. The runtime uses one
linear SQLite session per Conversation. It has no conversation tree, CLI/TUI,
multi-agent system, or plugin marketplace.

## Safety boundaries

- Provider protocol differences belong in adapters; Agent Core never branches on provider name.
- AIPic business work enters only through `AIPicToolAdapter` and B01 `ToolRegistry`/`AssetService`.
- File tools use `LocalExecutionEnv`: configured roots, atomic writes, unique edits,
  PowerShell execution, cancellation, output limits, and filtered environment variables.
- API/SSE projections exclude credentials, Authorization values, reasoning, tool arguments/results,
  and absolute workspace paths.

## Session, recovery, and compaction

Original messages remain in SQLite. Compaction records a structured rolling
summary plus retained tail; threshold, manual, and provider-overflow triggers
are supported. Tool Call/Result pairs are not split. Overflow retries the
original turn at most once. Restart marks unfinished operations interrupted.

## API and SSE

The authenticated `/v1/agent/conversations` API creates Conversations,
submits messages, reads complete messages/status, aborts, queues steering or
follow-up, manages Skills, reports Extensions, and exposes replayable SSE.
Agent API events have a durable monotonic sequence and accept `Last-Event-ID`.

## Provider configuration and checks

Provider descriptors, authentication, and frozen catalog metadata live in
`src/aipic_to_model/agent/providers`. DeepSeek uses `DEEPSEEK_API_KEY` only
from process environment or OS keyring; it is never logged, persisted, or put
in evidence. Run ruff, format, pyright, pytest, security/provider contracts,
and catalog synchronization. Real tests require `RUN_LIVE_LLM_TESTS=1` and
bounded temperature, turns, tool calls, output, and deadlines.
