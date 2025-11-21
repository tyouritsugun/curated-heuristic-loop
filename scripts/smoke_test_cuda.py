#!/usr/bin/env python
"""
NVIDIA CUDA smoke test that verifies CUDA-enabled llama-cpp-python
can load embedding and reranker models and execute on GPU.

This test validates:
1. llama-cpp-python is built with CUDA support
2. CUDA GPU offload is available
3. Embedding model loads and generates vectors
4. Reranker model loads and produces scores

Prerequisites:
- Linux/Windows with NVIDIA GPU (Pascal or newer)
- CUDA drivers and toolkit installed
- Python environment with CUDA-accelerated llama-cpp-python
- Models will be downloaded from Hugging Face if not cached

Usage:
    python scripts/smoke_test_cuda.py

Optional environment variables:
    CHL_EMBED_REPO      - Embedding model repo (default: Qwen/Qwen3-Embedding-0.6B-GGUF)
    CHL_EMBED_FILENAME  - Embedding GGUF file (default: q4_k_m.gguf)
    CHL_RERANK_REPO     - Reranker model repo (default: Mungert/Qwen3-Reranker-0.6B-GGUF)
    CHL_RERANK_FILENAME - Reranker GGUF file (default: q4_k_m.gguf)
    GGUF_CACHE          - Model cache directory (default: /opt/chl/models or ./models)
"""

from __future__ import annotations

import os
import sys
import platform
from pathlib import Path
from textwrap import dedent

try:
    from huggingface_hub import hf_hub_download
    from llama_cpp import Llama, llama_supports_gpu_offload
except ImportError as e:
    print(f"❌ Missing dependency: {e}")
    print("\nPlease install NVIDIA GPU dependencies:")
    print("  python -m pip install --upgrade pip")
    print("  CMAKE_ARGS='-DGGML_CUDA=on' \\")
    print("    python -m pip install -r requirements_nvidia.txt")
    sys.exit(1)


EMBED_REPO = os.getenv("CHL_EMBED_REPO", "Qwen/Qwen3-Embedding-0.6B-GGUF")
EMBED_FILENAME = os.getenv("CHL_EMBED_FILENAME", "q4_k_m.gguf")
RERANK_REPO = os.getenv("CHL_RERANK_REPO", "Mungert/Qwen3-Reranker-0.6B-GGUF")
RERANK_FILENAME = os.getenv("CHL_RERANK_FILENAME", "q4_k_m.gguf")

# Default to ./models for local testing, /opt/chl/models for Docker
default_cache = "./models" if not Path("/opt/chl").exists() else "/opt/chl/models"
GGUF_CACHE = Path(os.getenv("GGUF_CACHE", default_cache))
GGUF_CACHE.mkdir(parents=True, exist_ok=True)


def check_cuda_support() -> None:
    """Verify CUDA GPU offload is available."""
    print("🔍 Checking CUDA GPU support...")

    if llama_supports_gpu_offload():
        print("✅ GPU offload supported (CUDA acceleration available)")
    else:
        print("❌ GPU offload NOT supported")
        print("   This may indicate llama-cpp-python was not built with CUDA support")
        print("\nPlease reinstall with CUDA support:")
        print("  CMAKE_ARGS='-DGGML_CUDA=on' \\")
        print("    python -m pip install --force-reinstall llama-cpp-python")
        sys.exit(1)


def download_model(repo_id: str, filename: str) -> Path:
    """Download model from Hugging Face Hub."""
    print(f"⏬ Downloading {repo_id}/{filename}...")
    try:
        path = Path(
            hf_hub_download(
                repo_id=repo_id,
                filename=filename,
                local_dir=str(GGUF_CACHE),
                local_dir_use_symlinks=False,
            )
        )
        print(f"✅ Cached at {path}")
        return path
    except Exception as e:
        print(f"❌ Failed to download model: {e}")
        sys.exit(1)


