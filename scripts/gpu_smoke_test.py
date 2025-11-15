"""
Minimal smoke test that verifies a CUDA-enabled llama-cpp-python build can
load the Qwen embedding & reranker GGUF models and execute on GPU.

Run inside the GPU-enabled Docker image (see docker/Dockerfile.cuda-mvp) or any
environment where CUDA 12.5 drivers are available.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from textwrap import dedent

from huggingface_hub import hf_hub_download
from llama_cpp import Llama, llama_supports_gpu_offload


EMBED_REPO = os.getenv("CHL_EMBED_REPO", "Qwen/Qwen3-Embedding-0.6B-GGUF")
EMBED_FILENAME = os.getenv("CHL_EMBED_FILENAME", "q4_k_m.gguf")
RERANK_REPO = os.getenv("CHL_RERANK_REPO", "Mungert/Qwen3-Reranker-0.6B-GGUF")
RERANK_FILENAME = os.getenv("CHL_RERANK_FILENAME", "q4_k_m.gguf")
GGUF_CACHE = Path(os.getenv("GGUF_CACHE", "/opt/chl/models"))
GGUF_CACHE.mkdir(parents=True, exist_ok=True)


def download_model(repo_id: str, filename: str) -> Path:
    print(f"⏬ Downloading {repo_id}/{filename} ...")
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


def load_llama(model_path: Path, *, embedding: bool = False) -> Llama:
    print(f"⚙️  Loading {model_path.name} (embedding={embedding}) ...")
    llama = Llama(
        model_path=str(model_path),
        embedding=embedding,
        n_gpu_layers=-1,
        use_mlock=False,
        verbose=False,
    )
    print("✅ Model loaded on GPU")
    return llama


def verify_embedding(llama: Llama) -> None:
    sample = [
        "GPU smoke test sentence one.",
        "Another sentence for embeddings.",
    ]
    vectors = llama.embed(sample)
    dims = len(vectors[0])
    print(f"🧠 Embedding produced {len(vectors)} vectors with dimension {dims}")


def verify_reranker(llama: Llama) -> None:
    prompt = dedent(
        """
        ### query:
        Compare the relevance of the following passages for GPU setup troubleshooting.
        1. Ensure NVIDIA driver 570.158.01 matches CUDA toolkit 12.5.
        2. Verify llama-cpp-python was compiled with GGML_CUDA and cuBLAS.
        ### instruction:
        Respond with the index (1 or 2) that is more relevant.
        """
    ).strip()
    response = llama(
        prompt,
        max_tokens=8,
        temperature=0.1,
        top_p=0.9,
    )
    text = response["choices"][0]["text"].strip()
    print(f"📊 Reranker output: {text or '[empty]'}")


def main() -> int:
    print("llama_supports_gpu_offload:", llama_supports_gpu_offload())
    embed_path = download_model(EMBED_REPO, EMBED_FILENAME)
    rerank_path = download_model(RERANK_REPO, RERANK_FILENAME)

    embed_llama = load_llama(embed_path, embedding=True)
    verify_embedding(embed_llama)

    rerank_llama = load_llama(rerank_path, embedding=False)
    verify_reranker(rerank_llama)

    print("🎉 GPU smoke test completed successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
