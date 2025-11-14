# GPU Auto-Detection Backlog

**Updated:** 2025-11-14  
**Status:** 🟡 Planned – design approved, implementation pending

---

## Why This Matters

Today anyone who wants GPU acceleration must know which llama-cpp-python wheel to install and how many layers to offload. This leads to failed installs, silent CPU fallbacks, and support questions. We want a single command (`uv run python scripts/setup-gpu.py`) to detect the host GPU, install the right wheel, and confirm offload works before the MCP server starts.

---

## Current State (2025-11-14)

- `scripts/setup-gpu.py` focuses on DB/model/bootstrap tasks only. It never inspects the hardware, never switches wheel URLs, and never checks whether llama-cpp-python can offload to a GPU.
- ML extras are installed via `uv sync --extra ml`, which always pulls the default wheel (CPU-only on most hosts).
- `src/embedding/client.py` and `src/embedding/reranker.py` rely on the caller to pass `n_gpu_layers`; the scripts set it to `0` (CPU). There is no call to `llama_supports_gpu_offload()`.
- Logs do not surface whether the model is running on CPU vs GPU, so users cannot tell when the fallback happens.

Result: the documentation promises "automatic" GPU experience, but the repository still requires manual configuration.

---

## Detection Algorithm & Priority

1. **Explicit override first** – CLI flag `--gpu-backend` or env `CHL_GPU_BACKEND` short-circuits detection (values: `cuda`, `rocm`, `metal`, `cpu`).
2. **Apple Metal** – `platform.system() == "Darwin"` and `platform.machine() == "arm64"`. Intel Macs (Darwin x86_64) should fall back to CPU and log that Metal is unsupported.
3. **CUDA** – Evaluate in order:
   - `nvidia-smi --query-gpu=name --format=csv,noheader`
   - If missing (common in Docker), check `/proc/driver/nvidia/version`, `/dev/nvidia0`, or `os.environ.get("NVIDIA_VISIBLE_DEVICES")`.
   - As a last resort, attempt to import `ctypes.CDLL("libcuda.so")`.
4. **ROCm** – Run `rocminfo`/`rocm-smi`, else look for `/dev/kfd`, `/dev/dri/render*`, or `HIP_VISIBLE_DEVICES`.
5. **CPU** – No GPU signals found.

**Multi-GPU / hybrid hosts:** pick the highest-priority backend that has a working toolchain installed (Metal → CUDA → ROCm → CPU). In heterogeneous racks (e.g., NVIDIA + AMD), document that we choose the "best available" rather than attempting split installs. Future work: allow `--gpu-backend=auto-rocm-first` etc.

| Scenario | Behavior |
|----------|----------|
| Docker + NVIDIA GPU | Probe `/dev/nvidia*` plus `NVIDIA_VISIBLE_DEVICES` before attempting `nvidia-smi`; emit actionable instructions when both are absent. |
| Multiple GPU types | Honor `CHL_GPU_PRIORITY` (default `metal,cuda,rocm,cpu`) to let ops prefer a backend explicitly while keeping a deterministic fallback. |
| Container without `nvidia-smi` | Attempt CUDA runtime load via `ctypes.CDLL("libcuda.so")` or NVML Python bindings before declaring GPU unavailable. |

---

## Container & Virtualized Environments

| Scenario | Detection considerations |
|----------|-------------------------|
| Docker + NVIDIA GPU | Prefer `/dev/nvidia*` presence and `NVIDIA_VISIBLE_DEVICES` over `nvidia-smi` (often not mounted). If both absent, emit guidance to install nvidia-container-toolkit. |
| Docker + AMD ROCm | Check `/dev/kfd`, `/dev/dri/render*`, `ROCM_VISIBLE_DEVICES`. `rocminfo` may not be available; fallback to HIP runtime probe. |
| Container without tooling | Attempt to load CUDA runtime via `ctypes.CDLL("libcuda.so")` or ROCm's `libhip_hcc.so`. |
| WSL2 | Use `nvidia-smi` when available; otherwise parse `dxdiag` output via PowerShell invoked from bash. |
| Bare-metal CI | Respect `CHL_GPU_BACKEND=cpu` to avoid touching host GPUs. |

