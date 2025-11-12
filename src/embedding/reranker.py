"""Reranker client for cross-encoder reranking using Qwen3 GGUF models"""
import logging
from typing import List, Optional
from pathlib import Path
import numpy as np

logger = logging.getLogger(__name__)


class RerankerClient:
    """Client for reranking search results using GGUF reranker models

    Uses Qwen3-Reranker GGUF models via llama-cpp-python.
    Computes relevance scores for query-document pairs.

    Example:
        client = RerankerClient(
            model_repo="Mungert/Qwen3-Reranker-0.6B-GGUF",
            quantization="Q4_K_M"
        )
        scores = client.rerank("query", ["doc1", "doc2", "doc3"])
    """

    def __init__(
        self,
        model_repo: str,
        quantization: str,
        batch_size: int = 64,
        n_ctx: int = 512,
        n_gpu_layers: int = 0,
    ):
        """Initialize reranker client with GGUF model

        Args:
            model_repo: HuggingFace GGUF repo (e.g., 'Mungert/Qwen3-Reranker-0.6B-GGUF')
            quantization: Quantization type (e.g., 'Q4_K_M', 'Q8_0')
            batch_size: Default batch size for reranking
            n_ctx: Context size (default 512 tokens)
            n_gpu_layers: Number of layers to offload to GPU (0 = CPU only)

        Raises:
            RerankerClientError: If model cannot be loaded
        """
        self.model_repo = model_repo
        self.quantization = quantization
        self.batch_size = batch_size

        try:
            # Import here to avoid hard dependency
            from llama_cpp import Llama

            logger.info(f"Loading GGUF reranker model: {model_repo} [{quantization}]")

            # Find cached GGUF file
            model_path = self._find_cached_gguf(model_repo, quantization)

            # Load model with embedding mode for computing similarity
            self.model = Llama(
                model_path=str(model_path),
                embedding=True,  # Use embedding mode to compute similarities
                n_ctx=n_ctx,
                n_gpu_layers=n_gpu_layers,
                verbose=False,
            )

            logger.info(
                f"GGUF reranker model loaded: {model_repo} [{quantization}], "
                f"path={model_path}"
            )

        except ImportError as e:
            raise RerankerClientError(
                "llama-cpp-python not installed. Install ML extras with: uv sync --python 3.11 --extra ml"
            ) from e

        except Exception as e:
            raise RerankerClientError(
                f"Failed to load GGUF reranker model '{model_repo}': {e}"
            ) from e

    def _find_cached_gguf(self, repo_id: str, quantization: str) -> Path:
        """Find cached GGUF file in HuggingFace cache"""
        cache_dir = Path.home() / ".cache" / "huggingface" / "hub"
        model_cache_name = f"models--{repo_id.replace('/', '--')}"
        model_path = cache_dir / model_cache_name

        if not model_path.exists():
            raise FileNotFoundError(
                f"Model cache not found: {model_path}. "
                f"Run 'python scripts/setup-gpu.py --download-models' first."
            )

        # Generate filename based on provider convention
        model_name = repo_id.split("/")[1].replace("-GGUF", "")
        org = repo_id.split("/")[0]

        if org == "Qwen":
            quant_str = "f16" if quantization.upper() == "F16" else quantization.upper()
        else:
            quant_str = quantization.lower()

        filename = f"{model_name}-{quant_str}.gguf"

        # Search in snapshots
        for snapshot_dir in model_path.glob("snapshots/*"):
            gguf_file = snapshot_dir / filename
            if gguf_file.exists():
                return gguf_file

        raise FileNotFoundError(
            f"GGUF file not found: {filename} in {model_path}. "
            f"Run 'python scripts/setup-gpu.py --download-models' first."
        )

    def rerank(
        self,
        query: str,
        documents: List[str],
        batch_size: Optional[int] = None,
    ) -> List[float]:
        """Rerank documents against query using embedding similarity

        Computes embeddings for query and documents, then calculates
        cosine similarity scores.

        Args:
            query: Query text
            documents: List of document texts to rank
            batch_size: Batch size (currently unused, kept for API compatibility)

        Returns:
            List of relevance scores (higher = more relevant)
            Scores are in same order as input documents

        Raises:
            RerankerClientError: If reranking fails
        """
        if not documents:
            return []

        try:
            logger.debug(f"Reranking {len(documents)} documents with GGUF reranker")

            # Get query embedding
            query_text = f"{query}<|endoftext|>"
            query_result = self.model.create_embedding(query_text)
            query_emb = np.array(query_result['data'][0]['embedding'], dtype=np.float32)
            if query_emb.ndim == 2:
                query_emb = query_emb.mean(axis=0)

            # Normalize query embedding
            query_emb = query_emb / (np.linalg.norm(query_emb) + 1e-8)

            # Get document embeddings and compute scores
            scores = []
            for doc in documents:
                doc_text = f"{doc}<|endoftext|>"
                doc_result = self.model.create_embedding(doc_text)
                doc_emb = np.array(doc_result['data'][0]['embedding'], dtype=np.float32)
                if doc_emb.ndim == 2:
                    doc_emb = doc_emb.mean(axis=0)

                # Normalize document embedding
                doc_emb = doc_emb / (np.linalg.norm(doc_emb) + 1e-8)

                # Compute cosine similarity
                score = float(np.dot(query_emb, doc_emb))
                scores.append(score)

            logger.debug(f"Reranked {len(documents)} documents")

            return scores

        except Exception as e:
            raise RerankerClientError(f"Reranking failed: {e}") from e

    def get_model_version(self) -> str:
        """Get model version/revision

        Returns:
            Model version string (repo + quantization)
        """
        return f"{self.model_repo}:{self.quantization}"


class RerankerClientError(Exception):
    """Exception raised by RerankerClient"""
    pass
