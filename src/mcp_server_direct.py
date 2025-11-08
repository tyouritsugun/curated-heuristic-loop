#!/usr/bin/env python3
"""CHL MCP Server implementation using fastmcp"""
import json
import sys
from pathlib import Path
from logging.handlers import RotatingFileHandler

# Add parent directory to path for absolute imports
src_dir = Path(__file__).parent
if str(src_dir.parent) not in sys.path:
    sys.path.insert(0, str(src_dir.parent))

from typing import Dict, Any, List, Optional
from fastmcp import FastMCP

from src.config import get_config
from src.storage.database import init_database
from src.storage.repository import CategoryRepository, ExperienceRepository, CategoryManualRepository
from src.search.service import SearchService
import logging

logger = logging.getLogger(__name__)


from src.mcp.utils import create_error_response
from src.mcp.handlers_entries import (
    make_read_entries_handler,
    make_write_entry_handler,
    make_update_entry_handler,
    make_delete_entry_handler,
)
from src.mcp.handlers_guidelines import make_get_guidelines_handler

# Initialize MCP server
mcp = FastMCP("CHL MCP Server")

SERVER_VERSION = "1.1.0"

TOOL_INDEX = [
    {
        "name": "list_categories",
        "description": "List all available category shelves with code and name.",
        "example": {}
    },
    {
        "name": "read_entries",
        "description": "Fetch experiences or manuals by ids or semantic query.",
        "example": {
            "entity_type": "experience",
            "category_code": "PGS",
            "query": "handoff checklist"
        },
    },
    {
        "name": "write_entry",
        "description": "Create a new experience or manual in a category.",
        "example": {
            "entity_type": "experience",
            "category_code": "PGS",
            "data": {
                "section": "useful",
                "title": "Review breakpoints before spec",
                "playbook": "Confirm responsive states with design before writing HTML."
            },
        },
    },
    {
        "name": "update_entry",
        "description": "Update an existing experience or manual by id.",
        "example": {
            "entity_type": "manual",
            "category_code": "PGS",
            "entry_id": "MNL-PGS-20250115-104200123456",
            "updates": {"summary": "Adds audit checklist step."}
        },
    },
    {
        "name": "delete_entry",
        "description": "Delete a manual entry (experiences cannot be deleted via MCP).",
        "example": {
            "entity_type": "manual",
            "category_code": "PGS",
            "entry_id": "MNL-PGS-20250115-104200123456"
        },
    },
    {
        "name": "get_guidelines",
        "description": "Return the generator or evaluator workflow manual seeded in GLN.",
        "example": {"guide_type": "generator"}
    },
]

# Global state (initialized on startup)
config = None
db = None
search_service: Optional[SearchService] = None
_initialized = False


def _build_categories_payload() -> Dict[str, Any]:
    """Collect categories payload shared between startup broadcast and tool"""
    if db is None:
        raise RuntimeError("Server not initialized. Check configuration.")

    with db.session_scope() as session:
        category_repo = CategoryRepository(session)
        categories_db = category_repo.get_all()

        # Allow empty set (fresh installs before seeding)
        categories = [
            {"code": cat.code, "name": cat.name}
            for cat in categories_db
        ]

    return {"categories": categories}


