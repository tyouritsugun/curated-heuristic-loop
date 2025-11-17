# Phase B: Diagnostics & Environment Guardrails

## Overview

Phase B introduces a CLI-based environment diagnostics workflow that validates system readiness before API server installation. This phase moves GPU detection and wheel installation guidance from the web UI to a prerequisite script that users must run successfully before proceeding with setup.

## Objectives

1. **CLI-First Environment Validation**: Provide a standalone script that checks GPU availability, driver versions, VRAM, and llama-cpp-python compatibility.
2. **VRAM-Based Model Auto-Selection**: Automatically recommend optimal model sizes based on detected hardware capabilities.
3. **Dynamic Wheel Requirements**: Prefer reading package requirements directly from wheel metadata instead of duplicating low-level compatibility flags (e.g., nvcc/driver versions). Wheel *variants* (e.g., `metal`, `cu125`) may be hardcoded; low-level toolchain details must not be.
4. **LLM-Assisted Troubleshooting**: Generate structured prompts for ChatGPT/Claude when environment checks fail.
5. **UI Cleanup**: Remove duplicate environment setup functionality from web UI while preserving model management features.

## Architecture Principles (Phase 0 Compliance)

### CPU/GPU Code Isolation

Phase B **preserves** the CPU/GPU separation established in Phase 0:

- **GPU-specific logic**: All GPU detection, VRAM queries, and wheel metadata helpers remain in `src/api/services/gpu_installer.py` (API-layer only; not moved to `src/common/`).
- **CPU mode**: Requires no GPU dependencies; `check_api_env.py` treats the environment as CPU-only **only when no supported GPU backends are detected**.
- **No cross-imports**: The diagnostic script imports only from:
  - `src.api.services.gpu_installer` (a narrow, explicit exception to ADR-003 for setup/diagnostic scripts)
  - `src.common.config` (shared configuration)
  - Python stdlib

### Boundary Rules

Under ADR-003’s boundaries, this phase introduces a **narrow exception**:
- `scripts/check_api_env.py` may import from `src.api.services.gpu_installer` because it is a setup/diagnostic script; no other `src.api.*` imports are allowed.
- It **must only run with the API server stopped** (pre-installation phase). If the configured API base URL responds (e.g., `http://127.0.0.1:8000/`), the script prints a strong warning and exits with code 1 (optional `--force` flag may override in emergencies).
- It **never acts as a general orchestration tool** (one-time validation only).
- All GPU-specific code stays in `src/api/services/gpu_installer.py` (not moved to common).

## Design

### 1. Environment Check Script (`scripts/check_api_env.py`)

#### Responsibilities

**Detection Phase:**
- Detect GPU backend (Metal/CUDA/ROCm/CPU) using `gpu_installer.detect_gpu_backends()`
- Query VRAM for each detected backend:
  - **NVIDIA**: Parse `nvidia-smi --query-gpu=memory.total --format=csv`
  - **AMD**: Parse `rocm-smi --showmeminfo`
  - **Apple Metal**: Estimate from unified memory (`sysctl hw.memsize * 0.7`)
  - **CPU**: Report system RAM (not VRAM)
- Check driver versions and prerequisite toolchains (CUDA Toolkit, Metal CLT, ROCm SDK)
- Detect Python version, platform, and architecture

**Analysis Phase:**
- Run `prerequisite_check(gpu_state)` to validate environment readiness
- Determine recommended wheel suffix via `recommended_wheel_suffix(gpu_state)`
- If llama-cpp-python already installed, verify with `verify_llama_install(gpu_state)`
- Fetch wheel metadata from repository to validate compatibility (via `get_wheel_metadata()`). If this step fails due to network or index issues, the overall check fails (strict network requirement).

