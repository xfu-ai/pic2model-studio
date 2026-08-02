# AIPicToModel

AIPicToModel is a Windows desktop workbench for turning image concepts into
managed 3D-production assets. The desktop shell is built with Tauri and React;
the local sidecar is implemented in Python.

## Run the portable application

After cloning with Git LFS enabled, open:

```text
portable\AIPicToModel\aipic-to-model.exe
```

The portable directory contains the desktop executable and the packaged Python
sidecar. The sidecar embeds its Python runtime, Python packages, ONNX Runtime
native libraries, and the bundled Real-ESRGAN model. Python, Node.js, and Rust
are not required to run the portable build. Windows WebView2 is an operating
system prerequisite. Blender is optional and is used only for local FBX export.

## Development requirements

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

## Build the portable application

The repository does not publish an installer. Rebuild the directly runnable
portable directory with:

```powershell
.\scripts\build_portable.ps1
```

The script packages the sidecar, builds Tauri with `--no-bundle`, recreates
`portable\AIPicToModel`, and writes `SHA256SUMS.txt` for all shipped files.

Real provider smoke tests are opt-in and require the explicit safety flags
documented in `AGENTS.md` and `docs/controlled-validation.md`.