def _setup_logging(config) -> None:
    """Configure root logger with console and rotating file handler

    - Logs to stdout and to a file under <CHL_EXPERIENCE_ROOT>/log/chl_server.log
    - Rotates at ~5MB, keeping 3 backups
    - Level controlled by CHL_LOG_LEVEL (default INFO)
    """
    root = logging.getLogger()
    # Map string level to numeric
    level = getattr(logging, str(getattr(config, 'log_level', 'INFO')).upper(), logging.INFO)
    root.setLevel(level)

    # Create formatters
    fmt = logging.Formatter(
        fmt="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Avoid duplicate handlers if reloaded
    existing_targets = set()
    for h in list(root.handlers):
        target = getattr(h, 'baseFilename', None) or getattr(h, 'stream', None)
        existing_targets.add(target)

    # Console handler
    import sys as _sys
    if getattr(_sys, 'stdout', None) not in existing_targets:
        ch = logging.StreamHandler()
        ch.setLevel(level)
        ch.setFormatter(fmt)
        root.addHandler(ch)

    # File handler under <experience_root>/log/
    log_dir = Path(getattr(config, 'experience_root', 'data')) / 'log'
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / 'chl_server.log'

    if str(log_path) not in existing_targets:
        fh = RotatingFileHandler(str(log_path), maxBytes=5 * 1024 * 1024, backupCount=3)
        fh.setLevel(level)
        fh.setFormatter(fmt)
        root.addHandler(fh)

    logging.getLogger(__name__).info(f"Logging initialized. Level={logging.getLevelName(level)}, file={log_path}")


@mcp.tool()
def list_categories() -> Dict[str, Any]:
    """
    Return the current set of available category shelves.

    Example:
        {}
    
    Returns:
        Dictionary with 'categories' list containing code and name for each category
    """
    try:
        payload = _build_categories_payload()
        return payload
    
    except (RuntimeError, ValueError) as e:
        return create_error_response("INVALID_REQUEST", str(e), retryable=False)
    
    except Exception as e:
        return create_error_response("SERVER_ERROR", str(e), retryable=False)


def _init_search_service(session, config) -> SearchService:
    """Initialize search service ensuring vector search is available.

    Raises:
        RuntimeError: If required ML dependencies or GGUF models are missing.
    """
    try:
        from src.embedding.client import EmbeddingClient
        from src.embedding.reranker import RerankerClient
        from src.search.faiss_index import FAISSIndexManager
        from src.search.vector_provider import VectorFAISSProvider
    except ImportError as e:
        message = (
            "Vector search requires the ML dependencies. "
            "Install them with `uv sync --python 3.11 --extra ml` and rerun the server."
        )
        logger.error(message)
        raise RuntimeError(message) from e

    # Helper to redirect native stderr (llama.cpp) to server log
    from contextlib import contextmanager
    import os as _os, sys as _sys
    @contextmanager
    def _redirect_native_stderr():
        log_dir = Path(getattr(config, 'experience_root', 'data')) / 'log'
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / 'chl_server.log'
        f = log_path.open('a')
        try:
            fd = _sys.stderr.fileno()
            saved = _os.dup(fd)
            _os.dup2(f.fileno(), fd)
            try:
                yield
            finally:
                try:
                    _os.dup2(saved, fd)
                finally:
                    _os.close(saved)
        finally:
            try:
                f.close()
            except Exception:
                pass

    embedding_client = None
    try:
        with _redirect_native_stderr():
            embedding_client = EmbeddingClient(
                model_repo=config.embedding_repo,
                quantization=config.embedding_quant,
                n_gpu_layers=0  # CPU-only by default, set >0 for GPU offloading
            )
        logger.info(f"Embedding loaded: {config.embedding_repo} [{config.embedding_quant}]")
    except FileNotFoundError:
        logger.warning(
            "Embedding model not found. Run `python scripts/setup.py --download-models` "
            "to enable vector search."
        )
    except Exception as e:
        logger.warning(f"Failed to initialize embedding model; continuing without vector search: {e}")

    faiss_manager = None
    if embedding_client is not None:
        try:
            faiss_manager = FAISSIndexManager(
                index_dir=config.faiss_index_path,
                model_name=config.embedding_model,
                dimension=embedding_client.embedding_dimension,
                session=session
            )
        except Exception as e:
            logger.warning(f"Failed to initialize FAISS index; continuing without vector search: {e}")

    reranker_client = None
    if embedding_client is not None and faiss_manager is not None:
        try:
            with _redirect_native_stderr():
                reranker_client = RerankerClient(
                    model_repo=config.reranker_repo,
                    quantization=config.reranker_quant,
                    n_gpu_layers=0  # CPU-only by default
                )
            logger.info(f"Reranker loaded: {config.reranker_repo} [{config.reranker_quant}]")
        except FileNotFoundError:
            logger.warning(
                "Reranker model not found. Run `python scripts/setup.py --download-models` "
                "to enable reranking."
            )
        except Exception as e:
            logger.warning(f"Failed to initialize reranker; continuing without reranking: {e}")

    vector_provider = None
    if embedding_client is not None and faiss_manager is not None:
        try:
            vector_provider = VectorFAISSProvider(
                session=session,
                index_manager=faiss_manager,
                embedding_client=embedding_client,
                reranker_client=reranker_client,
                topk_retrieve=getattr(config, "topk_retrieve", 100),
                topk_rerank=getattr(config, "topk_rerank", 40),
            )
        except Exception as e:
            logger.warning(f"Vector provider initialization failed, falling back to sqlite_text: {e}")

    primary_provider = "sqlite_text"
    fallback_enabled = True

    if vector_provider and vector_provider.is_available:
        primary_provider = "vector_faiss"
        logger.info("✓ Vector search enabled (vector_faiss)")
        logger.info(f"  - Embedding model: {config.embedding_model}")
        logger.info(f"  - FAISS index: {faiss_manager.index.ntotal} vectors")
        logger.info(f"  - Reranker: {config.reranker_model}")
    else:
        logger.warning(
            "Vector search unavailable. Running in text-only mode; install ML extras or rebuild the FAISS index."
        )

    return SearchService(
        session,
        primary_provider=primary_provider,
        fallback_enabled=fallback_enabled,
        max_retries=getattr(config, "search_fallback_retries", 1),
        vector_provider=vector_provider,
    )


def _build_handshake_payload() -> Dict[str, Any]:
    """Return startup instructions shared with MCP clients."""
    category_payload = _build_categories_payload()
    search_payload: Dict[str, Any] = {
        "primary_provider": getattr(search_service, "primary_provider_name", "sqlite_text"),
        "vector_available": False,
        "fallback_enabled": getattr(search_service, "fallback_enabled", True),
        "topk": {
            "retrieve": getattr(config, "topk_retrieve", None),
            "rerank": getattr(config, "topk_rerank", None),
        },
    }

    try:
        vector_provider = getattr(search_service, "get_vector_provider", lambda: None)()
        search_payload["vector_available"] = bool(vector_provider and vector_provider.is_available)
    except Exception:
        search_payload["vector_available"] = False

    if not search_payload["vector_available"]:
        search_payload["status"] = "degraded"
        search_payload["hint"] = (
            "Vector search disabled; responses use sqlite_text fallback. "
            "Install ML extras and rebuild embeddings to restore semantic search."
        )
    else:
        search_payload["status"] = "ok"

    return {
        "version": SERVER_VERSION,
        "workflow_mode": {
            "default": "generator",
            "notes": (
                "Sessions start in Generator mode. Load generator guidelines first and "
                "switch to evaluator deliberately when reflecting on completed work."
            ),
            "guidelines": {
                "generator": "Use guide_type='generator' to fetch the authoring manual.",
                "evaluator": "Use guide_type='evaluator' only after generator work is done."
            },
        },
        "tool_index": TOOL_INDEX,
        "search": search_payload,
        **category_payload,
    }


def init_server():
    """Initialize server with configuration"""
    global config, db, search_service, _initialized

    if _initialized:
        logger.info("init_server() called after initialization; reusing existing services.")
        try:
            mcp.instructions = json.dumps(_build_handshake_payload())
        except (RuntimeError, ValueError) as e:
            mcp.instructions = json.dumps(create_error_response("INVALID_REQUEST", str(e), retryable=False))
        except Exception as e:
            mcp.instructions = json.dumps(create_error_response("SERVER_ERROR", str(e), retryable=False))
        return

    # Load configuration
    config = get_config()

    # Setup logging
    try:
        _setup_logging(config)
    except Exception as e:
        # Fall back silently; logging to stdout will still work
        print(f"Warning: failed to initialize file logging: {e}")

    # Initialize database
    db = init_database(config.database_path, config.database_echo)
    try:
        db.create_tables()
    except Exception:
        # If tables already exist or creation fails harmlessly, proceed
        pass

    # Validate database initialization (non-fatal if empty; seeding can run later)
    try:
        with db.session_scope() as session:
            from src.storage.repository import CategoryRepository
            cat_repo = CategoryRepository(session)
            _ = cat_repo.get_all()
    except Exception as e:
        raise RuntimeError(
            "Failed to validate CHL database initialization. "
            "Ensure `scripts/setup.py` has completed successfully."
        ) from e

    # Initialize search service with mandatory vector search components
    # Note: Using long-lived session for server lifetime
    session = db.get_session()
    try:
        search_service = _init_search_service(session, config)
    except Exception:
        session.close()
        raise
    # Register moved tools (search-related) after services are ready
    mcp.tool()(make_read_entries_handler(db, config, search_service))
    mcp.tool()(make_write_entry_handler(db, config, search_service))
    mcp.tool()(make_update_entry_handler(db, config, search_service))
    mcp.tool()(make_delete_entry_handler(db, config, search_service))
    mcp.tool()(make_get_guidelines_handler(db))

    # Broadcast categories in initial handshake
    try:
        mcp.instructions = json.dumps(_build_handshake_payload())
    except (RuntimeError, ValueError) as e:
        mcp.instructions = json.dumps(create_error_response("INVALID_REQUEST", str(e), retryable=False))
    except Exception as e:
        mcp.instructions = json.dumps(create_error_response("SERVER_ERROR", str(e), retryable=False))
    else:
        _initialized = True


# Initialize on module load
init_server()


if __name__ == "__main__":
    # Run MCP server when executed directly
    mcp.run()
