# Pic2Model Studio portable release

The assembled `Pic2Model-Studio/` directory is an external release artifact;
it is deliberately not committed to the source repository. See
[`docs/model-distribution.md`](../docs/model-distribution.md) for the model
distribution location and integrity requirements.

Open `pic2model-studio.exe` directly. Do not move the executable away from its
adjacent `resources` directory: together they form the complete portable
application. No Python, Node.js, Rust, or installer is required.

## Included offline capabilities

The package includes the desktop host, packaged Python sidecar, ONNX Runtime,
Real-ESRGAN, Ollama with Qwen3-VL, stable-diffusion.cpp with Z-Image-Turbo, and
the isolated TripoSR runtime and weights. The application resolves these
resources from this directory; no manual model-path configuration is required.

Windows 10/11 x64 with WebView2 Runtime is required. Blender is optional and is
only needed when exporting a model to formats such as FBX. Online Provider
features require the user's own credentials and network connection.

## Integrity check

`SHA256SUMS.txt` lists every release file except itself. From this directory,
run the following PowerShell command before distribution or after transfer:

```powershell
Get-Content .\SHA256SUMS.txt | ForEach-Object {
    $hash, $path = $_ -split '  ', 2
    if ((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $hash) {
        throw "Checksum mismatch: $path"
    }
}
```

`DEPENDENCIES.md` describes the included runtime boundary and system
requirements. `THIRD_PARTY_NOTICES.md` contains licensing and attribution
notices. `BUNDLED_COMPONENTS.txt` records the packaged sidecar contents.
