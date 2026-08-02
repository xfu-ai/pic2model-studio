# Python Pi Agent source manifest and behavior map

## Frozen reference

| Field | Value |
| --- | --- |
| Repository | Frozen upstream Pi source checkout |
| Commit | `8eef62ed3ea62d646a7fad92fa583fc8d71fec17` |
| Commit timestamp | `2026-07-24T19:05:08Z` |
| License | MIT, Copyright © 2025 Mario Zechner |
| Freeze rule | Later Pi changes are out of scope until this manifest and the generated provider catalog are explicitly refreshed. |

## Reference file list

The paths below are read from the frozen commit, not from a moving branch.
Directory entries are complete source families; their exact members are fixed
by the recorded commit.

| Pi source family | Referenced files or family | Python migration target |
| --- | --- | --- |
| Agent public API | `packages/agent/src/index.ts`, `types.ts`, `stream-fn.ts` | phases 1, 3, 5 |
| Agent execution | `packages/agent/src/agent.ts`, `agent-loop.ts` | phases 3 and 5 |
| Harness/session | `packages/agent/src/harness/agent-harness.ts`, `messages.ts`, `types.ts`, `session/{session,jsonl-repo,jsonl-storage,memory-repo,memory-storage,repo-utils}.ts` | phases 6–7 |
| Compaction | `packages/agent/src/harness/compaction/{compaction,utils,branch-summarization}.ts`, `packages/coding-agent/src/core/agent-session.ts` | phase 7; branch-tree behavior is excluded |
| Skills/templates | `packages/agent/src/harness/{skills,prompt-templates,system-prompt}.ts` | phase 10 |
| Execution environment/tools | `packages/agent/src/harness/env/nodejs.ts`, `harness/tools/{index,tool-context,path-utils,file-mutation-queue,read,write,edit,edit-diff,bash,image}.ts`, `harness/utils/{shell-output,truncate}.ts` | phase 9 |
| Provider primitives | `packages/ai/src/{types,models,model-catalog,models-store,api-registry,session-resources}.ts`, `utils/{event-stream,overflow,estimate,retry,provider-retry,validation}.ts` | phases 1–2, 11 |
| API adapters | `packages/ai/src/api/` (all adapter modules) and `images-api-registry.ts` | phases 2 and 11 |
| Provider descriptors/models | `packages/ai/src/providers/` (all `*.ts`, `*.models.ts`, image registration, Radius and Cloudflare helpers) | phase 11 |
| Authentication | `packages/ai/src/auth/` (all API-key, OAuth, AWS, Google, Cloudflare, Radius, token-store and resolver modules), `env-api-keys.ts`, `oauth.ts` | phase 11 |

## Behavior mapping

| Pi behavior | Python-native contract | Phase | Deliberate difference |
| --- | --- | ---:| --- |
| Typed messages, Tool Calls, usage and stream events | dataclasses/Pydantic DTOs and JSON serializers | 1 | No TypeScript/TypeBox runtime dependency |
| Async provider stream and cancellation | `asyncio` async iterator plus cancellation token | 1–2 | HTTP implementation uses Python async clients |
| Tool loop with hooks and limits | ordered sequential `AgentLoop`; Tool Results feed the next turn | 3 | Parallel type retained, not enabled initially |
| Stateful prompt queues | Agent facade with steer/follow-up/next-turn and one structural run | 5 | No TUI interaction state |
| Linear/memory session facilities | SQLite linear conversation with interrupted-operation recovery | 6 | No session tree, leaf, or branch navigation |
| Harness context projection | immutable turn snapshot and composable context transforms | 7 | AIPic project context is an extension input |
| Automatic compaction | persisted records, retained tail, summary, threshold/overflow retry | 7 | Branch summarization is excluded |
| Extension hooks | deterministic Python registration and error isolation | 8 | Trusted local Python only; no JS dynamic extension loader |
| Filesystem/shell tools | workspace-root-bound `ExecutionEnv` and PowerShell backend on Windows | 9 | No direct Node filesystem/process usage |
| Skill discovery/templates | application/user/project precedence and delayed loading | 10 | Skill never gains SecretStore access |
| Provider/auth/catalog registry | protocol adapters, descriptors, credential resolvers, versioned generated catalog | 11 | No runtime dependency on the Pi checkout |
| Coding-agent application integration | AIPic ToolRegistry adapter, FastAPI and SSE | 12–13 | Pi CLI/TUI is not migrated |

## Source-review discipline

Before each implementation phase, the corresponding row's source files must
be read in full and the reviewed paths added to that phase's evidence report.
This manifest is the phase-0 freeze and map, not a substitute for that source
review.
