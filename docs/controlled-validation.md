# Controlled validation

For the short, repository-wide rules that apply to automated contributors,
start with [`AGENTS.md`](../AGENTS.md). This document is the detailed runbook
and evidence reference.

The default validation command is intentionally local and free of paid provider calls:

```powershell
.\scripts\run_controlled_validation.ps1
```

It clears every `RUN_LIVE_*` flag and `ALLOW_REAL_PROVIDER_SMOKE`, then writes one log
per test group plus `git-status.txt`, `live-flags.json`, and `summary.json` under
`tests/evidence/controlled-validation/<timestamp>/`. Use `-KeepGoing` to collect all
group failures in one run. The test groups use the local HTTP sidecar and fake provider
adapters; they never contact Meshy, Gemini, Tripo, or OpenAI.

When the Debug desktop host is started with `AIPIC_CONTROLLED_E2E=1`, the sidecar also
selects concrete offline providers for image analysis/generation and Tripo. The mock
has deterministic image bytes and a Tripo timeline of `queued -> running -> succeeded`
that materializes a managed GLB. Set `AIPIC_CONTROLLED_E2E_PROVIDER_FAILURE=1` to make
those provider calls fail safely for recovery scenarios. This mode is test-only: it is
disabled unless the explicit controlled E2E flag is present and never reads a provider
credential or opens an external-provider socket.

Set `AIPIC_CONTROLLED_E2E_HEALTH_FAILURES=<n>` for the recovery path: the next
`n` `GET /v1/health` requests return a safe 503, then health succeeds. The WebView2
launcher uses `n=2` because React Strict Mode can issue two startup probes before
the user clicks reconnect. This is the backend half of the offline/reconnect DOM
scenario.

For a Tauri/WebView2 run, start the Debug host with the explicit
`AIPIC_CONTROLLED_E2E=1`, `AIPIC_CONTROLLED_E2E_FIXTURE_ROOT`, and isolated
`AIPIC_CONTROLLED_E2E_APP_DATA` variables. Only in that Debug-only mode do the
native chooser commands return fixture-backed, one-time capabilities instead of
opening a dialog. Capture the required failure bundle from its loopback-only CDP
port with:

```powershell
python .\scripts\webview2_cdp.py --debug-port 9225 --output tests\evidence\webview2\<case>
```

The bundle contains `dom.html`, `runtime.json` (console/unhandled-rejection and
redacted fetch data), `workspace.json`, and `webview.png`. The collector redacts
bearer-like values, API keys/tokens, and Windows absolute paths before writing.

## Coverage map

| Flow IDs | Primary automated proof |
| --- | --- |
| E2E-01–05 (startup, recovery, project and workspace restore) | `tests/e2e/test_desktop_sidecar_lifecycle.py`, `tests/contract/test_workspace_state_api.py`, `desktop/frontend/src/app/AppShell.test.tsx`, `desktop/frontend/src/features/projects/ProjectLauncher.test.tsx` |
| E2E-06–08 (import, canvas, assets/trash) | `tests/integration/test_asset_import.py`, `tests/integration/test_asset_trash.py`, `tests/integration/test_asset_current_decisions.py`, `desktop/frontend/src/features/{assets,canvas}/*.test.tsx` |
| E2E-09–11 (reference/prompt/candidates/approval) | `tests/integration/test_prompt_*.py`, `tests/integration/test_image_generation_candidates.py`, `tests/security/test_approval_no_network.py` |
| E2E-12–14 (selection and multiview) | `tests/unit/test_selection_coordinates.py`, `tests/integration/selections/*`, `tests/integration/multiview/*`, `desktop/frontend/src/features/canvas/{SelectionWorkspace,MultiviewWorkspace}.test.tsx` |
| E2E-15–17 (3D approval, jobs, Agent) | `tests/integration/jobs/*`, `tests/security/test_agent_workspace.py`, `tests/integration/agent/*`, `desktop/frontend/src/features/{jobs,agent}/*.test.tsx` |
| E2E-18 (GLB preview and conversion) | `tests/integration/models/*`, `tests/integration/jobs/test_conversion*.py`, `desktop/frontend/src/features/model/ModelViewport.test.tsx` |
| E2E-19–20 (credentials, packages, diagnostics) | `tests/security/test_secret_leakage.py`, `tests/security/test_path_archive_safety.py`, `tests/contract/test_project_package_v1.py`, `desktop/frontend/src/features/{diagnostics,projects}/*.test.tsx` |

