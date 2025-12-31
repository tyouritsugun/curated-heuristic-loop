"""FastAPI server entrypoint for CHL API (new path).

This module is adapted from the legacy `src/api_server.py` but uses the
new runtime builder and shared `src/common` modules.
"""

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
import logging
import json
import os
import time
from datetime import datetime
from pathlib import Path

from src.common.config.config import Config
from src.common.storage.database import Database
from src.api.metrics import metrics
from src.api.runtime_builder import build_mode_runtime
from src.common.web_utils import docs as web_docs  # noqa: F401  # imported for side effects/router

from src.api.routers.health import router as health_router
from src.api.routers.categories import router as categories_router
from src.api.routers.entries import router as entries_router
from src.api.routers.search import router as search_router
from src.api.routers.session import router as session_router
from src.api.routers.guidelines import router as guidelines_router
from src.api.routers.admin import router as admin_router
from src.api.routers.settings import router as settings_router
from src.api.routers.operations import router as operations_router
from src.api.routers.workers import router as workers_router
from src.api.routers.telemetry import router as telemetry_router
initial_config = Config()

if initial_config.search_mode == "cpu":
    from src.api.routers.cpu_ui import router as ui_router
else:
    from src.api.routers.gpu_ui import router as ui_router
from src.common.web_utils.docs import router as docs_router
from src.api.services.settings_service import SettingsService
from src.api.services.operations_service import OperationsService
from src.api.services.worker_control import WorkerControlService
from src.api.services.telemetry_service import TelemetryService

logger = logging.getLogger(__name__)

# Note: All service singletons are now stored in app.state instead of module globals
# to avoid circular imports with dependencies.py


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
    log_level = getattr(logging, initial_config.log_level, logging.INFO)
    logging.root.setLevel(log_level)
    logging.getLogger("uvicorn.access").setLevel(log_level)
    # Reduce noise from high-frequency background components. Embedding and
    # telemetry internals are still logged at WARNING+ for troubleshooting,
    # but INFO-level heartbeat messages are suppressed.
    logging.getLogger("src.api.gpu.embedding_service").setLevel(log_level)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for startup and shutdown."""
    # Use app.state instead of module globals to avoid circular imports
    logger.info("Starting CHL API server...")

    try:
        app.state.config = initial_config
        logger.info("Configuration loaded")

        app.state.db = Database(app.state.config.database_path)
        app.state.db.init_database()
        logger.info("Database initialized")

        app.state.settings_service = SettingsService(app.state.db.get_session, app.state.config.experience_root)
        app.state.settings_service.set_config(app.state.config)
        app.state.worker_control_service = WorkerControlService(app.state.db.get_session)
        app.state.operations_service = OperationsService(
            session_factory=app.state.db.get_session,
            data_path=app.state.config.experience_root,
            faiss_index_path=Path(app.state.config.faiss_index_path),
        )
        logger.info("Core services initialized (settings, worker control, operations)")

        app.state.background_worker = None
        app.state.worker_pool = None

        app.state.mode_runtime = build_mode_runtime(app.state.config, app.state.db, app.state.worker_control_service)
        app.state.search_service = app.state.mode_runtime.search_service
        app.state.thread_safe_faiss = app.state.mode_runtime.thread_safe_faiss

        # Attach mode runtime to services for diagnostics and operations
        try:
            app.state.settings_service.set_mode_runtime(app.state.mode_runtime)
        except Exception as exc:
            logger.warning("Failed to attach mode runtime to SettingsService: %s", exc)

        try:
            app.state.operations_service.set_mode_adapter(getattr(app.state.mode_runtime, "operations_mode_adapter", None))
        except Exception as exc:
            logger.warning("Failed to attach mode adapter to OperationsService: %s", exc)

        # Expose background worker / pool for telemetry if available
        app.state.background_worker = getattr(app.state.mode_runtime, "background_worker", None)
        app.state.worker_pool = getattr(app.state.mode_runtime, "worker_pool", None)

        def get_queue_depth():
            with app.state.db.session_scope() as session:
                return app.state.worker_control_service.queue_depth(session)

        app.state.telemetry_service = TelemetryService(
            session_factory=app.state.db.get_session,
            queue_probe=get_queue_depth,
            worker_probe=lambda: app.state.worker_pool.get_status() if app.state.worker_pool else None,
        )
        await app.state.telemetry_service.start()
        logger.info("Telemetry service started")

        yield

    finally:
        logger.info("Shutting down CHL API server...")

        if hasattr(app.state, 'telemetry_service') and app.state.telemetry_service:
            try:
                await app.state.telemetry_service.stop()
            except Exception as e:
                logger.warning(f"Error stopping telemetry service: {e}")

        if hasattr(app.state, 'background_worker') and app.state.background_worker and app.state.background_worker.is_running():
            try:
                app.state.background_worker.stop(timeout=10.0)
                logger.info("Background embedding worker stopped")
            except Exception as e:
                logger.warning(f"Error stopping background worker: {e}")

        if hasattr(app.state, 'operations_service') and app.state.operations_service:
            try:
                app.state.operations_service.shutdown()
            except Exception as e:
                logger.warning(f"Error shutting down operations service: {e}")

        if hasattr(app.state, 'thread_safe_faiss') and app.state.thread_safe_faiss:
            try:
                app.state.thread_safe_faiss.shutdown()
                logger.info("ThreadSafeFAISSManager shut down")
            except Exception as e:
                logger.warning(f"Error shutting down ThreadSafeFAISSManager: {e}")

        if hasattr(app.state, 'db') and app.state.db:
            app.state.db.close()
            logger.info("Database connection closed")
        logger.info("CHL API server shut down")


app = FastAPI(
    title="CHL API",
    description="Curated Heuristic Loop API for experience management",
    version="0.2.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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


app.include_router(health_router)
app.include_router(categories_router)
app.include_router(entries_router)
app.include_router(search_router)
app.include_router(session_router)
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
    """Return basic service info for smoke checks."""
    accept = request.headers.get("accept", "")
    if "text/html" in accept and "application/json" not in accept:
        return RedirectResponse(url="/settings", status_code=307)
    return {
        "service": "CHL API",
        "version": "0.2.0",
        "status": "running",
    }


configure_logging()

static_dir = Path(__file__).resolve().parents[1] / "common" / "web_utils" / "static"
static_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

favicon_path = static_dir / "favicon.ico"


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse(favicon_path)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