Add automated tests that mount fake device nodes into containers to ensure detection logic works without privileged access.

---

## Wheel Selection & Compatibility Matrix

| Backend | Versions we support | Wheel suffix mapping | Fallback strategy |
|---------|--------------------|----------------------|-------------------|
| CUDA | 11.8, 12.0, 12.1, 12.2, 12.4 | `cu118`, `cu120`, `cu121`, `cu122`, `cu124` | If host reports newer version, fall back to highest known (`cu124`) with warning. |
| ROCm | 5.6, 5.7, 6.0 | `rocm5.6`, `rocm5.7`, `rocm6.0` | If unsupported version detected, recommend CPU wheel or prompt for manual override. |
| Metal | macOS 13+ on Apple Silicon | `metal` | Fail fast on Intel Macs, instruct to use CPU wheel. |
| CPU | Any | `cpu` | Default when no GPU detected or install fails. |

Document where these wheels are hosted and who mirrors them (e.g., consider caching in our artifact registry to avoid external outages).

---

## Verification & Fallback Behavior

1. **Installation phase**
   - Run `uv pip install llama-cpp-python --extra-index-url <wheel_url>`.
   - On failure: log stderr, fall back to CPU wheel automatically **only after** the user sees the failure reason.
2. **Verification phase**
   - Import `llama_cpp` and call `llama_supports_gpu_offload()`.
   - Run a tiny inference: load a 7B Qwen GGUF, set `n_gpu_layers=-1`, encode "test" text, measure time.
   - If `llama_supports_gpu_offload()` returns `False` or inference fails with GPU errors:
     - Retry once with `n_gpu_layers=0` (CPU) to ensure functionality.
     - Mark backend as `degraded` and prompt user whether to keep CPU fallback or abort.
3. **Telemetry**
   - Record `{ "backend": "cuda", "version": "12.4", "wheel": "cu124", "verified": true, "diagnostics": [] }` in `data/gpu_state.json`.
   - Emit structured log `gpu_detection` with detection method, verification status, and fallback reason if applicable.

---

## State Management & Overrides

- Persist detection output to `data/gpu_state.json` (separate from model selection):
  ```json
  {
    "backend": "cuda",
    "version": "12.4",
    "wheel": "cu124",
    "verified_at": "2025-11-14T17:31:00Z",
    "status": "verified",
    "last_error": null
  }
  ```
- Add `--force-detect` flag (and `CHL_FORCE_GPU_DETECT=1`) to ignore cached state after hardware change or manual wheel install.
- If the user installs a different wheel manually, comparing `pip show llama-cpp-python` against cached metadata should trigger re-verification.

---

## Model Selection Schema

`data/model_selection.json` remains the shared contract between setup scripts, UI, and runtime (`src/config.py`, `src/api/routers/ui.py`). The schema we rely on is:

```json
{
  "embedding_repo": "Qwen/Qwen3-Embedding-0.6B-GGUF",
  "embedding_quant": "Q8_0",
  "reranker_repo": "Mungert/Qwen3-Reranker-0.6B-GGUF",
  "reranker_quant": "Q4_K_M",
  "last_updated": "2025-11-14T17:31:00Z",
  "set_by": "setup-gpu.py"
}
```

- GPU detection never mutates this file directly; it only updates `data/gpu_state.json`. `model_selection.json` changes occur when the user picks models via UI/API or when `scripts/setup-gpu.py --apply-model-defaults` is run after a verified GPU install.
- `--force-detect` re-runs hardware probes and, if necessary, rewrites `model_selection.json` so that GPU-only model defaults (e.g., larger reranker) stay in sync with hardware reality.
- Manual edits to `model_selection.json` trigger a post-save hook (UI writes already do this) that compares the persisted backend with `gpu_state.json`; mismatches force a verification cycle so stale caches do not leave us in a broken state.

