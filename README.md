# AIPicToModel

AIPicToModel is a Windows desktop workbench for turning image concepts into
managed 3D-production assets. The desktop shell is built with Tauri and React;
the local sidecar is implemented in Python.

## Requirements

- Windows 10 or later with WebView2
- Python 3.14
- Node.js with pnpm
- Rust stable toolchain
- Blender for local GLB-to-FBX conversion

## Install dependencies

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e . --group dev
pnpm --dir desktop install
pnpm --dir desktop/frontend install
```

## Validate

The controlled validation suite uses only offline fixtures and mock providers.

```powershell
.\scripts\run_controlled_validation.ps1
```

## Build

Build the Python sidecar and frontend, then create the Tauri bundle:

```powershell
pnpm --dir desktop install
pnpm --dir desktop/frontend install
pnpm --dir desktop build
pnpm --dir desktop tauri build
```

Real provider smoke tests are opt-in and require the explicit safety flags
documented in `AGENTS.md` and `docs/controlled-validation.md`.
