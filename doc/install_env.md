[Return to README.md](../README.md)
# Install Python Environment

This guide covers API server installation per hardware platform. For the overall workflow, see `doc/manual.md`.

## Install Python Environment

Choose your hardware platform and install the API server runtime.

### Quick Comparison

| Feature | CPU-only | Apple Metal | NVIDIA CUDA |
|---------|----------|-------------|-------------|
| Best for | Testing, keyword search, limited resources | Mac M1/M2/M3/M4 with semantic search | Linux/Windows with NVIDIA GPU and semantic search |
| Search type | SQLite text (LIKE queries) | FAISS + embeddings + reranker | FAISS + embeddings + reranker |
| Python version | 3.10-3.13 | 3.10-3.12 | 3.10-3.11 |
| ML dependencies | None | HuggingFace Transformers + PyTorch (Metal) | HuggingFace Transformers + PyTorch (CUDA) |
| Model downloads | None | ~1.5GB (Qwen 0.6B models) | ~1.5GB (0.6B models) or ~10GB (4B models) |
| VRAM requirements | N/A | 8GB+ unified memory | 6GB min (10GB+ for 4B models) |
| Setup complexity | Low (no drivers needed) | Medium (requires Xcode CLT) | High (CUDA toolkit + cuDNN required) |
| Requirements file | `requirements_cpu.txt` | `requirements_apple.txt` | `requirements_nvidia.txt` |

**Note:** AMD (ROCm) and Intel (oneAPI) GPU support is planned for future releases.

### Option A: CPU-Only Mode (No ML Dependencies)

**Best for:** Limited VRAM, keyword search is sufficient, or testing without GPU overhead.

**Python requirement:** 3.10 or newer (3.13 supported for CPU mode)

```bash
# Check Python version (should be 3.10+)
python3 --version

# Create venv and install dependencies
python3 -m venv .venv-cpu                 # macOS/Linux
# python -m venv .venv-cpu                # Windows
source .venv-cpu/bin/activate             # macOS/Linux
# .venv-cpu\Scripts\activate              # Windows

# Install API server dependencies
python -m pip install --upgrade pip
python -m pip install -r requirements_cpu.txt
```

### Option B: Apple Silicon (Metal GPU Acceleration)

**Best for:** macOS with M1/M2/M3/M4, semantic search with GPU acceleration using HuggingFace embeddings + reranker.

**Prerequisites:**
- macOS with Apple Silicon (M1, M2, M3, M4)
- Xcode Command Line Tools: `xcode-select --install`
- Python 3.12 (install via Homebrew: `brew install python@3.12`)

```bash
# Create dedicated venv for API server (Python 3.12)
python3.12 -m venv .venv-apple
source .venv-apple/bin/activate

# Install API server dependencies
python -m pip install --upgrade pip
python -m pip install -r requirements_apple.txt
```

**Note:** Default models are Qwen3-Embedding-0.6B and Qwen3-Reranker-0.6B (~1.5GB total) optimized for Metal GPU.

### Option C: NVIDIA GPU Acceleration (CUDA, HF stack)

**Best for:** Linux/Windows with NVIDIA GPU (Pascal or newer), want semantic search with GPU acceleration using HuggingFace Transformers + Torch CUDA.

**Prerequisites & VRAM sizing:**
- NVIDIA GPU with CUDA Compute Capability 6.0+ (Pascal or newer: GTX 1060+, RTX series, etc.)
- CUDA Toolkit 12.x installed (e.g., `/usr/local/cuda-12.4` or `/usr/local/cuda-12.5`)
- cuDNN libraries
- CMake 3.18+
- **Python 3.10 or 3.11** (Torch CUDA wheels are published for 3.10/3.11; 3.12 support may lag)
- VRAM guide for HF models:
  - <=10 GB: keep defaults (Embedding 0.6B + Reranker 0.6B)
  - 12-16 GB: Embedding 4B + Reranker 0.6B (better recall, safe VRAM)
  - >=20 GB: Embedding 4B + Reranker 4B (highest quality)

```bash
# Create dedicated venv for API server (Python 3.10 or 3.11)
/usr/bin/python3.11 -m venv .venv-nvidia  # Or python3.10
source .venv-nvidia/bin/activate          # Windows: .venv-nvidia\Scripts\activate

# Install API server dependencies (HF + Torch CUDA)
python -m pip install --upgrade pip
PIP_EXTRA_INDEX_URL=https://download.pytorch.org/whl/cu124 \
  python -m pip install -r requirements_nvidia.txt
```

**Troubleshooting:** If `python3.11 -m venv` fails with ensurepip errors and you have conda/uv installed, use the full system path `/usr/bin/python3.11` instead of just `python3.11` to avoid PATH conflicts.

### Option D: AMD GPU Acceleration (TBD)

**Best for:** Linux with AMD GPU (RDNA2 or newer), want semantic search with GPU acceleration.

**Status:** Requirements file and installation instructions to be added in a future release.

### Option E: Intel GPU Acceleration (TBD)

**Best for:** Linux/Windows with Intel Arc or integrated GPU, want semantic search with oneAPI acceleration.

**Status:** Requirements file and installation instructions to be added in a future release.
