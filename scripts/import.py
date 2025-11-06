#!/usr/bin/env python3
"""Import Google Sheet data into the local SQLite database (destructive)."""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from logging.handlers import RotatingFileHandler

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from typing import Dict

from _config_loader import (
    DEFAULT_CONFIG_PATH,
    ScriptConfigError,
    load_scripts_config,
)
from _embedding_sync import auto_sync_embeddings
from src.storage.database import Database
import requests
from src.storage.schema import (
    Category,
    CategoryManual,
    Embedding,
    Experience,
    FAISSMetadata,
    utc_now,
)
from src.storage.sheets_client import SheetsClient

EXPERIENCE_COLUMNS = [
    "id",
    "category_code",
    "section",
    "title",
    "playbook",
    "context",
    "source",
    "sync_status",
    "author",
    "embedding_status",
    "created_at",
    "updated_at",
    "synced_at",
    "exported_at",
]

MANUAL_COLUMNS = [
    "id",
    "category_code",
    "title",
    "content",
    "summary",
    "source",
    "sync_status",
    "author",
    "embedding_status",
    "created_at",
    "updated_at",
    "synced_at",
    "exported_at",
]

CATEGORY_COLUMNS = [
    "code",
    "name",
    "description",
    "created_at",
]

DEFAULT_WORKSHEET_EXPERIENCES = "Experiences"
DEFAULT_WORKSHEET_MANUALS = "Manuals"
DEFAULT_WORKSHEET_CATEGORIES = "Categories"


def _resolve_path(value: str | Path, base: Path) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = (base / path).resolve()
    return path


def _str_or_none(value: str | None) -> str | None:
    if value is None:
        return None
    value = str(value).strip()
    return value if value else None


def _int_or_default(value: str | None, default: int = 0) -> int:
    if value is None or str(value).strip() == "":
        return default
    try:
        return int(str(value).strip())
    except ValueError as exc:
        raise ValueError(f"Expected integer value, got '{value}'") from exc


def _require_value(row: Dict[str, str], key: str, entity: str) -> str:
    value = row.get(key)
    if value is None or str(value).strip() == "":
        raise ValueError(f"{entity} row is missing required value '{key}'")
    return str(value).strip()


