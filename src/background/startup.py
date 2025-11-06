"""Startup logic for embedding queue."""
import logging

logger = logging.getLogger(__name__)


def requeue_pending_embeddings(session):
    """
    Requeue any entries stuck in 'pending' state on startup.

    This handles crash recovery: entries that were pending when the API
    shut down need to be reprocessed.

    Note: Since we use embedding_status='pending' as queue state, this
    is essentially a no-op (entries are already pending). But we log
    the count for visibility.

    Args:
        session: SQLAlchemy session

    Returns:
        Total number of pending embeddings found
    """
    from src.storage.schema import Experience, CategoryManual

    pending_exp = session.query(Experience).filter(
        Experience.embedding_status == 'pending'
    ).count()

    pending_man = session.query(CategoryManual).filter(
        CategoryManual.embedding_status == 'pending'
    ).count()

    total = pending_exp + pending_man

    if total > 0:
        logger.info(
            f"Found {total} pending embeddings on startup "
            f"({pending_exp} experiences, {pending_man} manuals). "
            "Workers will process them."
        )
    else:
        logger.info("No pending embeddings on startup")

    return total
