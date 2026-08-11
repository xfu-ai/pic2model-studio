# Offline model and portable-artifact distribution

## Distribution boundary

The source repository at `xfu-ai/pic2model-studio` contains all application
source, tests, build scripts, lockfiles, dependency documentation, and
third-party notices. It does not commit the assembled portable runtime or the
offline model stores.

Model weights are obtained from their original upstream distribution instead
of being mirrored in this source repository. This avoids both unnecessary
redistribution and GitHub LFS's hard 2 GiB per-object limit.

- Ollama/Qwen3-VL: the [Ollama qwen3-vl registry](https://ollama.com/library/qwen3-vl).
- TripoSR weights: the [stabilityai/TripoSR Hugging Face repository](https://huggingface.co/stabilityai/TripoSR).
- DINO configuration: the [facebook/dino-vitb16 Hugging Face repository](https://huggingface.co/facebook/dino-vitb16).
- TripoSR source and stable-diffusion.cpp runtime: their pinned GitHub sources
  recorded in `workers/triposr/worker-manifest.json` and
  `resources/local-models/README.md`.
- Z-Image-Turbo weights and the Qwen GGUF text encoder: use the approved
  immutable upstream artifact URL and SHA-256 recorded for the release.

The complete `Pic2Model-Studio/` directory remains a generated release
artifact. When direct-download distribution is required, host that directory
with its `SHA256SUMS.txt` in an artifact service that permits files above 2
GiB, such as a maintainer-owned Hugging Face repository or object storage.

The two files that cannot be uploaded to GitHub LFS are
`z_image_turbo-Q3_K.gguf` and `Qwen3-4B-Instruct-2507-Q4_K_M.gguf`. Keep their
original filenames and hashes. Do not split or recompress them in a way that
changes the runtime layout.

## Consumer procedure

1. Download the versioned external artifact and verify its `SHA256SUMS.txt`.
2. Extract `Pic2Model-Studio/` as a single directory, retaining its `resources`
   sibling layout.
3. Double-click `pic2model-studio.exe`.

The package is self-contained after extraction. It does not require Python,
Node.js, Rust, or a Git checkout.

## Publishing status

The source repository is publishable independently. This checkout has no
configured external-artifact credentials, so it does not claim a hosted copy of
the generated portable directory.
