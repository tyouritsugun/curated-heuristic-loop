"""Embedding client for generating vector embeddings using Qwen3 GGUF models."""

import logging
from pathlib import Path
from typing import List, Optional

import numpy as np

logger = logging.getLogger(__name__)


class EmbeddingClient:
    """Client for generating embeddings using GGUF models via llama-cpp-python."""

    def __init__(
        self,
        model_repo: str,
        quantization: str,
        normalize: bool = True,
        batch_size: int = 64,
        n_ctx: int = 2048,
        n_gpu_layers: int = 0,
    ):
        self.model_repo = model_repo
        self.quantization = quantization
        self.normalize = normalize
        self.batch_size = batch_size

        try:
            from llama_cpp import Llama

            logger.info("Loading GGUF embedding model: %s [%s]", model_repo, quantization)

            model_path = self._find_cached_gguf(model_repo, quantization)
            self.model = Llama(
                model_path=str(model_path),
                embedding=True,
                n_ctx=n_ctx,
                n_gpu_layers=n_gpu_layers,
                verbose=False,
            )

            test_emb = self.model.create_embedding("test")
            self.dimension = len(test_emb["data"][0]["embedding"])

            logger.info(
                "GGUF embedding model loaded: %s [%s], dimension=%s, path=%s",
                model_repo,
                quantization,
                self.dimension,
                model_path,
            )
        except ImportError as exc:
            raise EmbeddingClientError(
                "llama-cpp-python not installed. Install ML extras with: "
                "uv sync --python 3.11 --extra ml"
            ) from exc
        except Exception as exc:
            raise EmbeddingClientError(
                f"Failed to load GGUF embedding model '{model_repo}': {exc}"
            ) from exc

    @property
    def model_name(self) -> str:
        return f"{self.model_repo}:{self.quantization}"

    @property
    def embedding_dimension(self) -> int:
        return self.dimension

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

    def encode(
        self,
        texts: List[str],
        batch_size: Optional[int] = None,
        show_progress: bool = False,
    ) -> np.ndarray:
        if not texts:
            return np.array([])

        try:
            logger.debug(
                "Encoding %s texts with GGUF model, normalize=%s",
                len(texts),
                self.normalize,
            )

            embeddings = []
            for i, text in enumerate(texts):
                if show_progress and i % 10 == 0:
                    logger.info("Encoding progress: %s/%s", i, len(texts))

                text_with_token = f"{text}<|endoftext|>"
                result = self.model.create_embedding(text_with_token)
                embedding = result["data"][0]["embedding"]
                embeddings.append(embedding)

            embeddings = np.array(embeddings, dtype=np.float32)

            if self.normalize:
                norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
                embeddings = embeddings / (norms + 1e-8)

            logger.debug("Encoded %s texts, output shape: %s", len(texts), embeddings.shape)
            return embeddings
        except Exception as exc:
            raise EmbeddingClientError(f"Embedding encoding failed: {exc}") from exc

    def encode_single(self, text: str) -> np.ndarray:
        embeddings = self.encode([text])
        return embeddings[0]

    def get_model_version(self) -> str:
        return f"{self.model_repo}:{self.quantization}"


class EmbeddingClientError(Exception):
    """Exception raised by EmbeddingClient."""

    pass

