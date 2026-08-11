# Bundled local model resources

This directory is the canonical, Git LFS-managed source for the desktop app's
offline model runtimes. A complete checkout after `git lfs pull` supports all
three local profiles without manually setting capability environment variables.

## Layout

- `ollama/runtime` and `ollama/models`: Ollama 0.32.5 with `qwen3-vl:8b`.
- `z-image-turbo/runtime`: stable-diffusion.cpp Windows CUDA runtime from commit
  `db99efdd` (release `master-810`).
- `z-image-turbo/models`: the validated Z-Image-Turbo Q3_K diffusion model,
  Qwen3 4B Q4_K_M text encoder, and FLUX VAE.
- `triposr/python`: portable CPython 3.11.9 with the locked TripoSR/PyTorch
  dependencies installed into its own `Lib/site-packages`.
- `triposr/source`: TripoSR source at commit
  `107cefdc244c39106fa830359024f6a2f1c78871`, without nested Git metadata.
- `triposr/model`: the offline `stabilityai/TripoSR` weights plus the pinned
  DINO configuration cache required by the worker.

The desktop resolves these paths internally. Native paths never cross the
renderer capability boundary. Local inference remains offline and does not
contact or charge an external Provider.

Do not replace individual weights or native binaries without updating the
model ledger, third-party notices, and portable SHA-256 manifest and rerunning
the controlled validation suite.
