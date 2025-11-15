"""FastAPI server entrypoint for CHL API."""

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, FileResponse
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
from src.api.metrics import metrics
from src.modes import build_mode_runtime

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
background_worker = None  # BackgroundEmbeddingWorker instance
worker_pool = None  # WorkerPool wrapper
mode_runtime = None  # ModeRuntime instance with search/worker wiring


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
    # Suppress noisy access logs (HTMX polling) unless warnings/errors occur
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for startup and shutdown."""
    global config, db, search_service, thread_safe_faiss
    global settings_service, operations_service, worker_control_service, telemetry_service
    global background_worker, worker_pool, mode_runtime

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

        # Initialize background embedding worker (will be set up by mode runtime)
        background_worker = None
        worker_pool = None

        def worker_probe():
            if worker_pool:
                return worker_pool.get_status()
            return None

        # Initialize search service and related components via mode runtime
        mode_runtime = build_mode_runtime(config, db, worker_control_service)
        search_service = mode_runtime.search_service
        thread_safe_faiss = mode_runtime.thread_safe_faiss
        background_worker = mode_runtime.background_worker
        worker_pool = mode_runtime.worker_pool

        try:
            if hasattr(settings_service, "set_mode_runtime"):
                settings_service.set_mode_runtime(mode_runtime)
        except Exception:
            logger.debug("Failed to attach mode runtime to settings service", exc_info=True)

        # Attach mode-specific operations adapter (CPU-only vs vector-capable)
        try:
            adapter = getattr(mode_runtime, "operations_adapter", None)
            if adapter is not None and hasattr(operations_service, "set_mode_adapter"):
                operations_service.set_mode_adapter(adapter)
        except Exception:
            # Operations can still function without an adapter; log at debug level
            logger.debug("Failed to attach operations mode adapter", exc_info=True)

        telemetry_service = TelemetryService(
            session_factory=db.get_session,
            queue_probe=queue_probe,
            worker_probe=worker_probe,
            interval_seconds=getattr(config, "telemetry_interval", 5),
            meta_provider=lambda: {"search_mode": getattr(config, "search_mode", None)},
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

        # Stop background worker before operations service
        if background_worker and background_worker.is_running():
            try:
                background_worker.stop(timeout=10.0)
                logger.info("Background embedding worker stopped")
            except Exception as e:
                logger.warning(f"Error stopping background worker: {e}")

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
def root(request: Request):
    """Return basic service info for smoke checks.

    UI remains at /settings.
    """
    accept = request.headers.get("accept", "")
    if "text/html" in accept and "application/json" not in accept:
        return RedirectResponse(url="/settings", status_code=307)
    return {
        "service": "CHL API",
        "version": "0.2.0",
        "status": "running",
    }


# Configure logging on module import
configure_logging()


# Mount static assets for the web UI
static_dir = Path(__file__).resolve().parent / "web" / "static"
static_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

favicon_path = static_dir / "favicon.ico"


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    """Serve the dashboard favicon."""
    return FileResponse(favicon_path)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
