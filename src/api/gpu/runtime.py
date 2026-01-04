"""GPU-capable runtime builder (FAISS + embeddings).

This module is the canonical GPU/vector search wiring for the API server.
It mirrors the legacy `modes/vector/runtime.py` implementation but is
scoped under `src/api/gpu` and uses the shared `ModeRuntime` dataclass
from `src.common.interfaces.runtime`.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import func

from src.common.config.config import Config
from src.common.storage.database import Database
from src.common.storage.schema import FAISSMetadata, utc_now
from src.common.interfaces.runtime import (
    ModeRuntime,
    OperationsModeAdapter,
    DiagnosticsModeAdapter,
)
from src.api.services.search_service import SearchService
from src.api.gpu.faiss_manager import (
    ThreadSafeFAISSManager,
    initialize_faiss_with_recovery,
)
from src.api.gpu.search_provider import VectorFAISSProvider
from src.api.gpu.embedding_client import EmbeddingClient
from src.api.gpu.reranker_client import RerankerClient
from src.api.services.background_worker import BackgroundEmbeddingWorker, WorkerPool
from src.api.services.worker_control import WorkerControlService

logger = logging.getLogger(__name__)


class GpuOperationsModeAdapter(OperationsModeAdapter):
    """Operations adapter for vector-capable (GPU) mode."""

    def __init__(
        self,
        embedding_client=None,
        thread_safe_faiss=None,
        vector_provider=None,
        session_factory=None,
        model_name: Optional[str] = None,
        skills_enabled: bool = True,
    ):
        self._embedding_client = embedding_client
        self._thread_safe_faiss = thread_safe_faiss
        self._vector_provider = vector_provider
        self._session_factory = session_factory
        self._model_name = model_name
        self._skills_enabled = skills_enabled

    def can_run_vector_jobs(self) -> bool:
        return (
            self._embedding_client is not None
            and self._thread_safe_faiss is not None
            and self._vector_provider is not None
            and getattr(self._vector_provider, "is_available", False)
        )

    def get_embedding_service(self):
        """Get or create embedding service for operations."""
        if (
            not self.can_run_vector_jobs()
            or self._session_factory is None
        ):
            return None
        session = self._session_factory()
        from src.api.gpu.embedding_service import EmbeddingService
        service = EmbeddingService(
            session=session,
            embedding_client=self._embedding_client,
            model_name=self._model_name
            or getattr(self._embedding_client, "model_name", "unknown"),
            faiss_index_manager=self._thread_safe_faiss,
            skills_enabled=self._skills_enabled,
        )
        return service, session

    def get_search_provider(self):
        """Get vector search provider (contains FAISS manager and rebuild logic)."""
        return self._vector_provider


class GpuDiagnosticsAdapter(DiagnosticsModeAdapter):
    """Diagnostics adapter that inspects FAISS artifacts when GPU mode is active."""

    def faiss_status(self, data_path: Path, session) -> dict:
        faiss_index_dir = Path(data_path)
        if not faiss_index_dir.exists():
            return {
                "state": "info",
                "headline": "FAISS index not built",
                "detail": "Build index via Operations page or upload snapshot",
            }

        index_files = list(faiss_index_dir.glob("*.index"))
        if not index_files:
            return {
                "state": "info",
                "headline": "FAISS index not built",
                "detail": "Build index via Operations page or upload snapshot",
            }

        try:
            vector_count = (
                session.query(func.count(FAISSMetadata.id))
                .filter(FAISSMetadata.deleted == False)  # noqa: E712 - SQLAlchemy comparison
                .scalar()
                or 0
            )
        except Exception as exc:  # pragma: no cover - diagnostics shouldn't crash
            return {
                "state": "warn",
                "headline": "FAISS check failed",
                "detail": str(exc),
            }

        validated_at = utc_now()
        if vector_count > 0:
            index_size_mb = index_files[0].stat().st_size / (1024 * 1024)
            latest_entry = (
                session.query(FAISSMetadata)
                .order_by(FAISSMetadata.created_at.desc())
                .first()
            )
            if latest_entry and latest_entry.created_at:
                built_date = latest_entry.created_at.date().isoformat()
            else:
                built_date = "N/A"
            return {
                "state": "ok",
                "headline": "FAISS index ready",
                "detail": f"{vector_count} vectors",
                "validated_at": validated_at,
            }

        return {
            "state": "warn",
            "headline": "FAISS metadata missing",
            "detail": "Index files exist but metadata table is empty. Rebuild index to sync state.",
            "validated_at": validated_at,
        }


def _build_embedding_stack(
    config: Any, db: Any
) -> tuple[
    Optional[EmbeddingClient],
    Optional[ThreadSafeFAISSManager],
    Optional[RerankerClient],
    Optional[VectorFAISSProvider],
]:
    """Initialize embedding client, FAISS manager, reranker, and vector provider."""
    embedding_client: Optional[EmbeddingClient] = None
    thread_safe_faiss: Optional[ThreadSafeFAISSManager] = None
    reranker_client: Optional[RerankerClient] = None
    vector_provider: Optional[VectorFAISSProvider] = None

    try:
        logger.info("Starting search service initialization...")

        # Embedding client
        try:
            logger.info("Loading embedding client: %s", config.embedding_model)
            embedding_client = EmbeddingClient(
                model_repo=config.embedding_repo,
                quantization=config.embedding_quant,
                n_gpu_layers=getattr(config, "embedding_n_gpu_layers", 0),
            )
            logger.info(
                "✓ Embedding client loaded successfully: %s", config.embedding_model
            )
        except Exception as exc:
            logger.error(
                "✗ Embedding client initialization failed: %s", exc, exc_info=True
            )
            # Don't continue in GPU mode without embedding client - stop the server
            raise RuntimeError(
                f"GPU mode requires embedding model '{config.embedding_model}' to be available locally. "
                f"Please ensure the model is cached in your Hugging Face cache, or switch to CPU mode by using .venv-cpu."
            ) from exc

        # FAISS manager + thread-safe wrapper
        if embedding_client:
            try:
                logger.info("Initializing FAISS index with recovery...")
                with db.session_scope() as temp_session:
                    faiss_manager = initialize_faiss_with_recovery(
                        config,
                        temp_session,
                        embedding_client,
                        session_factory=db.get_session,
                    )

                if faiss_manager:
                    thread_safe_faiss = ThreadSafeFAISSManager(
                        faiss_manager=faiss_manager,
                        save_policy=config.faiss_save_policy,
                        save_interval=config.faiss_save_interval,
                        rebuild_threshold=config.faiss_rebuild_threshold,
                    )
                    logger.info(
                        "✓ ThreadSafeFAISSManager initialized: policy=%s, "
                        "threshold=%s, vectors=%s",
                        config.faiss_save_policy,
                        config.faiss_rebuild_threshold,
                        faiss_manager.index.ntotal,
                    )
                else:
                    logger.warning(
                        "✗ FAISS index recovery returned None, will use text search fallback"
                    )
            except Exception as exc:
                logger.error(
                    "✗ FAISS initialization failed with exception: %s",
                    exc,
                    exc_info=True,
                )
                logger.warning("FAISS initialization failed: %s", exc)
        else:
            logger.info("Skipping FAISS initialization (no embedding client)")

        # Reranker
        if embedding_client and thread_safe_faiss:
            try:
                logger.info("Loading reranker: %s", config.reranker_model)
                reranker_client = RerankerClient(
                    model_repo=config.reranker_repo,
                    quantization=config.reranker_quant,
                    n_gpu_layers=getattr(config, "reranker_n_gpu_layers", 0),
                )
                logger.info("✓ Reranker loaded: %s", config.reranker_model)
            except Exception as exc:
                logger.warning("✗ Reranker not available: %s", exc)
        else:
            logger.info(
                "Skipping reranker initialization (missing embedding client or FAISS)"
            )

        # Vector provider
        if embedding_client and thread_safe_faiss:
            try:
                logger.info("Creating vector provider...")
                vector_provider = VectorFAISSProvider(
                    index_manager=thread_safe_faiss,
                    embedding_client=embedding_client,
                    model_name=config.embedding_model,
                    reranker_client=reranker_client,
                    topk_retrieve=getattr(config, "topk_retrieve", 100),
                    topk_rerank=getattr(config, "topk_rerank", 40),
                )
                logger.info(
                    "✓ Vector provider initialized, is_available=%s",
                    vector_provider.is_available,
                )
            except Exception as exc:
                logger.error(
                    "✗ Vector provider initialization failed: %s", exc, exc_info=True
                )
                logger.warning("Vector provider initialization failed: %s", exc)
        else:
            logger.info(
                "Skipping vector provider initialization (missing embedding client or FAISS)"
            )

    except Exception as exc:
        logger.error("✗ Search stack initialization failed: %s", exc, exc_info=True)

    return embedding_client, thread_safe_faiss, reranker_client, vector_provider


def _build_worker_stack(
    config: Any,
    db: Any,
    worker_control_service: WorkerControlService,
    embedding_client: Optional[EmbeddingClient],
    thread_safe_faiss: Optional[ThreadSafeFAISSManager],
) -> tuple[Optional[BackgroundEmbeddingWorker], Optional[WorkerPool]]:
    """Initialize background embedding worker and worker pool if possible."""
    if not embedding_client:
        logger.info("Skipping background worker initialization (no embedding client)")
        return None, None

    try:
        poll_interval = float(os.getenv("CHL_WORKER_POLL_INTERVAL", "5.0"))
        batch_size = int(os.getenv("CHL_WORKER_BATCH_SIZE", "10"))
        auto_start = os.getenv("CHL_WORKER_AUTO_START", "1") != "0"

        worker = BackgroundEmbeddingWorker(
            session_factory=db.get_session,
            embedding_client=embedding_client,
            model_name=config.embedding_model,
            faiss_manager=thread_safe_faiss,  # May be None; worker handles this
            poll_interval=poll_interval,
            batch_size=batch_size,
            max_tokens=8000,
            skills_enabled=bool(getattr(config, "skills_enabled", True)),
        )

        pool = WorkerPool(worker)
        worker_control_service.set_pool_getter(lambda: pool)

        if auto_start:
            worker.start()
            logger.info(
                "Background embedding worker started (poll_interval=%ss, batch_size=%s)",
                poll_interval,
                batch_size,
            )
        else:
            logger.info(
                "Background embedding worker initialized but not started "
                "(CHL_WORKER_AUTO_START=0)"
            )

        return worker, pool
    except Exception as exc:
        logger.warning("Background worker initialization failed: %s", exc)
        return None, None


def build_gpu_runtime(
    config: Config, db: Database, worker_control: WorkerControlService
) -> ModeRuntime:
    """Build GPU-capable ModeRuntime using FAISS and background worker."""
    (
        embedding_client,
        thread_safe_faiss,
        reranker_client,
        vector_provider,
    ) = _build_embedding_stack(config, db)

    # Decide primary provider based on vector availability
    primary_provider = (
        "vector_faiss"
        if vector_provider is not None and vector_provider.is_available
        else "sqlite_text"
    )
    logger.info("Determined primary provider: %s", primary_provider)

    search_service: Optional[SearchService]
    try:
        search_service = SearchService(
            primary_provider=primary_provider,
            fallback_enabled=True,
            max_retries=getattr(config, "search_fallback_retries", 1),
            vector_provider=vector_provider,
        )
        logger.info(
            "✓ Search service initialized with primary provider: %s", primary_provider
        )
    except Exception as exc:
        logger.error(
            "✗ Search service initialization completely failed: %s", exc, exc_info=True
        )
        logger.warning("Search service initialization failed: %s", exc)
        search_service = None

    background_worker, worker_pool = _build_worker_stack(
        config, db, worker_control, embedding_client, thread_safe_faiss
    )

    return ModeRuntime(
        search_service=search_service,
        thread_safe_faiss=thread_safe_faiss,
        operations_mode_adapter=GpuOperationsModeAdapter(
            embedding_client=embedding_client,
            thread_safe_faiss=thread_safe_faiss,
            vector_provider=vector_provider,
            session_factory=db.get_session,
            model_name=getattr(config, "embedding_model", None),
            skills_enabled=bool(getattr(config, "skills_enabled", True)),
        ),
        diagnostics_adapter=GpuDiagnosticsAdapter(),
        background_worker=background_worker,
        worker_pool=worker_pool,
    )
