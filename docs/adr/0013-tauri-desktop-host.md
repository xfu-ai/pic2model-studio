# ADR-013: Tauri 2 desktop host and Python sidecar lifecycle

## Status

Accepted — B04-01.

## Decision

The desktop product uses **Tauri 2 + React 18 + the existing Python local
service as a sidecar**. Tauri owns application lifetime, native file/directory
selection, window state, notifications, and the one-time session handoff to
the renderer. Python continues to own B01–B03 business rules, SQLite access,
Tool execution, jobs, Agent runs, and provider requests.

At launch, the Rust host creates a high-entropy token in memory, starts the
sidecar with that token, and waits for its non-secret `ready` message. The
sidecar binds only `127.0.0.1:0`, so the operating system selects a fresh port;
the chosen port is reported to the host but is never persisted. The host gives
the renderer the base URL and bearer token only through a Tauri IPC command
after the WebView is ready. It does not write either value to files, URLs,
localStorage, diagnostic exports, or logs.

The API continues to require the Tauri origin and bearer token. A failed health
check results in a recoverable launch error rather than repeated respawning.
On application exit, the host asks the sidecar to stop and waits for it; the
sidecar owns durable B01–B03 checkpointing.

## Alternatives rejected

- **Electron:** unnecessary bundled Chromium footprint and a wider native
  permission surface for an application whose backend is already Python.
- **Browser launcher:** cannot provide the required native capability boundary,
  reliable sidecar cleanup, or packaged offline desktop experience.
- **Renderer-owned sidecar/API:** would expose arbitrary process/path control
  to web content and collapse the required host/business boundary.

## Security invariants

1. The service can bind only IPv4 loopback, never a LAN interface or fixed port.
2. Renderer commands accept IDs or host-issued one-time file tokens, never an
   arbitrary path or shell command.
3. Provider credentials remain in the backend keyring/environment boundary;
   neither Rust nor React reads or stores them.
4. A sidecar crash is surfaced as recoverable state. The host has no automatic
   infinite restart loop.