**Recommendation Phase:**
- Based on detected VRAM, recommend model sizes using the same repos/quantizations as `scripts/setup-gpu.py`:
  - **≥ 6GB VRAM**: 4B models (Q4_K_M quantization) for both embedding and reranker (e.g., `Qwen/Qwen3-Embedding-4B-GGUF` + `Mungert/Qwen3-Reranker-4B-GGUF`, both Q4_K_M).
  - **≥ 2GB, < 6GB VRAM**: 0.6B models (Q8_0 quantization) for both (e.g., `Qwen/Qwen3-Embedding-0.6B-GGUF` + `Mungert/Qwen3-Reranker-0.6B-GGUF`, both Q8_0).
  - **< 2GB VRAM**: 0.6B embedding (Q8_0) + 0.6B reranker (Q4_K_M, lighter) to fit minimal hardware.
- Save recommended selection to `data/model_selection.json` (same format and keys as used by `scripts/setup-gpu.py` and the GPU model management UI).

**Output Phase:**
- **Success case (exit 0)**: Print summary with detected backend, VRAM, recommended models, and green checkmarks. If any supported GPU backend (Metal/CUDA/ROCm) passes prerequisites, the script treats the environment as GPU-capable and recommends GPU mode (`CHL_SEARCH_MODE=auto`) in its output, regardless of the current value.
- **Failure case (exit 1)**: Generate LLM support prompt via `build_support_prompt()` and save to `data/support_prompt.txt`. Network failures when fetching wheel metadata are treated as hard failures (no offline mode for Phase B diagnostics).

#### LLM Support Prompt Structure

When checks fail, the generated prompt includes:
- **System Context**: OS, Python version, detected GPU hardware, driver versions
- **Issue Summary**: Which prerequisites failed (missing drivers, outdated CUDA, etc.)
- **Wheel Requirements**: Dynamically fetched from package metadata (never hardcoded)
- **Diagnostic Logs**: Relevant command outputs (nvidia-smi, rocm-smi, etc.)
- **Official Resources**: Links to NVIDIA/AMD/Apple driver download pages
- **Reproduction Steps**: Exact commands the script ran for diagnosis
- **Goal Statement**: "Help me install the necessary drivers and dependencies to satisfy these requirements"

The user copies this prompt to ChatGPT/Claude to get step-by-step remediation guidance.

### 2. Extended gpu_installer.py Functions

#### New Functions to Implement

**VRAM Detection:**
- `get_vram_info() -> Dict[str, Any]`: Returns `{"backend": str, "vram_gb": float, "method": str}`
- Calls backend-specific helpers: `_get_nvidia_vram()`, `_get_amd_vram()`, `_get_metal_vram()`
- Handles failures gracefully (returns `None` if VRAM cannot be determined)

**Prerequisite Validation:**
- `prerequisite_check(gpu_state: Dict) -> Dict[str, Any]`: Returns `{"status": "ok"|"warn"|"error", "message": str, "issues": List[str]}`
- Checks:
  - Driver presence and version
  - Required libraries (libcuda.so, Metal.framework, HIP runtime)
  - Compiler toolchains if wheel build is needed (CMake, gcc/clang)
  - Python version compatibility with wheel

**Wheel Suffix Recommendation:**
- `recommended_wheel_suffix(gpu_state: Dict) -> Optional[str]`: Returns wheel suffix (e.g., "cu125", "metal", "rocm6.0") or `None` if CPU
- Already partially implemented via `determine_cuda_wheel()` and `determine_rocm_wheel()`
- Extend to return final suffix string

**Installation Verification:**
- `verify_llama_install(gpu_state: Dict) -> Tuple[bool, str]`: Attempts to import llama_cpp and verify GPU backend
- Returns (success: bool, log: str with any error messages)
- Runs minimal inference test if possible (load tiny GGUF, check backend is correct)

**Metadata Fetching:**
- `get_wheel_metadata(backend: str, suffix: str) -> Dict[str, Any]`: Fetch wheel metadata from repository
- Queries `https://abetlen.github.io/llama-cpp-python/whl/{backend}/` index
- Parses wheel filename to extract Python version requirements, platform tags
- Returns `{"python_requires": str, "platforms": List[str], "url": str, "size_mb": float}`

