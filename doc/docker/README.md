# Docker Strategy MVP

This document tracks the containerization plan for the CHL API runtime. The goal
is to keep the MCP server lightweight (still bootstrapped via `uv sync` +
`pyproject.toml`), while the API server runs inside vendor-specific containers
that encapsulate GPU drivers, llama-cpp builds, and model assets.

## Platform Matrix

| Host GPU / Mode | Delivery | Dependency file | Notes |
|-----------------|----------|-----------------|-------|
| CPU-only (validation, CI) | `docker/Dockerfile.cpu` | `requirements_cpu.txt` | No GPU libs; embeds smoke test to ensure embeddings/reranker work on CPU. |
| NVIDIA / CUDA 12.5 | `docker/Dockerfile.cuda-mvp` (existing) | `requirements_docker.txt` | Uses `nvidia/cuda:12.5.0-devel-ubuntu22.04`, compiles `llama-cpp-python` with `GGML_CUDA`. Run with `--gpus all`. |
| AMD / ROCm 6.x | `docker/Dockerfile.rocm` (TBD) | `requirements_docker.txt` + ROCm extras | Base on `rocm/dev-ubuntu-22.04`, compile with HIP (`-DGGML_HIPBLAS=on`). Container needs `/dev/kfd`/`/dev/dri` devices. |
| Intel GPU (oneAPI / Level Zero) | `docker/Dockerfile.intel` (TBD) | `requirements_docker.txt` + oneAPI extras | Base on Intel oneAPI runtime, compile with SYCL (`-DGGML_SYCL=on`). Requires `--device /dev/dri`. |
| Apple Silicon / Metal | Native macOS install (no Docker) | `requirements_apple.txt` | Docker Desktop cannot expose Metal; provide a shell script that builds a venv, installs the Metal wheel, runs smoke test. |

Shared artifacts across all modes:
- `scripts/gpu_smoke_test.py` – downloads the Qwen embedding & reranker GGUFs,
  loads them with `llama-cpp`, and produces embeddings + a reranker response.
  Every container (and the Apple script) must run this as part of installation.
- `/opt/chl/data` volume – persists SQLite + FAISS. Containers mount the host
  `data/` directory so MCP clients see identical state via HTTP.

## Runtime Separation Goals

1. **API server containers** own:
   - GPU detection + driver checks
   - Embedding/reranker downloads
   - Queue workers, HTML UI, metrics
   - HTTP endpoints for MCP + browsers

2. **MCP server**:
   - Never opens SQLite/FAISS directly
   - Uses `src/api_client.CHlAPIClient` for all CRUD/search operations
   - Ships via `uv sync` (pyproject dependencies only)

3. **Shared code** lives in `src/common/` (config, storage schema, DTOs).
   Mode-specific logic moves into `src/cpu/`, `src/gpu/`, and `src/mcp/`.

## Build / Run Recipes

### NVIDIA CUDA (current MVP)
```bash
# Build once per host architecture
docker build -f docker/Dockerfile.cuda-mvp -t chl-api-cuda .

# Run with GPU access + bind the local data directory
docker run --rm --gpus all \
  -v "$(pwd)/data:/opt/chl/data" \
  -e HF_TOKEN=... \
  -p 8000:8000 \
  chl-api-cuda
```
Container entrypoint: `python scripts/gpu_smoke_test.py` (ensures CUDA offload).
Next iteration will start `uvicorn src.api_server:app` after the smoke test.

### CPU Validation
```bash
docker build -f docker/Dockerfile.cpu -t chl-api-cpu .
docker run --rm -p 8000:8000 chl-api-cpu
```
Useful for CI and hosts without GPUs.

### AMD / Intel Roadmap
- Copy `Dockerfile.cuda-mvp` structure.
- Swap base images (`rocm/dev-ubuntu-22.04` / Intel oneAPI).
- Adjust `CMAKE_ARGS` (HIPBLAS/SYCL) and runtime entrypoints.
- Update smoke test env vars if ROCm/oneAPI require different device settings.

### Apple Silicon Script
```
python3.11 -m venv .venv-metal
source .venv-metal/bin/activate
python -m pip install --upgrade pip
PIP_EXTRA_INDEX_URL=https://abetlen.github.io/llama-cpp-python/whl/metal \
  python -m pip install -r requirements_apple.txt
python scripts/gpu_smoke_test.py
```
Documented in `doc/apple/README.md` (TBD).

## Open Tasks
### Phase 1 – Documentation & Baseline
- Capture overall strategy (this document).
- Keep MCP bootstrap via `uv sync` unchanged; validate CUDA MVP container + smoke test.

### Phase 2 – Dependency Split & CPU Container
- Create `requirements_cpu.txt`, `requirements_docker.txt`, `requirements_apple.txt`.
- Ship `docker/Dockerfile.cpu` that runs the API server + smoke test without GPU deps.
- Ensure MCP can target the CPU container over HTTP (no direct SQLite access).

### Phase 3 – Source Tree Restructure
- Move shared config/storage into `src/common/`.
- Add `src/runtime_cpu/` & `src/runtime_gpu/` built via a `ModeRuntime` factory.
- Update MCP modules to depend only on `src/common/` + `src/mcp/` (drop repository imports).

### Phase 4 – GPU Containers
- Extend CUDA Dockerfile to run the API server after the smoke test.
- Add `docker/Dockerfile.rocm` and `docker/Dockerfile.intel` with platform-specific build flags.
- Integrate `scripts/gpu_smoke_test.py` in each image and document run commands.

### Phase 5 – Apple Silicon Native Flow
- Provide `requirements_apple.txt` + a shell script to install the Metal wheel.
- Document macOS-native setup (no Docker) alongside GPU containers.

### Phase 6 – MCP Hardening & Docs
- Remove remaining direct DB/FAISS usage from MCP (HTTP-only via `src/api_client`).
- Update README/settings to explain: run API container (per HW), then run MCP separately.

Once these are in place, MCP clients will always hit the API server over HTTP,
regardless of whether that server runs on bare metal, Docker CPU, or a
GPU-specific container. HOMES: doc/docker/README.md (this file).