`tests/fixtures/controlled_e2e.py` creates the named `source-a.png`, `source-b.png`,
`source-c.png`, independently generated `fixture-model.glb`, corrupt GLB, normal package,
and corrupt project directory in each test's temporary directory. The archive corpus under
`tests/fixtures/project_packages/` supplies the package bytes. Provider adapters used by the
controlled suite are fakes, not a bypass for the approval gate.

`open_model_browser_preview` is safe to exercise while offline: the Rust host embeds
the fixed official `@google/model-viewer` dependency from
`desktop/src-tauri/resources/model-viewer/` and serves it from a short-lived loopback
endpoint. It does not fetch a public CDN.

## Controlled WebView2 DOM run

The default command also materializes a fresh `webview2-fixtures` directory in its
evidence output. Start a separate **Debug** Tauri instance with that directory as
`AIPIC_CONTROLLED_E2E_FIXTURE_ROOT`, a fresh `AIPIC_CONTROLLED_E2E_APP_DATA`, and
`AIPIC_CONTROLLED_E2E=1`. Its test-only native chooser seam yields only opaque
capability IDs for that fixture root; it never exposes a real local path to React.

Enable a loopback-only WebView2 DevTools port through Tauri's Debug configuration
(`app.windows[0].additionalBrowserArgs`, for example
`--remote-debugging-port=9225`), then run:

```powershell
# Creates fixtures, an isolated Vite/Tauri/WebView profile, runs the DOM check,
# and removes the test processes afterwards.
.\scripts\run_controlled_webview2.ps1
# On a new isolated fixture project only:
.\scripts\run_controlled_webview2.ps1 -CreateProject
# Includes E2E-07 canvas preview, zoom, reset, and middle-pan checks:
.\scripts\run_controlled_webview2.ps1 -CreateProject -ImageCanvas
# E2E-15 Mock Tripo approval (cancel, then approve) and E2E-18 result preview:
.\scripts\run_controlled_webview2.ps1 -CreateProject -MockTripoApproval -KeepApp
.\.venv\Scripts\python.exe scripts\run_controlled_webview2_e2e.py --debug-port 9226 --open-model-result --output tests\evidence\webview2\model-result
```

Pass a free `-DevPort` when a developer-owned Vite server already uses 14200:

```powershell
.\scripts\run_controlled_webview2.ps1 -DevPort 14203 -DebugPort 9226 -CreateProject
```

Use `-KeepApp` when running several DOM checks against the same controlled
window. The launcher preserves the already-running Vite server in either mode;
with `-KeepApp` it also leaves the Tauri host open, so follow-up checks can
attach to the same `-DebugPort` without restarting the UI. A later normal run
cleans only controlled test-host process trees before it starts.

In controlled Debug mode only, the host passes this loopback origin to the sidecar
through `AIPIC_CONTROLLED_E2E_RENDERER_ORIGIN`. Values other than
`http://127.0.0.1:<port>` are rejected, so this escape hatch cannot widen the local
API CORS boundary.

The Tauri window `dataDirectory` used by this harness is intentionally a unique
**relative** profile name. Tauri ignores absolute config paths; reusing its default
WebView2 profile would cause a second test host to inherit incompatible browser flags
and make the CDP port unavailable.

Use `-RecoverOffline` to run the one-failure/reconnect scenario against the
controlled sidecar instead of relying on a frontend-only mock.

`run_controlled_webview2_e2e.py` uses CDP DOM calls only. It verifies a healthy
application shell, and optionally creates a project through the fixture-backed
capability. It always writes a redacted DOM snapshot, runtime errors/unhandled
rejections, captured API request/status/response summaries, workspace snapshot, and
WebView screenshot; a failed scenario additionally writes `failure.txt`. Do not point
the create-project option at a normal desktop session.

Some current WebView2 runtimes do not expose a configured DevTools TCP port. The
equivalent pipe-based fallback is also DOM-only and keeps the same redacted evidence
bundle:

```powershell
.\scripts\run_controlled_webdriver.ps1 -CreateProject
.\scripts\run_controlled_webdriver.ps1 -RecoverOffline
```

It launches an isolated Debug app through the locally versioned Edge WebDriver, so it
must not be pointed at a user-owned app or project.

## Hot-update desktop UI verification

Use this method for a user-facing desktop UI change that has state, a primary action,
or visual hierarchy that cannot be trusted from a component test alone. It is the
default iteration loop for workbench layouts, canvases, task state, generated results,
and similar workflow surfaces.

