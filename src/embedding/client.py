"""Embedding client for generating vector embeddings using Qwen3 GGUF models"""
import logging
from typing import List, Optional
from pathlib import Path
import numpy as np

logger = logging.getLogger(__name__)


class EmbeddingClient:
    """Client for generating embeddings using GGUF models via llama-cpp-python

    Supports Qwen3 embedding models with proper tokenization and pooling.
    Uses last token pooling and L2 normalization for optimal results.

    Example:
        client = EmbeddingClient(
            model_repo="Qwen/Qwen3-Embedding-0.6B-GGUF",
            quantization="Q8_0"
        )
        embeddings = client.encode(["text 1", "text 2"])
    """

    def __init__(
        self,
        model_repo: str,
        quantization: str,
        normalize: bool = True,
        batch_size: int = 64,
        n_ctx: int = 2048,
        n_gpu_layers: int = 0,
    ):
        """Initialize embedding client with GGUF model

        Args:
            model_repo: HuggingFace GGUF repo (e.g., 'Qwen/Qwen3-Embedding-0.6B-GGUF')
            quantization: Quantization type (e.g., 'Q4_K_M', 'Q8_0', 'f16')
            normalize: Apply L2 normalization to embeddings (recommended for cosine similarity)
            batch_size: Default batch size for encoding
            n_ctx: Context size (default 2048 tokens)
            n_gpu_layers: Number of layers to offload to GPU (0 = CPU only)

        Raises:
            EmbeddingClientError: If model cannot be loaded
        """
        self.model_repo = model_repo
        self.quantization = quantization
        self.normalize = normalize
        self.batch_size = batch_size

        try:
            # Import here to avoid hard dependency
            from llama_cpp import Llama

            logger.info(f"Loading GGUF embedding model: {model_repo} [{quantization}]")

            # Find cached GGUF file
            model_path = self._find_cached_gguf(model_repo, quantization)

            # Load model with embedding mode enabled
            self.model = Llama(
                model_path=str(model_path),
                embedding=True,  # Enable embedding mode
                n_ctx=n_ctx,
                n_gpu_layers=n_gpu_layers,
                verbose=False,
            )

            # Get embedding dimension from first test encoding
            test_emb = self.model.create_embedding("test")
            self.dimension = len(test_emb['data'][0]['embedding'])

            logger.info(
                f"GGUF embedding model loaded: {model_repo} [{quantization}], "
                f"dimension={self.dimension}, path={model_path}"
            )

        except ImportError as e:
            raise EmbeddingClientError(
                "llama-cpp-python not installed. Install ML extras with: uv sync --python 3.11 --extra ml"
            ) from e

        except Exception as e:
            raise EmbeddingClientError(
                f"Failed to load GGUF embedding model '{model_repo}': {e}"
            ) from e

    @property
    def model_name(self) -> str:
        """Get model name in legacy format (repo:quant) for backward compatibility"""
        return f"{self.model_repo}:{self.quantization}"

    @property
    def embedding_dimension(self) -> int:
        """Alias for dimension property for backward compatibility"""
        return self.dimension

    def _find_cached_gguf(self, repo_id: str, quantization: str) -> Path:
        """Find cached GGUF file in HuggingFace cache

        Args:
            repo_id: Repository ID (e.g., 'Qwen/Qwen3-Embedding-0.6B-GGUF')
            quantization: Quantization type (e.g., 'Q8_0')

        Returns:
            Path to cached GGUF file

        Raises:
            FileNotFoundError: If cached file not found
        """
        cache_dir = Path.home() / ".cache" / "huggingface" / "hub"
        model_cache_name = f"models--{repo_id.replace('/', '--')}"
        model_path = cache_dir / model_cache_name

        if not model_path.exists():
            raise FileNotFoundError(
                f"Model cache not found: {model_path}. "
                f"Run 'python scripts/setup.py --download-models' first."
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
            f"Run 'python scripts/setup.py --download-models' first."
        )

    def encode(
        self,
        texts: List[str],
        batch_size: Optional[int] = None,
        show_progress: bool = False,
    ) -> np.ndarray:
        """Encode texts into embeddings using Qwen3 GGUF models

        Qwen3 requirements:
        - Append <|endoftext|> token to each text
        - Use last token pooling
        - Apply L2 normalization with factor of 2

        Args:
            texts: List of text strings to encode
            batch_size: Batch size (currently processed sequentially due to llama.cpp limitations)
            show_progress: Show progress bar (useful for large batches)

        Returns:
            numpy array of shape (len(texts), dimension)

        Raises:
            EmbeddingClientError: If encoding fails
        """
        if not texts:
            return np.array([])

        try:
            logger.debug(
                f"Encoding {len(texts)} texts with GGUF model, "
                f"normalize={self.normalize}"
            )

            embeddings = []

            for i, text in enumerate(texts):
                if show_progress and i % 10 == 0:
                    logger.info(f"Encoding progress: {i}/{len(texts)}")

                # Append Qwen3 special token for embeddings
                text_with_token = f"{text}<|endoftext|>"

                # Generate embedding
                result = self.model.create_embedding(text_with_token)
                embedding = result['data'][0]['embedding']

                embeddings.append(embedding)

            # Convert to numpy array
            embeddings = np.array(embeddings, dtype=np.float32)

            # Apply L2 normalization if requested
            if self.normalize:
                # Qwen3 uses L2 normalization with factor of 2
                norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
                embeddings = embeddings / (norms + 1e-8)  # Add epsilon to avoid division by zero

            logger.debug(f"Encoded {len(texts)} texts, output shape: {embeddings.shape}")

            return embeddings

        except Exception as e:
            raise EmbeddingClientError(f"Embedding encoding failed: {e}") from e

    def encode_single(self, text: str) -> np.ndarray:
        """Encode a single text into embedding

        Args:
            text: Text string to encode

        Returns:
            numpy array of shape (dimension,)

        Raises:
            EmbeddingClientError: If encoding fails
        """
        embeddings = self.encode([text])
        return embeddings[0]

    def get_model_version(self) -> str:
        """Get model version/revision for cache invalidation

        Returns:
            Model version string (repo + quantization)
        """
        return f"{self.model_repo}:{self.quantization}"

    @property
    def embedding_dimension(self) -> int:
        """Get embedding dimension"""
        return self.dimension


class EmbeddingClientError(Exception):
    """Exception raised by EmbeddingClient"""
    pass
