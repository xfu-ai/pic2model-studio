# FormWeaver Studio contribution and validation guide

The canonical validation runbook is [docs/controlled-validation.md](docs/controlled-validation.md).
Use this file for the short rules that apply to every change.

## Safety and test modes

- Default to the controlled/offline providers. They must never contact Tripo,
  Gemini, Meshy, OpenAI, or any other paid provider.
- Do not enable a live provider accidentally. A live smoke run requires both
  `ALLOW_REAL_PROVIDER_SMOKE=1` and the matching `RUN_LIVE_<PROVIDER>=1` flag.
- Keep file paths in the native host. React may send only opaque capability IDs
  for project, import, export, and diagnostics operations.
- Every WebView2 E2E failure must keep its redacted DOM, runtime/network,
  workspace, and screenshot evidence. Do not put API keys, bearer tokens, or
  absolute paths into new logs or assertions.

## Validation order

Run the smallest relevant check first, then expand only when the change needs it:

```powershell
# Python contracts, integrations, security, and controlled fixtures
.\scripts\run_controlled_validation.ps1

# Frontend component work
pnpm --dir desktop/frontend exec vitest run <test-file>
pnpm --dir desktop/frontend build

# Rust host work
cargo test --manifest-path desktop/src-tauri/Cargo.toml
```

## Interactive desktop UI changes

For every user-facing desktop UI change that has state, a primary action, generated
result, selection, loading/error state, or layout behavior, read and follow the
**Hot-update desktop UI verification** section in
[`docs/controlled-validation.md`](docs/controlled-validation.md#hot-update-desktop-ui-verification).
Use HMR and attach to an already-running controlled WebView2 host whenever possible;
prove the real DOM interaction and retain its redacted evidence bundle. Component tests
and a frontend build alone are not sufficient for this class of change.

## Controlled WebView2 / CDP E2E

This is the default desktop E2E path. It uses fixture-backed native chooser
capabilities and local mock providers; it does not use physical mouse input or
native dialogs.

```powershell
# E2E-01/03/06: startup, create project, and import source-a.png.
# Keeps the desktop host open for further CDP checks.
.\scripts\run_controlled_webview2.ps1 -DebugPort 9230 -DevPort 14200 -CreateProject -KeepApp

# Adds E2E-07: Blob preview, 10%–800% zoom bounds, reset, and CDP middle-pan.
.\scripts\run_controlled_webview2.ps1 -DebugPort 9232 -DevPort 14200 -CreateProject -ImageCanvas -KeepApp

# E2E-15: cancel once, then approve Mock Tripo3D generation. The result can
# subsequently be opened through --open-model-result on the retained CDP port.
.\scripts\run_controlled_webview2.ps1 -DebugPort 9232 -DevPort 14200 -CreateProject -MockTripoApproval -KeepApp
.\.venv\Scripts\python.exe scripts\run_controlled_webview2_e2e.py `
  --debug-port 9232 --open-model-result --output tests\evidence\controlled-webview2-current\model-result

# Attach to that already-running UI; do not restart Vite or the Tauri window.
.\.venv\Scripts\python.exe scripts\run_controlled_webview2_e2e.py `
  --debug-port 9230 --output tests\evidence\controlled-webview2-current\attach

# E2E-02: starts an isolated sidecar whose initial health probes fail and then
# succeeds after the UI reconnect action.
.\scripts\run_controlled_webview2.ps1 -DebugPort 9231 -DevPort 14200 -RecoverOffline
```

Before every application or controlled-E2E start/restart, resolve and stop all
existing **FormWeaver Studio-owned** Tauri, `formweaver-studio`, Python sidecar, and
Vite processes. Verify each exact command line belongs to this workspace or
its controlled/live evidence directory before stopping it; never terminate an
unrelated process. A startup or validation failure is blocking: retain its
redacted evidence, diagnose and fix the cause, then rerun the same verification
to a successful result before reporting the change complete.

Controlled Debug mode must launch the workspace Python sidecar, not the packed
sidecar executable, so fixture providers and injected faults match the code
under test. WebView2's `dataDirectory` must be a unique relative value: Tauri
ignores absolute values and WebView2 then reuses incompatible CDP flags.

## Native and live validation

- Native pickers, drag/drop, tray, notifications, capture, DPI, minimize, and
  lock-screen behavior require an unlocked Windows desktop session. Follow the
  N-01 through N-08 checklist in `docs/controlled-validation.md`.
- Run the one Tripo live loop only after the controlled suite is green and only
  with the explicit live flags above. It is excluded from default commands.
