#!/usr/bin/env python
"""Rebuild FAISS index from embeddings table

Usage:
    python scripts/rebuild_index.py

Environment Variables:
    CHL_DATABASE_PATH: Path to SQLite database (default: <experience_root>/chl.db; relative resolves under <experience_root>)
    CHL_EMBEDDING_MODEL: Embedding model name (default: Qwen/Qwen3-Embedding-0.6B)
    CHL_FAISS_INDEX_PATH: FAISS index directory (default: <experience_root>/faiss_index; relative resolves under <experience_root>)

This script will:
1. Clear existing FAISS index and metadata
2. Load all embeddings from the database
3. Rebuild the FAISS index
4. Save the index to disk

Preconditions:
- Database exists and has embeddings
- ML/FAISS dependencies installed (pip install -e ".[ml]")
- Sufficient disk space for index files

Example:
    # Basic usage
    python scripts/rebuild_index.py

    # With custom paths
    CHL_DATABASE_PATH=data/test.db python scripts/rebuild_index.py
"""
import os
import sys
import logging
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config import get_config
from src.storage.database import Database
from src.embedding.client import EmbeddingClient
from src.search.faiss_index import FAISSIndexManager
from src.search.vector_provider import VectorFAISSProvider

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def main():
    """Rebuild FAISS index from embeddings"""
    try:
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

            # Initialize vector provider (has rebuild logic)
            provider = VectorFAISSProvider(
                index_manager=index_manager,
                embedding_client=embedding_client,
                model_name=config.embedding_model,
                reranker_client=None,  # Not needed for rebuild
                topk_retrieve=config.topk_retrieve,
                topk_rerank=config.topk_rerank
            )

            # Rebuild index
            logger.info("Starting FAISS index rebuild...")
            provider.rebuild_index(session)

            logger.info("✓ FAISS index rebuild completed successfully!")

            # Print statistics
            logger.info(f"  Total vectors: {index_manager.index.ntotal}")
            logger.info(f"  Dimension: {index_manager.dimension}")
            logger.info(f"  Model: {config.embedding_model}")
            logger.info(f"  Index path: {index_manager.index_path}")

    except Exception as e:
        logger.error(f"✗ Index rebuild failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
