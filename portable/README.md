# Portable application

Double-click `Pic2Model-Studio\pic2model-studio.exe` to run the application. Keep the
`resources` directory beside the executable; it contains the self-contained
Python sidecar used by the desktop host.

The sidecar embeds the application model, ONNX Runtime native libraries, Python
runtime, and Python package dependencies. `SHA256SUMS.txt` records the exact
contents of the portable distribution.

Run `scripts\build_portable.ps1` from the repository root to rebuild this
directory without producing an installer.