**Support Prompt Generation:**
- `build_support_prompt(gpu_state: Dict, prereq: Dict, verify_log: Optional[str]) -> str`: Generate LLM troubleshooting prompt
- Includes all diagnostic information, wheel requirements, and official resource links
- Template-based generation (could use Jinja2 or string formatting)
These helpers are consumed exclusively by the CLI diagnostics script. The web UI no longer calls `gpu_installer` directly; it reflects configuration and model selection state derived from `data/model_selection.json` and environment variables, and points users to `python scripts/check_api_env.py` for hardware diagnostics and troubleshooting prompts.

### 3. README.md Workflow Update

#### New Pre-Installation Section (before "Quick Start")

Add a mandatory step:

```
## Step 0: Verify Your Environment

Before installing the API server, validate your system is ready:

$ python scripts/check_api_env.py

This script checks:
- GPU hardware detection (Metal/CUDA/ROCm/CPU)
- Driver versions and toolchain availability
- VRAM capacity and model size recommendations
- llama-cpp-python wheel compatibility

If checks pass, proceed to Step 1. If checks fail, the script generates a troubleshooting prompt at `data/support_prompt.txt` - copy this to ChatGPT or Claude for installation guidance.

**Do not proceed until this script exits with code 0.**
```

Update "Quick Start" to reference this prerequisite and to align CUDA/Metal installation steps with the wheel-based story used by `check_api_env.py` and `src/api/services/gpu_installer.py` (no more git-based source installs for `llama-cpp-python`).

### 4. UI Cleanup

#### Remove from src/api/routers/gpu_ui.py

**Endpoints to remove (duplicate functionality now in CLI):**
- `GET /ui/settings/gpu/card`: GPU runtime card no longer performs hardware detection or installation
- `POST /ui/settings/gpu/detect`: Hardware detection is CLI-only (`check_api_env.py`)
- `POST /ui/settings/gpu/install`: Wheel installation now handled pre-deployment in the CLI
- `POST /ui/settings/gpu/support-prompt`: Prompt generation now in `check_api_env.py`

**Endpoints to keep (unique web UI value):**
- `POST /ui/operations/models/change`: Model selection/download after setup
- `GET /ui/operations/models`: Model management card

#### Template Updates

Remove from `src/api/templates/gpu/partials/settings_gpu_runtime.html`:
- Entire card (file removed). The settings page no longer includes a GPU runtime card; instead, it should briefly explain that hardware diagnostics and wheel compatibility checks live in `scripts/check_api_env.py` and are performed before API server installation.

Remove from `src/api/templates/gpu/settings_gpu.html`:
- GPU runtime/settings card include (no longer rendered in the settings layout)

Keep:
- Model selection/download card (operations use case)
- Configuration status display

### 5. Script Execution Flow

```
User runs: python scripts/check_api_env.py

1. Ensure API server is not running (HTTP probe); if running, print warning and exit 1 (unless `--force` is provided).
2. Detect GPU backends → gpu_installer.detect_gpu_backends()
2. Query VRAM → get_vram_info()
3. Check prerequisites → prerequisite_check(gpu_state)
4. Verify wheel availability → recommended_wheel_suffix(gpu_state)
5. If llama-cpp-python installed → verify_llama_install(gpu_state)

IF all checks pass:
   - Print success summary (backend, VRAM, recommended models)
   - Save model recommendations to data/model_selection.json
   - Recommend `CHL_SEARCH_MODE=auto` in the output if any supported GPU backend is available; otherwise recommend `CHL_SEARCH_MODE=cpu`
   - Exit 0

IF any check fails:
   - Generate LLM support prompt → build_support_prompt(gpu_state, prereq, log)
   - Save prompt to data/support_prompt.txt
   - Print failure message with next steps
   - Exit 1
```

## Implementation Notes

### VRAM Detection Edge Cases

