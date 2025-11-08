"""FastAPI server entrypoint for CHL API."""

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
import os
import logging
import json
import time
import threading
from datetime import datetime
from pathlib import Path

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
from src.api.routers.admin import router as admin_router
from src.api.routers.settings import router as settings_router
from src.api.routers.operations import router as operations_router
from src.api.routers.workers import router as workers_router
from src.api.routers.telemetry import router as telemetry_router
from src.api.routers.ui import router as ui_router
from src.web.docs import router as docs_router
from src.services.settings_service import SettingsService
from src.services.operations_service import OperationsService
from src.services.worker_control import WorkerControlService
from src.services.telemetry_service import TelemetryService

logger = logging.getLogger(__name__)

# Global singletons (initialized on startup)
config = None
db = None
search_service = None
thread_safe_faiss = None  # ThreadSafeFAISSManager instance
settings_service = None
operations_service = None
worker_control_service = None
telemetry_service = None


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
    global config, db, search_service, thread_safe_faiss
    global settings_service, operations_service, worker_control_service, telemetry_service

    logger.info("Starting CHL API server...")

    try:
        # Load configuration
        config = Config()
        logger.info("Configuration loaded")

        # Initialize database
        db = Database(config.database_path)
        db.init_database()
        logger.info(f"Database initialized: {config.database_path}")

        # Initialize service layer singletons
        settings_service = SettingsService(db.get_session, config.experience_root)
        worker_control_service = WorkerControlService(db.get_session)
        operations_mode = os.getenv("CHL_OPERATIONS_MODE")
        operations_service = OperationsService(db.get_session, mode=operations_mode)
        logger.info("Core services initialized (settings, worker control, operations)")

        def queue_probe():
            session = db.get_session()
            try:
                return worker_control_service.queue_depth(session)
            finally:
                session.close()

        def worker_probe():
            return None

        # Initialize search service (sessionless for thread-safety)
        try:
            from src.embedding.client import EmbeddingClient
            from src.search.thread_safe_faiss import initialize_faiss_with_recovery, ThreadSafeFAISSManager
            from src.search.vector_provider import VectorFAISSProvider
            from src.embedding.reranker import RerankerClient

            # Try to initialize embedding components
            embedding_client = None
            faiss_manager = None
            reranker_client = None
            vector_provider = None

            try:
                embedding_client = EmbeddingClient(
                    model_repo=config.embedding_repo,
                    quantization=config.embedding_quant,
                    n_gpu_layers=0  # CPU-only
                )
                logger.info(f"Embedding client loaded: {config.embedding_repo}:{config.embedding_quant}")
            except Exception as e:
                logger.warning(f"Embedding client not available: {e}")

            if embedding_client:
                try:
                    # Use recovery logic to load FAISS index with automatic fallback
                    with db.session_scope() as temp_session:
                        faiss_manager = initialize_faiss_with_recovery(
                            config, temp_session, embedding_client
                        )

                    if faiss_manager:
                        # Wrap in ThreadSafeFAISSManager for concurrency control
                        thread_safe_faiss = ThreadSafeFAISSManager(
                            faiss_manager=faiss_manager,
                            save_policy=config.faiss_save_policy,
                            save_interval=config.faiss_save_interval,
                            rebuild_threshold=config.faiss_rebuild_threshold,
                        )
                        logger.info(
                            f"ThreadSafeFAISSManager initialized: policy={config.faiss_save_policy}, "
                            f"threshold={config.faiss_rebuild_threshold}"
                        )
                    else:
                        logger.warning("FAISS index recovery failed, will use text search fallback")
                except Exception as e:
                    logger.warning(f"FAISS initialization failed: {e}")

            if embedding_client and thread_safe_faiss:
                try:
                    reranker_client = RerankerClient(
                        model_repo=config.reranker_repo,
                        quantization=config.reranker_quant,
                        n_gpu_layers=0
                    )
                    logger.info(f"Reranker loaded: {config.reranker_repo}:{config.reranker_quant}")
                except Exception as e:
                    logger.warning(f"Reranker not available: {e}")

            if embedding_client and thread_safe_faiss:
                try:
                    vector_provider = VectorFAISSProvider(
                        index_manager=thread_safe_faiss,
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

        telemetry_service = TelemetryService(
            session_factory=db.get_session,
            queue_probe=queue_probe,
            worker_probe=worker_probe,
            interval_seconds=getattr(config, "telemetry_interval", 5),
        )
        await telemetry_service.start()
        logger.info("Telemetry service started")

        logger.info("CHL API server started successfully")

        yield  # Application is running

    except Exception as e:
        logger.exception(f"Failed to start server: {e}")
        raise

    finally:
        # Shutdown cleanup
        logger.info("Shutting down CHL API server...")

        if telemetry_service:
            try:
                await telemetry_service.stop()
            except Exception as e:
                logger.warning(f"Error stopping telemetry service: {e}")

        if operations_service:
            try:
                operations_service.shutdown()
            except Exception as e:
                logger.warning(f"Error shutting down operations service: {e}")

        # Shutdown ThreadSafeFAISSManager (stops periodic saver if running)
        if thread_safe_faiss:
            try:
                thread_safe_faiss.shutdown()
                logger.info("ThreadSafeFAISSManager shut down")
            except Exception as e:
                logger.warning(f"Error shutting down ThreadSafeFAISSManager: {e}")

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
app.include_router(admin_router)
app.include_router(settings_router)
app.include_router(operations_router)
app.include_router(workers_router)
app.include_router(telemetry_router)
app.include_router(ui_router)
app.include_router(docs_router)


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


# Mount static assets for the web UI
static_dir = Path(__file__).resolve().parent / "web" / "static"
static_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
