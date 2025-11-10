"""Hot-reload functionality for embedding and reranker clients

Allows reloading embedding/reranker clients from updated model_selection.json
without requiring a full server restart.
"""
import json
import logging
from pathlib import Path
from typing import Optional, Tuple

from .client import EmbeddingClient, EmbeddingClientError
from .reranker import RerankerClient

logger = logging.getLogger(__name__)


class ModelReloadError(Exception):
    """Raised when model hot-reload fails"""
    pass


def load_model_selection(project_root: Optional[Path] = None) -> dict:
    """Load model selection from data/model_selection.json

    Args:
        project_root: Project root directory (defaults to auto-detect)

    Returns:
        Dict with embedding_repo, embedding_quant, reranker_repo, reranker_quant

    Raises:
        ModelReloadError: If model_selection.json is missing or invalid
    """
    if project_root is None:
        project_root = Path(__file__).resolve().parents[2]

    model_selection_path = project_root / "data" / "model_selection.json"

    if not model_selection_path.exists():
        raise ModelReloadError(
            f"Model selection file not found: {model_selection_path}"
        )

    try:
        with model_selection_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        raise ModelReloadError(
            f"Failed to read model selection file: {e}"
        ) from e

    # Validate required fields
    required_fields = ["embedding_repo", "embedding_quant", "reranker_repo", "reranker_quant"]
    missing = [f for f in required_fields if not data.get(f)]
    if missing:
        raise ModelReloadError(
            f"Model selection file missing required fields: {', '.join(missing)}"
        )

    return data


def reload_embedding_client(
    current_client: Optional[EmbeddingClient] = None,
    n_gpu_layers: int = 0,
    project_root: Optional[Path] = None,
) -> Tuple[EmbeddingClient, bool]:
    """Reload embedding client from model_selection.json

    Args:
        current_client: Current embedding client (to check if reload is needed)
        n_gpu_layers: Number of GPU layers to use (default: 0 for CPU-only)
        project_root: Project root directory (defaults to auto-detect)

    Returns:
        Tuple of (new_client, changed) where changed indicates if model changed

    Raises:
        ModelReloadError: If reload fails
    """
    try:
        model_data = load_model_selection(project_root)
        embedding_repo = model_data["embedding_repo"]
        embedding_quant = model_data["embedding_quant"]

        # Check if reload is needed
        if current_client is not None:
            if (current_client.model_repo == embedding_repo and
                current_client.quantization == embedding_quant):
                logger.info("Embedding client already using requested model, no reload needed")
                return current_client, False

        # Load new client
        logger.info(f"Hot-reloading embedding client: {embedding_repo} [{embedding_quant}]")
        new_client = EmbeddingClient(
            model_repo=embedding_repo,
            quantization=embedding_quant,
            n_gpu_layers=n_gpu_layers,
        )

        logger.info(f"Successfully reloaded embedding client: {embedding_repo} [{embedding_quant}]")
        return new_client, True

    except EmbeddingClientError as e:
        raise ModelReloadError(f"Failed to load embedding client: {e}") from e


def reload_reranker_client(
    current_client: Optional[RerankerClient] = None,
    n_gpu_layers: int = 0,
    project_root: Optional[Path] = None,
) -> Tuple[Optional[RerankerClient], bool]:
    """Reload reranker client from model_selection.json

    Args:
        current_client: Current reranker client (to check if reload is needed)
        n_gpu_layers: Number of GPU layers to use (default: 0 for CPU-only)
        project_root: Project root directory (defaults to auto-detect)

    Returns:
        Tuple of (new_client, changed) where changed indicates if model changed
        Returns (None, False) if reranker is optional and load fails

    Raises:
        ModelReloadError: If reload fails critically
    """
    try:
        model_data = load_model_selection(project_root)
        reranker_repo = model_data["reranker_repo"]
        reranker_quant = model_data["reranker_quant"]

        # Check if reload is needed
        if current_client is not None:
            if (current_client.model_repo == reranker_repo and
                current_client.quantization == reranker_quant):
                logger.info("Reranker client already using requested model, no reload needed")
                return current_client, False

        # Load new client
        logger.info(f"Hot-reloading reranker client: {reranker_repo} [{reranker_quant}]")
        new_client = RerankerClient(
            model_repo=reranker_repo,
            quantization=reranker_quant,
            n_gpu_layers=n_gpu_layers,
        )

        logger.info(f"Successfully reloaded reranker client: {reranker_repo} [{reranker_quant}]")
        return new_client, True

    except Exception as e:
        # Reranker is optional, so we log warning but don't fail
        logger.warning(f"Failed to load reranker client (continuing without reranker): {e}")
        return None, False


def hot_reload_models(
    current_embedding: Optional[EmbeddingClient] = None,
    current_reranker: Optional[RerankerClient] = None,
    n_gpu_layers: int = 0,
    project_root: Optional[Path] = None,
) -> Tuple[EmbeddingClient, Optional[RerankerClient], bool]:
    """Hot-reload both embedding and reranker clients from model_selection.json

    Args:
        current_embedding: Current embedding client
        current_reranker: Current reranker client
        n_gpu_layers: Number of GPU layers to use
        project_root: Project root directory

    Returns:
        Tuple of (embedding_client, reranker_client, changed) where changed
        indicates if any model changed

    Raises:
        ModelReloadError: If embedding client reload fails
    """
    embedding_client, emb_changed = reload_embedding_client(
        current_embedding, n_gpu_layers, project_root
    )

    reranker_client, rnk_changed = reload_reranker_client(
        current_reranker, n_gpu_layers, project_root
    )

    changed = emb_changed or rnk_changed

    if changed:
        logger.info("Model hot-reload completed successfully")
    else:
        logger.info("No model changes detected, using existing clients")

    return embedding_client, reranker_client, changed