The method deliberately combines three layers of proof:

| Layer | Purpose | Required proof |
| --- | --- | --- |
| Component | Prove local state and request wiring. | Smallest relevant Vitest file. |
| Build | Prove the production frontend compiles. | `pnpm --dir desktop/frontend build`. |
| Running desktop host | Prove HMR, real DOM layout, managed-asset rendering, and interaction state in WebView2. | Redacted CDP evidence bundle and semantic interaction assertions. |

### Clean application start and failure blocking

Before every desktop application or controlled-E2E start/restart, resolve and
terminate all existing **Pic2Model Studio-owned** Tauri, `pic2model-studio`, Python
sidecar, and Vite processes. Verify each command line belongs to this workspace
or its controlled/live evidence directory before terminating it; never stop an
unrelated application. A fresh controlled host then starts from an isolated
profile and fixture directory.

```powershell
# Start once; retain the controlled Debug host for follow-up changes.
.\scripts\run_controlled_webview2.ps1 -DebugPort 9237 -DevPort 14200 -CreateProject -KeepApp

# After an HMR update, attach and collect a fresh redacted evidence bundle.
.\.venv\Scripts\python.exe scripts\run_controlled_webview2_e2e.py `
  --debug-port 9237 `
  --output tests\evidence\<feature>\<timestamp>\desktop-9237
```

Only use `run_controlled_webview2.ps1` to start an isolated controlled host. After a
successful start, follow-up checks may attach to that host for HMR updates. If a
restart is needed, repeat the clean-process step first.

### Verification sequence

1. Run the smallest relevant component test, then the frontend build.
2. Attach to the retained controlled WebView2 host after HMR and navigate through the
   product's visible controls. Do not validate by rendering the component in a second
   browser or by inspecting CSS alone.
3. Exercise the actual interaction contract through CDP DOM events: enter text, select
   parameters, invoke the primary action where the controlled provider makes that safe,
   and verify completion/error/selection states. Prefer semantic assertions such as
   `aria-pressed`, `role=status`, labels, and the selected managed asset identity over
   pixel coordinates or physical mouse input.
4. For generated image or model results, wait for the managed Blob-backed element and
   verify both the result count and the selected-result transition. Do not compare Blob
   URLs from two independently rendered instances; compare the asset identity, alt
   text, or other stable semantic state instead.
5. Attach `run_controlled_webview2_e2e.py` at the end even when the custom interaction
   assertions pass. Its `dom.html`, `runtime.json`, `workspace.json`, and `webview.png`
   are the required evidence that no console error, unhandled rejection, failed fetch,
   path leak, or layout regression was hidden by the assertion.
6. When a visual target exists, inspect the target and final screenshot together. Store
   a comparison image or concise QA record beside the evidence and record only
   actionable P0/P1/P2 differences before calling the UI change complete.

### Evidence and state rules

- Store one iteration under `tests/evidence/<feature>/<timestamp>/desktop-<port>/`.
  Keep the final screenshot, DOM/runtime/workspace bundle, interaction assertion
  summary, and (when applicable) visual comparison together.
- Treat a visually plausible screenshot as insufficient if the primary action, status,
  selection, or error state was not exercised.
- Keep the test project and current user state intact. Do not delete jobs/assets or
  clear a user's prompt merely to obtain a clean screenshot. Use fixture data, restore
  a test value after an assertion, or create a new controlled fixture project.
- Keep provider validation offline by default. A controlled image/vision/Tripo request
  is allowed only with `AIPIC_CONTROLLED_E2E=1`; a real provider remains subject to
  the opt-in rules below.
- Never put raw local paths, secrets, bearer values, or provider payloads in an ad-hoc
  CDP log, assertion failure, or screenshot annotation. Use the built-in redacted
  evidence collector.

### Pass criteria for an interactive UI change

An interactive desktop UI change is ready only when all of the following are true:

- relevant component test and frontend build pass;
- the already-running controlled desktop app has received the HMR update;
- the user-visible primary workflow completes with the controlled provider, or its
  intended disabled/error state is explicitly verified;
- visible selection, loading, success, and failure states are coherent and match the
  application state rather than a stale spinner or static mock;
- a final redacted evidence bundle exists and has no runtime error, unhandled
  rejection, or unexpected failed request.
- any failed startup, CDP/WebDriver attach, or interaction attempt has been
  diagnosed and corrected; the same desktop verification has then passed.

## Paid provider smoke is opt-in

