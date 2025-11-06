"""FastAPI server entrypoint for CHL API."""

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import logging
import json
import time
import threading
from datetime import datetime

from src.config import Config
from src.storage.database import Database
from src.search.service import SearchService
from src.api.metrics import metrics

# Import routers
from src.api.routers.health import router as health_router
from src.api.routers.categories import router as categories_router
from src.api.routers.entries import router as entries_router
from src.api.routers.search import router as search_router
from src.api.routers.guidelines import router as guidelines_router

logger = logging.getLogger(__name__)

# Global singletons (initialized on startup)
config = None
db = None
search_service = None
faiss_lock = None  # Thread-safe FAISS lock


class JSONFormatter(logging.Formatter):
    """JSON log formatter for structured logging."""

    def format(self, record):
        log_data = {
            "timestamp": datetime.utcnow().isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
        }
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_data)


def configure_logging():
    """Configure structured JSON logging."""
    handler = logging.StreamHandler()
    handler.setFormatter(JSONFormatter())
    logging.root.handlers = [handler]
    logging.root.setLevel(logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for startup and shutdown."""
    global config, db, search_service, faiss_lock

    logger.info("Starting CHL API server...")

    try:
        # Load configuration
        config = Config()
        logger.info("Configuration loaded")

        # Initialize database
        db = Database(config.database_path)
        db.init_database()
        logger.info(f"Database initialized: {config.database_path}")

        # Initialize FAISS lock
        faiss_lock = threading.RLock()
        logger.info("FAISS lock initialized")

        # Initialize search service (sessionless for thread-safety)
        try:
            from src.embedding.client import EmbeddingClient
            from src.search.faiss_index import FAISSIndexManager
            from src.search.vector_provider import VectorFAISSProvider
            from src.embedding.reranker import RerankerClient

            # Try to initialize embedding components
            embedding_client = None
            faiss_manager = None
            reranker_client = None
            vector_provider = None

            try:
                embedding_client = EmbeddingClient(
                    model_repo=config.embedding_model,
                    quantization=config.embedding_quant,
                    n_gpu_layers=0  # CPU-only
                )
                logger.info(f"Embedding client loaded: {config.embedding_model}")
            except Exception as e:
                logger.warning(f"Embedding client not available: {e}")

            if embedding_client:
                try:
                    # Create temporary session for FAISS index loading
                    with db.session_scope() as temp_session:
                        faiss_manager = FAISSIndexManager(
                            index_dir=config.faiss_index_path,
                            model_name=config.embedding_model,
                            dimension=embedding_client.embedding_dimension,
                            session=temp_session
                        )
                        logger.info(f"FAISS index loaded: {faiss_manager.index.ntotal} vectors")
                except Exception as e:
                    logger.warning(f"FAISS index not available: {e}")

            if embedding_client and faiss_manager:
                try:
                    reranker_client = RerankerClient(
                        model_repo=config.reranker_repo,
                        quantization=config.reranker_quant,
                        n_gpu_layers=0
                    )
                    logger.info(f"Reranker loaded: {config.reranker_repo}")
                except Exception as e:
                    logger.warning(f"Reranker not available: {e}")

            if embedding_client and faiss_manager:
                try:
                    vector_provider = VectorFAISSProvider(
                        index_manager=faiss_manager,
                        embedding_client=embedding_client,
                        reranker_client=reranker_client,
                        topk_retrieve=getattr(config, "topk_retrieve", 100),
                        topk_rerank=getattr(config, "topk_rerank", 40),
                    )
                    logger.info("Vector provider initialized")
                except Exception as e:
                    logger.warning(f"Vector provider initialization failed: {e}")

            primary_provider = "vector_faiss" if (vector_provider and vector_provider.is_available) else "sqlite_text"

            search_service = SearchService(
                primary_provider=primary_provider,
                fallback_enabled=True,
                max_retries=getattr(config, "search_fallback_retries", 1),
                vector_provider=vector_provider,
            )
            logger.info(f"Search service initialized with primary provider: {primary_provider}")

        except Exception as e:
            logger.warning(f"Search service initialization failed: {e}")
            search_service = None

        logger.info("CHL API server started successfully")

        yield  # Application is running

    except Exception as e:
        logger.exception(f"Failed to start server: {e}")
        raise

    finally:
        # Shutdown cleanup
        logger.info("Shutting down CHL API server...")
        if db:
            db.close()
            logger.info("Database connection closed")
        logger.info("CHL API server shut down")


# Create FastAPI app
app = FastAPI(
    title="CHL API",
    description="Curated Heuristic Loop API for experience management",
    version="0.2.0",
    lifespan=lifespan,
)

# Configure CORS (allow all origins for local development)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Metrics middleware
@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    """Track request metrics."""
    start_time = time.time()

    response = await call_next(request)

    duration = time.time() - start_time
    metrics.increment(
        f"api_requests_total.{request.url.path}.{request.method}.{response.status_code}"
    )
    metrics.observe(
        f"api_request_duration_seconds.{request.url.path}",
        duration
    )

    return response


# Register routers
app.include_router(health_router)
app.include_router(categories_router)
app.include_router(entries_router)
app.include_router(search_router)
app.include_router(guidelines_router)


@app.get("/")
def root():
    """Root endpoint."""
    return {
        "service": "CHL API",
        "version": "0.2.0",
        "status": "running",
        "docs": "/docs",
        "health": "/health",
    }


# Configure logging on module import
configure_logging()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