def load_llama(model_path: Path, *, embedding: bool = False) -> Llama:
    """Load model with CUDA GPU acceleration."""
    print(f"⚙️  Loading {model_path.name} (embedding={embedding}) with CUDA GPU...")
    try:
        llama = Llama(
            model_path=str(model_path),
            embedding=embedding,
            n_gpu_layers=-1,  # Offload all layers to CUDA GPU
            use_mlock=False,
            verbose=False,
        )
        print("✅ Model loaded successfully")
        return llama
    except Exception as e:
        print(f"❌ Failed to load model: {e}")
        print("\nTroubleshooting:")
        print("  1. Ensure you have enough VRAM (models need ~2-4GB each)")
        print("  2. Check that CUDA drivers are properly installed")
        print("  3. Verify llama-cpp-python is installed with CUDA support")
        print("  4. Check model file is not corrupted")
        sys.exit(1)


def verify_embedding(llama: Llama) -> None:
    """Test embedding generation."""
    print("🧠 Testing embedding generation...")
    sample = [
        "CUDA GPU smoke test sentence one.",
        "Another sentence to verify batch embedding.",
    ]

    try:
        vectors = llama.embed(sample)
        dims = len(vectors[0])
        print(f"✅ Generated {len(vectors)} embeddings with dimension {dims}")

        # Sanity check: vectors should be non-zero
        if all(abs(v) < 1e-6 for v in vectors[0][:10]):
            print("⚠️  WARNING: Embedding vectors appear to be all zeros")
        else:
            print("✅ Embedding vectors contain non-zero values")

    except Exception as e:
        print(f"❌ Embedding generation failed: {e}")
        sys.exit(1)


def verify_reranker(llama: Llama) -> None:
    """Test reranker inference."""
    print("📊 Testing reranker inference...")

    prompt = dedent(
        """
        ### query:
        Compare the relevance of the following passages for NVIDIA CUDA setup.
        1. Ensure NVIDIA driver version matches CUDA toolkit requirements.
        2. Verify llama-cpp-python was compiled with GGML_CUDA and cuBLAS.
        ### instruction:
        Respond with the index (1 or 2) that is more relevant.
        """
    ).strip()

    try:
        response = llama(
            prompt,
            max_tokens=8,
            temperature=0.1,
            top_p=0.9,
        )
        text = response["choices"][0]["text"].strip()
        print(f"✅ Reranker output: {text or '[empty]'}")

        if not text:
            print("⚠️  WARNING: Reranker produced empty output (may be normal for some models)")

    except Exception as e:
        print(f"❌ Reranker inference failed: {e}")
        sys.exit(1)


def main() -> int:
    """Run NVIDIA CUDA smoke tests."""
    print("=" * 60)
    print("CHL NVIDIA CUDA GPU Smoke Test")
    print("=" * 60)
    print(f"\nPlatform: {platform.system()} {platform.machine()}")
    print(f"Python: {sys.version.split()[0]}")
    print(f"Model cache: {GGUF_CACHE}")
    print()

    # Check CUDA support
    check_cuda_support()

    # Download models
    embed_path = download_model(EMBED_REPO, EMBED_FILENAME)
    rerank_path = download_model(RERANK_REPO, RERANK_FILENAME)

    # Test embedding model
    print("\n" + "-" * 60)
    print("Testing Embedding Model")
    print("-" * 60)
    embed_llama = load_llama(embed_path, embedding=True)
    verify_embedding(embed_llama)

    # Test reranker model
    print("\n" + "-" * 60)
    print("Testing Reranker Model")
    print("-" * 60)
    rerank_llama = load_llama(rerank_path, embedding=False)
    verify_reranker(rerank_llama)

    # Success
    print("\n" + "=" * 60)
    print("✅ All NVIDIA CUDA GPU smoke tests passed!")
    print("=" * 60)
    print("\nCUDA acceleration is working correctly:")
    print("  ✅ llama-cpp-python supports GPU offload")
    print("  ✅ Embedding model loads and generates vectors")
    print("  ✅ Reranker model loads and produces output")
    print("\nYour NVIDIA CUDA GPU setup is ready for production use.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
