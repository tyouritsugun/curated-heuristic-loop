"""Shared helper for regenerating embeddings after bulk data changes."""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Tuple

from src.storage.database import Database
from tqdm import tqdm
import os
import sys
from contextlib import contextmanager


@contextmanager
def _redirect_stderr(to_path: Path):
    """Temporarily redirect OS-level stderr (fd=2) to a file.

    Captures chatty native output from llama.cpp/Metal during model load/encode.
    """
    to_path.parent.mkdir(parents=True, exist_ok=True)
    # Open log file in append mode
    f = to_path.open("a")
    try:
        stderr_fd = sys.stderr.fileno()
        saved_fd = os.dup(stderr_fd)
        os.dup2(f.fileno(), stderr_fd)
        try:
            yield
        finally:
            try:
                os.dup2(saved_fd, stderr_fd)
            finally:
                os.close(saved_fd)
    finally:
        try:
            f.close()
        except Exception:
            pass


def auto_sync_embeddings(
    db: Database,
    data_path: Path,
    database_path: Path,
    logger: logging.Logger,
    *,
    retry_failed: bool = True,
) -> Tuple[bool, str | None]:
    """Regenerate embeddings/FAISS index after imports or seeding.

    Args:
        db: Initialised Database instance.
        data_path: Project data directory (used for defaults).
        database_path: Absolute path to SQLite database file.
        logger: Logger to emit status updates.
        retry_failed: When true, retry rows previously marked failed.

    Returns:
        Tuple of (success flag, failure reason message or None).
    """
    try:
        from src.config import get_config
        from src.embedding.client import EmbeddingClient, EmbeddingClientError
        from src.embedding.service import EmbeddingService
        from src.search.faiss_index import FAISSIndexManager
    except ImportError as exc:
        msg = (
            "ML dependencies missing. Install extras with "
            "`uv sync --python 3.11 --extra ml` then rerun "
            "`python scripts/sync_embeddings.py --retry-failed`."
        )
        logger.warning("Skipping automatic embedding sync: %s", msg)
        return False, msg

    os.environ.setdefault("CHL_EXPERIENCE_ROOT", str(data_path))
    os.environ.setdefault("CHL_DATABASE_PATH", str(database_path))

    try:
        config = get_config()
    except Exception as exc:  # pragma: no cover - defensive
        msg = f"failed to load configuration ({exc})"
        logger.warning("Skipping automatic embedding sync: %s", msg)
        return False, msg

    # Redirect noisy native stderr to server log while loading/encoding
    log_path = Path(config.experience_root) / "log" / "chl_server.log"
    try:
        with _redirect_stderr(log_path):
            embedding_client = EmbeddingClient(
                model_repo=config.embedding_repo,
                quantization=config.embedding_quant,
                normalize=True,
            )
    except (EmbeddingClientError, FileNotFoundError) as exc:
        msg = (
            f"embedding model unavailable ({exc}). Run "
            "`python scripts/setup.py --download-models` and retry."
        )
        logger.warning("Skipping automatic embedding sync: %s", msg)
        return False, msg
    except Exception as exc:
        msg = f"unexpected embedding error ({exc})"
        logger.warning("Skipping automatic embedding sync: %s", msg)
        return False, msg

    try:
        with db.session_scope() as session:
            with _redirect_stderr(log_path):
                index_manager = FAISSIndexManager(
                    index_dir=config.faiss_index_path,
                    model_name=config.embedding_model,
                    dimension=embedding_client.embedding_dimension,
                    session=session,
                )

                embedding_service = EmbeddingService(
                    session=session,
                    embedding_client=embedding_client,
                    faiss_index_manager=index_manager,
                )

            # Progress for pending embeddings
            pend_exp = embedding_service.get_pending_experiences()
            pend_man = embedding_service.get_pending_manuals()
            pending_pairs = [("experience", e.id) for e in pend_exp] + [("manual", m.id) for m in pend_man]
            pending_stats = {"processed": 0, "succeeded": 0, "failed": 0}
            bar_disable = not sys.stdout.isatty()
            if pending_pairs:
                for etype, eid in tqdm(pending_pairs, desc="Embedding pending", unit="item", disable=bar_disable):
                    with _redirect_stderr(log_path):
                        ok = (
                            embedding_service.generate_for_experience(eid)
                            if etype == "experience"
                            else embedding_service.generate_for_manual(eid)
                        )
                    pending_stats["processed"] += 1
                    if ok:
                        pending_stats["succeeded"] += 1
                    else:
                        pending_stats["failed"] += 1
            else:
                logger.info("No pending embeddings to process.")

            # Progress for failed embeddings (optional retry)
            retry_stats = {"retried": 0, "succeeded": 0, "failed": 0}
            if retry_failed:
                fail_exp = embedding_service.get_failed_experiences()
                fail_man = embedding_service.get_failed_manuals()
                failed_pairs = [("experience", e.id) for e in fail_exp] + [("manual", m.id) for m in fail_man]
                if failed_pairs:
                    for etype, eid in tqdm(failed_pairs, desc="Retrying failed", unit="item", disable=bar_disable):
                        # Reset status to pending before retry is handled inside service methods
                        with _redirect_stderr(log_path):
                            ok = (
                                embedding_service.generate_for_experience(eid)
                                if etype == "experience"
                                else embedding_service.generate_for_manual(eid)
                            )
                        retry_stats["retried"] += 1
                        if ok:
                            retry_stats["succeeded"] += 1
                        else:
                            retry_stats["failed"] += 1
                else:
                    logger.info("No failed embeddings to retry.")

                index_manager.save()
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("Automatic embedding sync failed: %s", exc)
        return False, str(exc)

    logger.info(
        "Automatic embedding sync completed "
        "(processed=%s, succeeded=%s, failed=%s, retried=%s, retry_succeeded=%s, still_failed=%s).",
        pending_stats["processed"],
        pending_stats["succeeded"],
        pending_stats["failed"],
        retry_stats["retried"],
        retry_stats["succeeded"],
        retry_stats["failed"],
    )
    return True, None
