"""Embedding provider protocol."""

from __future__ import annotations

from typing import Protocol, List


class EmbeddingProvider(Protocol):
    """Abstract embedding provider used by GPU runtime."""

    @property
    def embedding_dimension(self) -> int: ...

    def get_model_version(self) -> str: ...

    def encode(self, texts: List[str]) -> List[List[float]]: ...

    def encode_single(self, text: str) -> List[float]: ...