### Provider transport and submission boundary

All Provider adapters must use the shared transport failure classifier. A
connect, proxy, or connection-pool failure proves that a create request was not
submitted and must return retryable `PROVIDER_UNAVAILABLE` with
`fee_incurred=false`. A read/write/protocol failure after a paid create request
started can have an unknown remote outcome and must return
`JOB_UNKNOWN_SUBMISSION` with `safe_to_retry=false`. Do not add separate
preflight requests, random idempotency fields, or Provider-specific exception
rules to bypass this boundary.

Every mapped Provider failure must retain bounded, redacted diagnostics in
`technical_message`. HTTP responses record only the status and whether a
Provider request ID was present. Transport failures record only stable failure,
phase, cause, method, host, numeric OS error, and paid-submission flags. Never
persist exception messages, response bodies, headers, credentials, query
strings, or full URLs. This diagnostic contract is the supported way to
distinguish DNS, TCP, TLS, proxy, timeout, protocol, and Provider 5xx failures
after a controlled or user-authorized reproduction.

Live Provider clients must use the shared native-trust TLS context. Certificate
verification and hostname checks remain mandatory, with TLS 1.2 as the minimum.
Do not create adapter-local clients that fall back to a bundled CA file, disable
verification, or bypass the shared TLS policy; Windows enterprise and local
trust roots must be resolved through the OS certificate store.

Paid Tool idempotency is scoped to one explicit submission request. Replaying
the same `request_id` must return the same Tool Call/Job, while a distinct
user action with a new `request_id` must create a new approval and Job even
when Prompt, assets, parameters, and Provider profile are unchanged. Historical
or `unknown_submission` Jobs remain immutable audit records and must never
replace or block the Job created for a later explicit submission.

Paid tests require both a broad acknowledgement and a provider-specific switch. Run them
through the isolated official Python 3.14/OpenSSL 3.0.x environment; an uv-managed
OpenSSL 3.5.x runtime can fail TLS renegotiation before these Providers return a response.
For the single Tripo loop, run only after the controlled suite is green:

```powershell
.\scripts\run_real_provider_smoke.ps1 -Tripo
```

The same entry point accepts `-Meshy` and `-Gemini`, individually or in one
intentional run. Internally it still sets `ALLOW_REAL_PROVIDER_SMOKE=1` and only the
selected `RUN_LIVE_<PROVIDER>=1` flags. NanoBanana browser preflight continues to require
`RUN_LIVE_NANOBANANA=1`. Do not add these flags to a default test command or CI profile.

## Desktop-session smoke checklist

Run this only in an unlocked interactive Windows session. It is deliberately excluded
from the controlled command because native pickers, drag/drop, tray actions, notification
permissions, display capture, DPI, minimization, and lock-screen behavior cannot be
faithfully asserted through a locked WebView DOM session.

| Smoke ID | Interactive action | Pass condition |
| --- | --- | --- |
| N-01 | Create a project, open an existing project, import `source-a.png`, import valid `fixture-model.glb`, and select export destination through native dialogs. | Each request has an opaque capability ID only; corrupt GLB is recoverable. |
| N-02 | Drop a real image and GLB onto the window. | Both become managed assets; invalid drop leaves no asset. |
| N-03 | Capture all attached displays. | One physical-pixel managed PNG is imported; window hides and restores. |
| N-04 | Drive a terminal mock job. | One notification per terminal state; body contains no key, token, or path. |
| N-05 | Use tray hide, restore, and exit. | Main window state changes correctly; exit reaps sidecar. |
| N-06 | Open managed GLB in the default browser while offline. | Browser preview loads the loopback GLB and bundled model-viewer without a public CDN. |
| N-07 | Check 100%, 150%, and 200% DPI; minimize and restore. | Workspace remains usable and layout does not overlap the play area. |
| N-08 | Submit a controlled long job, lock Windows 2–5 minutes without sleeping, then unlock. | Recorded job advances as expected, is submitted once, and sidecar has no restart loop. |

For N-08, record the job ID and database state before locking and after unlocking. Locking
is not sleeping: a sleep/power-policy test is separate and may legitimately pause polling.

Separately exercise the live Tripo approval loop: cancel first (zero job/asset/request),
then approve once, record approval/job/model values without secrets, poll to a terminal
state, preview the managed GLB, request FBX conversion, export/reopen the project, and
archive the redacted evidence.

Locking is not sleeping: power-policy behavior belongs to a separate test.