- **Apple Metal**: Unified memory means GPU shares system RAM; estimate ~70% available for GPU tasks
- **Multi-GPU systems**: Report highest VRAM GPU (user can override via CHL_GPU_PRIORITY)
- **Headless/SSH environments**: nvidia-smi may fail; fallback to /proc/driver/nvidia/gpus/*/information
- **WSL2**: CUDA passthrough is partial; detect via NVIDIA_VISIBLE_DEVICES

### Model Recommendation Logic

The VRAM thresholds are conservative:
- **6GB threshold**: 4B models at Q4_K_M need ~2.5GB each (embedding + reranker = 5GB), leaving headroom
- **2GB threshold**: 0.6B models at Q8_0 need ~600MB each (total 1.2GB), safe for integrated GPUs
- **Below 2GB**: Use lighter reranker quantization (Q4_K_M = ~300MB) to fit minimal hardware

Users can override recommendations by editing `data/model_selection.json` manually or via the web UI model management.

### Wheel Metadata Fetching

**Approach:**
1. Fetch wheel index from `https://abetlen.github.io/llama-cpp-python/whl/{backend}/`
2. Parse HTML to find latest wheel matching detected Python version and platform
3. Download wheel metadata (`.whl` is a ZIP; extract `METADATA` file)
4. Parse `Requires-Python`, `Requires-Dist`, and platform tags

**Fallback (strict failure):** If network is unavailable or the index is unreachable, treat this as a **hard failure** for Phase B diagnostics:
- Print a clear message that outbound HTTPS access to the configured wheel index is required.
- Exit with code 1 (no offline mode for Phase B; offline deployments are considered out of scope).

### CPU Mode Behavior

CPU-only behavior is driven by **hardware detection first**, not the current `CHL_SEARCH_MODE` value:
- If any supported GPU backend (Metal/CUDA/ROCm) is detected and passes basic prerequisite checks, `check_api_env.py` **always** runs full GPU diagnostics (VRAM, drivers, wheel validation) and recommends GPU mode (`CHL_SEARCH_MODE=auto`) in its output.
- If no supported GPU backends are detected, treat the environment as CPU-only:
  - Skip GPU-specific checks (VRAM, drivers, wheel validation)
  - Print message: "CPU-only environment detected; GPU features disabled"
  - Recommend CPU-optimized models (0.6B Q8_0 for speed) or skipping ML setup entirely
  - Recommend `CHL_SEARCH_MODE=cpu` in the output
- If a GPU is detected but `CHL_SEARCH_MODE=cpu` is currently set, the script warns the user and suggests switching to `CHL_SEARCH_MODE=auto` to take advantage of GPU acceleration.

## Success Criteria

Phase B is complete when:

1. ✅ `scripts/check_api_env.py` runs successfully on reference hardware (Apple Silicon, NVIDIA CUDA, CPU-only)
2. ✅ VRAM detection works for all supported backends (Metal, CUDA, ROCm)
3. ✅ Script auto-selects correct model sizes based on detected VRAM
4. ✅ LLM support prompt is generated when checks fail and helps user resolve issues
5. ✅ Wheel metadata is fetched dynamically (no duplicated low-level requirements in code) and the script fails fast when the index is unreachable
6. ✅ README.md guides users to run check_api_env.py before installation
7. ✅ Web UI no longer has duplicate environment setup functionality
8. ✅ Phase 0 CPU/GPU isolation is preserved (no new cross-imports)

## Non-Goals (Deferred to Future Phases)

- **Automatic remediation**: Script only detects and reports; it does not install drivers or fix issues automatically
- **Continuous monitoring**: This is a one-time pre-installation check, not runtime monitoring
- **Multi-GPU orchestration**: Only detects and reports; user must set CHL_GPU_PRIORITY manually
- **Lock mechanism improvements**: Phase 0 preserves existing locks; Phase B does not modify concurrency controls
- **ROCm/Intel GPU enablement**: AMD ROCm and Intel GPU acceleration remain TBD per README (Options D/E). Phase B focuses on Metal, CUDA, and CPU-only environments.

## Validation

After implementation:

1. **Boundary tests**: Verify check_api_env.py only imports allowed modules (gpu_installer, config, stdlib)
2. **Manual testing**: Run on Apple Silicon, NVIDIA CUDA, and CPU-only systems
3. **Failure scenarios**: Test with missing drivers, insufficient VRAM, wrong Python version
4. **Prompt quality**: Validate generated LLM prompts successfully guide users to resolution
5. **UI regression**: Verify web UI still works for model management after removal of install buttons
