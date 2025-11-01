#!/usr/bin/env python
"""Sync embeddings for pending/failed entities

Usage:
    python scripts/sync_embeddings.py [--retry-failed] [--max-count N]

Options:
    --retry-failed    Retry failed embeddings in addition to pending
    --max-count N     Process at most N entities

Environment Variables:
    CHL_DATABASE_PATH: Path to SQLite database (default: <experience_root>/chl.db; relative resolves under <experience_root>)
    CHL_EMBEDDING_MODEL: Embedding model name (default: Qwen/Qwen3-Embedding-0.6B)
    CHL_FAISS_INDEX_PATH: FAISS index directory (default: <experience_root>/faiss_index; relative resolves under <experience_root>)

This script will:
1. Find all entities with embedding_status='pending' or 'failed'
2. Generate embeddings for them
3. Store in embeddings table
4. Update FAISS index incrementally
5. Update entity status to 'embedded'

Preconditions:
- Database exists with experiences/manuals
- ML/FAISS dependencies installed (pip install -e ".[ml]")
- Embedding model accessible

Example:
    # Process pending embeddings
    python scripts/sync_embeddings.py

    # Also retry failed embeddings
    python scripts/sync_embeddings.py --retry-failed

    # Process at most 100 entities
    python scripts/sync_embeddings.py --max-count 100
"""
import os
import sys
import argparse
import os
import logging
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config import get_config
from src.storage.database import Database
from src.embedding.client import EmbeddingClient
from src.embedding.service import EmbeddingService
from src.search.faiss_index import FAISSIndexManager

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def main():
    """Sync embeddings for pending/failed entities"""
    # Parse arguments
    parser = argparse.ArgumentParser(description='Sync embeddings for pending/failed entities')
    parser.add_argument(
        '--retry-failed',
        action='store_true',
        help='Retry failed embeddings in addition to pending'
    )
    parser.add_argument(
        '--max-count',
        type=int,
        default=None,
        help='Maximum number of entities to process'
    )
    parser.add_argument(
        '--data-path',
        help='Path to data directory (sets CHL_EXPERIENCE_ROOT and default CHL_DATABASE_PATH)'
    )

    args = parser.parse_args()

    try:
        # Allow CLI to set env before loading config
        if args.data_path:
            os.environ["CHL_EXPERIENCE_ROOT"] = args.data_path
            os.environ.setdefault("CHL_DATABASE_PATH", os.path.join(args.data_path, "chl.db"))

        # Load configuration
        logger.info("Loading configuration...")
        config = get_config()

        # Initialize database
        logger.info(f"Connecting to database: {config.database_path}")
        db = Database(config.database_path, echo=config.database_echo)
        db.init_database()

        # Initialize GGUF embedding client
        logger.info(f"Loading embedding model: {config.embedding_repo} [{config.embedding_quant}]")
        embedding_client = EmbeddingClient(
            model_repo=config.embedding_repo,
            quantization=config.embedding_quant,
            normalize=True
        )

        # Initialize FAISS index manager
        logger.info(f"Initializing FAISS index: {config.faiss_index_path}")
        with db.session_scope() as session:
            index_manager = FAISSIndexManager(
                index_dir=config.faiss_index_path,
                model_name=config.embedding_model,
                dimension=embedding_client.embedding_dimension,
                session=session
            )

            # Initialize embedding service
            embedding_service = EmbeddingService(
                session=session,
                embedding_client=embedding_client,
                faiss_index_manager=index_manager
            )

            # Process pending embeddings
            logger.info("Processing pending embeddings...")
            pending_stats = embedding_service.process_pending(max_count=args.max_count)

            logger.info(
                f"✓ Pending embeddings processed: "
                f"{pending_stats['processed']} total, "
                f"{pending_stats['succeeded']} succeeded, "
                f"{pending_stats['failed']} failed"
            )

            # Retry failed if requested
            if args.retry_failed:
                logger.info("Retrying failed embeddings...")
                failed_stats = embedding_service.retry_failed(max_count=args.max_count)

                logger.info(
                    f"✓ Failed embeddings retried: "
                    f"{failed_stats['retried']} total, "
                    f"{failed_stats['succeeded']} succeeded, "
                    f"{failed_stats['failed']} still failed"
                )

            # Save FAISS index
            logger.info("Saving FAISS index...")
            index_manager.save()

            logger.info("✓ Embedding sync completed successfully!")

    except Exception as e:
        logger.error(f"✗ Embedding sync failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