---

## Runtime Defaults & Logging

- Provide `auto_gpu_layers()` helper that checks `CHL_N_GPU_LAYERS` override, then GPU state file, then runtime probe.
- Embedding and reranker clients should log a single line: `embedding_backend=llama.cpp offload=cuda n_gpu_layers=-1 status=verified`.
- Add `/health` payload field `{ "gpu": { "backend": "cuda", "verified": true } }` so frontends can surface status.

---

## Rollback & Recovery

- If GPU install + verification passes but later inference fails (e.g., driver update), `auto_gpu_layers()` should detect exceptions and:
  1. Log the failure with traceback.
  2. Temporarily set `n_gpu_layers=0` and proceed so ingestion jobs never block.
  3. Mark `gpu_state.json.status="needs_attention"` so ops can investigate.
- Provide `scripts/setup-gpu.py --rollback cpu` to reinstall CPU wheel and clean cached GPU artifacts.

---

## Test / Validation Matrix

| Scenario | Host | Expected behavior |
|----------|------|-------------------|
| Apple Silicon laptop | macOS 15, M3 | Detect Metal, install Metal wheel, report `GPU offload: available`. |
| Intel MacBook Pro | macOS 13, Intel | Skip Metal detection, install CPU wheel, warn that Metal requires Apple Silicon. |
| Windows workstation | RTX 4090 + CUDA 12.4 | Detect CUDA via `nvidia-smi`, install `cu124` wheel, set `n_gpu_layers=-1`. |
| Docker container (no nvidia-smi) | RTX 3090 passthrough | Detect `/dev/nvidia0`, install CUDA wheel, verification succeeds. |
| Linux ROCm box | Radeon VII, ROCm 6.0 | Detect ROCm via `/dev/kfd`, install ROCm wheel, offload enabled. |
| CPU-only VM | No GPU | Install CPU wheel, log warning that offload unavailable. |
| Override scenario | `CHL_GPU_BACKEND=cpu` on NVIDIA host | Skip detection, install CPU wheel, log that override applied. |
| Verification failure | Simulate broken CUDA driver | Install CUDA wheel, verification fails, automatic rollback to CPU documented in logs. |

Each scenario should execute a minimal embedding request via `EmbeddingClient.encode()` to prove llama-cpp works end-to-end and assert telemetry entries.

---

## Success Criteria

- `scripts/setup-gpu.py --detect-only` outputs backend, version, wheel, and verification status within 5 seconds on supported hardware.
- Fresh installs automatically pick the correct wheel ≥95% of the time across CI matrix.
- `EmbeddingClient` defaults to GPU offload when available without user configuration.
- Ops dashboards display GPU status derived from `/health` endpoint.
- Rollback from GPU to CPU completes in <1 minute via documented command.

---

## Issue Tracking

- [tyouritsugun/curated-heuristic-loop#241](https://github.com/tyouritsugun/curated-heuristic-loop/issues/241) – GPU detection & wheel install automation.
- [tyouritsugun/curated-heuristic-loop#242](https://github.com/tyouritsugun/curated-heuristic-loop/issues/242) – runtime auto-offload configuration + verification.
- [tyouritsugun/curated-heuristic-loop#243](https://github.com/tyouritsugun/curated-heuristic-loop/issues/243) – telemetry, rollback tooling, and ops UX.

---

## Open Questions / Next Steps

1. Should we expose a hook so ops can prefer ROCm over CUDA on hybrid hosts (e.g., `CHL_GPU_PRIORITY="rocm,cuda,metal"`)?
2. Do we need to support OpenCL/Vulkan backends, or is CUDA/ROCm/Metal sufficient for the 2025 roadmap?
3. Who owns long-term maintenance of custom wheel URLs (especially the community ROCm builds)? Consider mirroring to our artifact registry.
4. Link implementation work to issues: `DEV-241` (detection + wheel install), `DEV-242` (runtime auto offload), `DEV-243` (telemetry + rollback tooling).