def _configure_logging(log_path: Path, level: int, name: str) -> logging.Logger:
    """Configure console + rotating file handlers explicitly."""
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    root = logging.getLogger()
    root.setLevel(level)

    for handler in list(root.handlers):
        root.removeHandler(handler)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setLevel(level)
    stream_handler.setFormatter(formatter)
    root.addHandler(stream_handler)

    file_handler = RotatingFileHandler(
        str(log_path), maxBytes=5 * 1024 * 1024, backupCount=3
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    logger = logging.getLogger(name)
    logger.debug("Logging configured. Writing to %s", log_path)
    return logger


def _check_api_server(base_url: str, timeout: int = 2) -> bool:
    """Check if API server is running.

    Args:
        base_url: Base URL for API server (e.g., http://localhost:8000)
        timeout: Request timeout in seconds

    Returns:
        True if server is reachable, False otherwise
    """
    try:
        response = requests.get(f"{base_url}/health", timeout=timeout)
        return response.status_code in (200, 307)  # 307 is redirect to /health/
    except Exception:
        return False


def _pause_workers(base_url: str, logger: logging.Logger, timeout: int = 5) -> bool:
    """Pause background workers via API.

    Args:
        base_url: Base URL for API server
        logger: Logger instance
        timeout: Request timeout in seconds

    Returns:
        True if successful, False otherwise
    """
    try:
        response = requests.post(f"{base_url}/admin/queue/pause", timeout=timeout)
        if response.status_code == 200:
            logger.info("Background workers paused successfully")
            return True
        elif response.status_code == 503:
            logger.info("Worker pool not initialized (ML dependencies not available)")
            return True  # Not an error, just not available
        else:
            logger.warning(f"Failed to pause workers: HTTP {response.status_code}")
            return False
    except Exception as e:
        logger.warning(f"Failed to pause workers: {e}")
        return False


def _drain_queue(base_url: str, logger: logging.Logger, timeout: int = 300) -> bool:
    """Wait for embedding queue to drain via API.

    Args:
        base_url: Base URL for API server
        logger: Logger instance
        timeout: Maximum wait time in seconds

    Returns:
        True if drained successfully, False otherwise
    """
    try:
        logger.info(f"Waiting for embedding queue to drain (max {timeout}s)...")
        response = requests.post(
            f"{base_url}/admin/queue/drain",
            params={"timeout": timeout},
            timeout=timeout + 10  # Add buffer to request timeout
        )
        if response.status_code == 200:
            result = response.json()
            if result.get("status") == "drained":
                logger.info(f"Queue drained in {result.get('elapsed', 0):.1f}s")
                return True
            else:
                remaining = result.get("remaining", "unknown")
                logger.warning(f"Queue drain timeout after {timeout}s ({remaining} jobs remaining)")
                return False
        elif response.status_code == 503:
            logger.info("Worker pool not initialized (nothing to drain)")
            return True
        else:
            logger.warning(f"Failed to drain queue: HTTP {response.status_code}")
            return False
    except Exception as e:
        logger.warning(f"Failed to drain queue: {e}")
        return False


def _resume_workers(base_url: str, logger: logging.Logger, timeout: int = 5) -> bool:
    """Resume background workers via API.

    Args:
        base_url: Base URL for API server
        logger: Logger instance
        timeout: Request timeout in seconds

    Returns:
        True if successful, False otherwise
    """
    try:
        response = requests.post(f"{base_url}/admin/queue/resume", timeout=timeout)
        if response.status_code == 200:
            logger.info("Background workers resumed successfully")
            return True
        elif response.status_code == 503:
            logger.info("Worker pool not initialized (nothing to resume)")
            return True
        else:
            logger.warning(f"Failed to resume workers: HTTP {response.status_code}")
            return False
    except Exception as e:
        logger.warning(f"Failed to resume workers: {e}")
        return False


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Overwrite local SQLite database with rows from Google Sheets.",
    )
    parser.add_argument(
        "--config",
        help=f"Path to YAML config file (default: {DEFAULT_CONFIG_PATH})",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip confirmation prompt (dangerous).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug logging.",
    )
    parser.add_argument(
        "--skip-embeddings",
        action="store_true",
        help="Skip automatic embedding regeneration after import completes.",
    )
    parser.add_argument(
        "--api-url",
        default="http://localhost:8000",
        help="API server URL for worker coordination (default: http://localhost:8000)",
    )
    parser.add_argument(
        "--skip-api-coordination",
        action="store_true",
        help="Skip API worker coordination (pause/drain/resume).",
    )

    args = parser.parse_args()

    try:
        config_dict, config_path = load_scripts_config(args.config)
    except ScriptConfigError as exc:
        print(f"\nConfiguration error: {exc}", file=sys.stderr)
        sys.exit(1)

    import_cfg = config_dict.get("import")
    if not isinstance(import_cfg, dict):
        print(
            "\nConfiguration error: 'import' section is missing or not a mapping "
            f"in {config_path}",
            file=sys.stderr,
        )
        sys.exit(1)

    root_dir = config_path.parent.parent.resolve()
    default_data_path = root_dir / "data"

    data_path_value = import_cfg.get("data_path", config_dict.get("data_path"))
    data_path = (
        _resolve_path(data_path_value, config_path.parent)
        if data_path_value
        else default_data_path
    )

    database_filename = import_cfg.get("database_filename", "chl.db")
    if Path(database_filename).is_absolute():
        database_path = Path(database_filename)
    else:
        database_path = (data_path / database_filename).resolve()

    credentials_value = import_cfg.get(
        "google_credentials_path", config_dict.get("google_credentials_path")
    )
    if not credentials_value:
        print(
            "\nConfiguration error: google_credentials_path is required under "
            "'import' (or top-level) section.",
            file=sys.stderr,
        )
        sys.exit(1)
    credentials_path = _resolve_path(credentials_value, config_path.parent)

    verbose = args.verbose or bool(import_cfg.get("verbose", False))
    log_level = logging.DEBUG if verbose else logging.INFO

    log_dir = data_path / "log"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "chl_server.log"

    logger = _configure_logging(log_path, log_level, "import")

    common_sheet_id = import_cfg.get("sheet_id")

    category_cfg = import_cfg.get("category_sheet", {}) or {}
    if not isinstance(category_cfg, dict):
        print(
            "\nConfiguration error: 'category_sheet' must be a mapping in the import section.",
            file=sys.stderr,
        )
        sys.exit(1)
    category_sheet_id = category_cfg.get("id", common_sheet_id)
    category_worksheet = category_cfg.get("worksheet", DEFAULT_WORKSHEET_CATEGORIES)
    if not category_sheet_id:
        print(
            "\nConfiguration error: provide a sheet ID via import.sheet_id or import.category_sheet.id.",
            file=sys.stderr,
        )
        sys.exit(1)

    experiences_cfg = import_cfg.get("experiences_sheet", {}) or {}
    if not isinstance(experiences_cfg, dict):
        print(
            "\nConfiguration error: 'experiences_sheet' must be a mapping in the import section.",
            file=sys.stderr,
        )
        sys.exit(1)
    experiences_sheet_id = experiences_cfg.get("id", common_sheet_id)
    experiences_worksheet = experiences_cfg.get(
        "worksheet", DEFAULT_WORKSHEET_EXPERIENCES
    )
    if not experiences_sheet_id:
        print(
            "\nConfiguration error: provide a sheet ID via import.sheet_id or import.experiences_sheet.id.",
            file=sys.stderr,
        )
        sys.exit(1)

    manuals_cfg = import_cfg.get("manuals_sheet", {}) or {}
    if not isinstance(manuals_cfg, dict):
        print(
            "\nConfiguration error: 'manuals_sheet' must be a mapping in the import section.",
            file=sys.stderr,
        )
        sys.exit(1)
    manuals_sheet_id = manuals_cfg.get("id", common_sheet_id)
    manuals_worksheet = manuals_cfg.get("worksheet", DEFAULT_WORKSHEET_MANUALS)
    if not manuals_sheet_id:
        print(
            "\nConfiguration error: provide a sheet ID via import.sheet_id or import.manuals_sheet.id.",
            file=sys.stderr,
        )
        sys.exit(1)

    logger.info("Using config file: %s", config_path)
    logger.debug("Database path: %s", database_path)
    logger.debug("Data path: %s", data_path)
    logger.debug(
        "Source sheets (categories=%s/%s, experiences=%s/%s, manuals=%s/%s)",
        category_sheet_id,
        category_worksheet,
        experiences_sheet_id,
        experiences_worksheet,
        manuals_sheet_id,
        manuals_worksheet,
    )

    if not args.yes:
        response = input(
            "This will DELETE all local experiences/manuals and replace them from Google Sheets.\n"
            "Continue? [y/N]: "
        ).strip().lower()
        if response not in {"y", "yes"}:
            print("Import aborted.")
            return

    # Coordinate with API server workers (Phase 4)
    # This ensures pending embeddings are processed before clearing the database
    api_coordinated = False
    if not args.skip_api_coordination:
        logger.info("Checking for running API server at %s", args.api_url)
        if _check_api_server(args.api_url):
            logger.info("API server detected, coordinating with background workers...")
            # Pause workers
            if _pause_workers(args.api_url, logger):
                # Wait for pending jobs to complete
                if _drain_queue(args.api_url, logger, timeout=300):
                    api_coordinated = True
                else:
                    logger.warning("Queue drain incomplete, proceeding anyway")
            else:
                logger.warning("Failed to pause workers, proceeding anyway")
        else:
            logger.info("API server not running, skipping worker coordination")

    sheets = SheetsClient(str(credentials_path))

    categories_rows = sheets.read_worksheet(category_sheet_id, category_worksheet)
    experiences_rows = sheets.read_worksheet(experiences_sheet_id, experiences_worksheet)
    manuals_rows = sheets.read_worksheet(manuals_sheet_id, manuals_worksheet)

    logger.info(
        "Fetched %s category rows, %s experience rows and %s manual rows from Google Sheets",
        len(categories_rows),
        len(experiences_rows),
        len(manuals_rows),
    )

    # Validate required columns
    if not categories_rows:
        raise RuntimeError(
            "Categories worksheet is empty. Ensure the export includes category rows before importing."
        )
    missing = [col for col in CATEGORY_COLUMNS if col not in categories_rows[0]]
    if missing:
        raise RuntimeError(
            f"Categories worksheet is missing required columns: {', '.join(sorted(missing))}"
        )
    if experiences_rows:
        missing = [col for col in EXPERIENCE_COLUMNS if col not in experiences_rows[0]]
        if missing:
            raise RuntimeError(
                f"Experiences worksheet is missing required columns: {', '.join(sorted(missing))}"
            )
    if manuals_rows:
        missing = [col for col in MANUAL_COLUMNS if col not in manuals_rows[0]]
        if missing:
            raise RuntimeError(
                f"Manuals worksheet is missing required columns: {', '.join(sorted(missing))}"
            )

    # Initialise database and overwrite
    db = Database(str(database_path), echo=False)
    db.init_database()

    try:
        with db.session_scope() as session:
            logger.info("Clearing existing categories, experiences, manuals, and embeddings")
            session.query(Embedding).delete()
            session.query(FAISSMetadata).delete()
            session.query(Experience).delete()
            session.query(CategoryManual).delete()
            session.query(Category).delete()
            session.flush()

            now_iso = utc_now()

            for row in categories_rows:
                code = _require_value(row, "code", "Category").upper()
                name = _require_value(row, "name", "Category")
                category = Category(
                    code=code,
                    name=name,
                    description=_str_or_none(row.get("description")),
                    created_at=_str_or_none(row.get("created_at")) or now_iso,
                )
                session.add(category)

            for row in experiences_rows:
                try:
                    exp = Experience(
                        id=_require_value(row, "id", "Experience"),
                        category_code=_require_value(row, "category_code", "Experience").upper(),
                        section=_require_value(row, "section", "Experience"),
                        title=_require_value(row, "title", "Experience"),
                        playbook=_require_value(row, "playbook", "Experience"),
                        context=_str_or_none(row.get("context")),
                        source=_str_or_none(row.get("source")) or "local",
                        sync_status=_int_or_default(row.get("sync_status"), default=1),
                        author=_str_or_none(row.get("author")),
                        embedding_status="pending",
                        created_at=_str_or_none(row.get("created_at")) or now_iso,
                        updated_at=_str_or_none(row.get("updated_at")) or now_iso,
                        synced_at=_str_or_none(row.get("synced_at")),
                        exported_at=_str_or_none(row.get("exported_at")),
                    )
                except ValueError as exc:
                    raise ValueError(
                        f"Invalid experience row (id={row.get('id', '<missing>')}) - {exc}"
                    ) from exc
                session.add(exp)

            for row in manuals_rows:
                try:
                    manual = CategoryManual(
                        id=_require_value(row, "id", "Manual"),
                        category_code=_require_value(row, "category_code", "Manual").upper(),
                        title=_require_value(row, "title", "Manual"),
                        content=_require_value(row, "content", "Manual"),
                        summary=_str_or_none(row.get("summary")),
                        source=_str_or_none(row.get("source")) or "local",
                        sync_status=_int_or_default(row.get("sync_status"), default=1),
                        author=_str_or_none(row.get("author")),
                        embedding_status="pending",
                        created_at=_str_or_none(row.get("created_at")) or now_iso,
                        updated_at=_str_or_none(row.get("updated_at")) or now_iso,
                        synced_at=_str_or_none(row.get("synced_at")),
                        exported_at=_str_or_none(row.get("exported_at")),
                    )
                except ValueError as exc:
                    raise ValueError(
                        f"Invalid manual row (id={row.get('id', '<missing>')}) - {exc}"
                    ) from exc
                session.add(manual)

        logger.info(
            "Import completed. Wrote %s categories, %s experiences and %s manuals.",
            len(categories_rows),
            len(experiences_rows),
            len(manuals_rows),
        )

        if args.skip_embeddings:
            logger.info(
                "Skipping automatic embedding regeneration (--skip-embeddings). "
                "Run `python scripts/sync_embeddings.py --retry-failed` when ready."
            )
        else:
            success, reason = auto_sync_embeddings(db, data_path, database_path, logger)
            if not success:
                detail = f" ({reason})" if reason else ""
                logger.warning(
                    "Automatic embedding sync was skipped or failed%s. "
                    "Run `python scripts/sync_embeddings.py --retry-failed` to finish the workflow.",
                    detail,
                )

    finally:
        # Resume workers if they were paused (Phase 4)
        if api_coordinated:
            logger.info("Resuming background workers...")
            _resume_workers(args.api_url, logger)


if __name__ == "__main__":
    main()
