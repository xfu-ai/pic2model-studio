# TripoSR isolated worker

This environment is intentionally separate from the packaged Python 3.14 sidecar.
Provision Python 3.11, install `requirements.lock`, and check out both upstream commits
recorded in `worker-manifest.json`. Build `torchmcubes` from the pinned source revision.
A full CUDA Toolkit produces a CUDA extension; a CPU-only extension is also valid and
causes only marching cubes to fall back to CPU while the TripoSR network stays on CUDA.

Download the pinned `stabilityai/TripoSR` `config.yaml` and `model.ckpt` ahead of time.
The model directory must also contain the pinned `facebook/dino-vitb16/config.json` in
the Hugging Face cache layout under `huggingface-cache/`. The Host points the isolated
process at that cache. Runtime inference is forced offline and will not download weights,
the DINO configuration, or background-removal models.

Bind the Host-owned capability slots with these environment variables:

- `AIPIC_TRIPOSR_PYTHON`: isolated environment's Python executable.
- `AIPIC_TRIPOSR_RUNNER`: pinned upstream `run.py`.
- `AIPIC_TRIPOSR_MODEL`: local model directory containing `config.yaml` and `model.ckpt`.

The Host invokes a fixed single-image command, composites alpha over neutral gray, passes
`--no-remove-bg`, and accepts only `output/0/mesh.glb` after GLB authenticity checks.
