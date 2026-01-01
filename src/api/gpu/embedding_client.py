"""Embedding client for generating vector embeddings using Qwen3 HF models."""

import logging
from typing import List, Optional

import numpy as np

import torch
from transformers import AutoModel, AutoTokenizer

logger = logging.getLogger(__name__)


class EmbeddingClient:
    """Client for generating embeddings using HF Transformers."""

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
        self._init_hf(model_repo)

    @property
    def model_name(self) -> str:
        return f"{self.model_repo}:{self.quantization}"

    @property
    def embedding_dimension(self) -> int:
        return self.dimension

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

            embeddings = self._encode_hf(texts)

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

    def _init_hf(self, model_repo: str) -> None:
        try:
            logger.info("Loading HF embedding model: %s", model_repo)
            self.tokenizer = AutoTokenizer.from_pretrained(model_repo, trust_remote_code=True, local_files_only=True)

            if torch.backends.mps.is_available():
                self.device = torch.device("mps")
                torch_dtype = torch.float16
            elif torch.cuda.is_available():
                self.device = torch.device("cuda")
                torch_dtype = torch.float16
            else:
                self.device = torch.device("cpu")
                torch_dtype = None

            self.model = AutoModel.from_pretrained(
                model_repo,
                trust_remote_code=True,
                torch_dtype=torch_dtype,
                local_files_only=True
            )

            self.model.to(self.device)
            self.model.eval()

            # Infer dimension by a tiny forward pass
            with torch.no_grad():
                inputs = self.tokenizer("test", return_tensors="pt").to(self.device)
                outputs = self.model(**inputs)
                hidden = outputs.last_hidden_state
                self.dimension = int(hidden.shape[-1])

            logger.info(
                "HF embedding model loaded: %s on %s, dimension=%s",
                model_repo,
                self.device,
                self.dimension,
            )
        except Exception as exc:
            raise EmbeddingClientError(
                f"Failed to load HF embedding model '{model_repo}': {exc}"
            ) from exc

    def _encode_hf(self, texts: List[str]) -> np.ndarray:
        embeddings = []
        for start in range(0, len(texts), self.batch_size):
            batch = texts[start : start + self.batch_size]
            with torch.no_grad():
                inputs = self.tokenizer(
                    batch,
                    return_tensors="pt",
                    padding=True,
                    truncation=True,
                    max_length=512,
                ).to(self.device)
                outputs = self.model(**inputs)
                hidden = outputs.last_hidden_state  # [B, T, D]
                # Mean pooling over tokens (mask aware)
                attn_mask = inputs.get("attention_mask")
                if attn_mask is None:
                    pooled = hidden.mean(dim=1)
                else:
                    mask = attn_mask.unsqueeze(-1).expand(hidden.size()).float()
                    pooled = (hidden * mask).sum(dim=1) / mask.sum(dim=1).clamp(min=1e-9)
                embeddings.append(pooled.cpu().numpy())

        return np.concatenate(embeddings, axis=0).astype(np.float32)


class EmbeddingClientError(Exception):
    """Exception raised by EmbeddingClient."""

    pass
