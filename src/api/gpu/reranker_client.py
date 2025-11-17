"""Reranker client for Qwen GGUF models."""

import logging
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import List, Optional

import numpy as np

logger = logging.getLogger(__name__)


@contextmanager
def _suppress_llama_stderr():
    """Temporarily suppress noisy C++ logs from llama.cpp on stderr."""
    try:
        fd = sys.stderr.fileno()
    except OSError:
        yield
        return

    old_fd = os.dup(fd)
    try:
        with open(os.devnull, "w") as devnull:
            os.dup2(devnull.fileno(), fd)
        try:
            yield
        finally:
            os.dup2(old_fd, fd)
    finally:
        os.close(old_fd)


class RerankerClient:
    """Client for reranking documents using GGUF models."""

    def __init__(
        self,
        model_repo: str,
        quantization: str,
        n_ctx: int = 2048,
        n_gpu_layers: int = 0,
    ):
        self.model_repo = model_repo
        self.quantization = quantization

        try:
            from llama_cpp import Llama

            # Align llama.cpp logging behavior with the embedding client by
            # suppressing verbose backend logs from the C++ layer.
            import logging as _logging

            _logging.getLogger("llama-cpp-python").setLevel(_logging.CRITICAL)

            logger.info("Loading GGUF reranker model: %s [%s]", model_repo, quantization)

            model_path = self._find_cached_gguf(model_repo, quantization)
            with _suppress_llama_stderr():
                self.model = Llama(
                    model_path=str(model_path),
                    embedding=True,
                    n_ctx=n_ctx,
                    n_gpu_layers=n_gpu_layers,
                    verbose=False,
                )

            logger.info("GGUF reranker model loaded: %s [%s], path=%s", model_repo, quantization, model_path)
        except ImportError as exc:
            raise RerankerClientError(
                "llama-cpp-python not installed. Install ML extras with: uv sync --python 3.11 --extra ml"
            ) from exc
        except Exception as exc:
            raise RerankerClientError(
                f"Failed to load GGUF reranker model '{model_repo}': {exc}"
            ) from exc

    def _find_cached_gguf(self, repo_id: str, quantization: str) -> Path:
        cache_dir = Path.home() / ".cache" / "huggingface" / "hub"
        model_cache_name = f"models--{repo_id.replace('/', '--')}"
        model_path = cache_dir / model_cache_name

        if not model_path.exists():
            raise FileNotFoundError(
                f"Model cache not found: {model_path}. "
                "Run 'python scripts/setup-gpu.py --download-models' first."
            )

        model_name = repo_id.split("/")[1].replace("-GGUF", "")
        org = repo_id.split("/")[0]

        if org == "Qwen":
            quant_str = "f16" if quantization.upper() == "F16" else quantization.upper()
        else:
            quant_str = quantization.lower()

        filename = f"{model_name}-{quant_str}.gguf"

        for snapshot_dir in model_path.glob("snapshots/*"):
            gguf_file = snapshot_dir / filename
            if gguf_file.exists():
                return gguf_file

        raise FileNotFoundError(
            f"GGUF file not found: {filename} in {model_path}. "
            "Run 'python scripts/setup-gpu.py --download-models' first."
        )

    def rerank(
        self,
        query: str,
        documents: List[str],
        batch_size: Optional[int] = None,
    ) -> List[float]:
        if not documents:
            return []

        try:
            logger.debug("Reranking %s documents with GGUF reranker", len(documents))

            query_text = f"{query}<|endoftext|>"
            query_result = self.model.create_embedding(query_text)
            query_emb = np.array(query_result["data"][0]["embedding"], dtype=np.float32)
            if query_emb.ndim == 2:
                query_emb = query_emb.mean(axis=0)
            query_emb = query_emb / (np.linalg.norm(query_emb) + 1e-8)

            scores: List[float] = []
            for doc in documents:
                doc_text = f"{doc}<|endoftext|>"
                doc_result = self.model.create_embedding(doc_text)
                doc_emb = np.array(doc_result["data"][0]["embedding"], dtype=np.float32)
                if doc_emb.ndim == 2:
                    doc_emb = doc_emb.mean(axis=0)
                doc_emb = doc_emb / (np.linalg.norm(doc_emb) + 1e-8)
                score = float(np.dot(query_emb, doc_emb))
                scores.append(score)

            logger.debug("Reranked %s documents", len(documents))
            return scores
        except Exception as exc:
            raise RerankerClientError(f"Reranking failed: {exc}") from exc

    def get_model_version(self) -> str:
        return f"{self.model_repo}:{self.quantization}"


class RerankerClientError(Exception):
    """Exception raised by RerankerClient."""

    pass
