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

from typing import Dict, Optional

from _config_loader import (
    DEFAULT_CONFIG_PATH,
    ScriptConfigError,
    load_scripts_config,
)
from src.storage.database import Database
from src.api_client import CHLAPIClient
from src.storage.schema import (
    Category,
    CategoryManual,
    Embedding,
    Experience,
    FAISSMetadata,
    utc_now,
)
from src.storage.sheets_client import SheetsClient
from src.services.settings_service import SettingsService

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


def _credentials_path_from_settings(db: Database, data_path: Path) -> Optional[Path]:
    settings_service = SettingsService(db.get_session, str(data_path))
    with db.session_scope() as session:
        snapshot = settings_service.snapshot(session)
        credentials = snapshot.get("credentials") if snapshot else None
        if credentials and credentials.get("path"):
            candidate = Path(credentials["path"]).expanduser()
            if candidate.exists():
                return candidate.resolve()
    return None


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

    database_filename = import_cfg.get("database_filename") or config_dict.get("database_filename") or "chl.db"
    if Path(database_filename).is_absolute():
        database_path = Path(database_filename)
    else:
        database_path = (data_path / database_filename).resolve()

    db = Database(str(database_path), echo=False)
    db.init_database()

    credentials_value = import_cfg.get(
        "google_credentials_path", config_dict.get("google_credentials_path")
    )
    credentials_source = "config" if credentials_value else "settings"
    if credentials_value:
        credentials_path = _resolve_path(credentials_value, config_path.parent)
    else:
        credentials_path = _credentials_path_from_settings(db, data_path)
        if credentials_path is None:
            print(
                "\nConfiguration error: provide google_credentials_path in scripts_config.yaml "
                "or upload credentials via the Settings UI.",
                file=sys.stderr,
            )
            sys.exit(1)

    verbose = args.verbose or bool(import_cfg.get("verbose", False))
    log_level = logging.DEBUG if verbose else logging.INFO

    log_dir = data_path / "log"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "chl_server.log"

    logger = _configure_logging(log_path, log_level, "import")

    spreadsheet_id = (import_cfg.get("spreadsheet_id") or import_cfg.get("sheet_id") or "").strip()

    try:
        category_sheet_id, category_worksheet = _worksheet_config(
            import_cfg,
            key="categories",
            legacy_key="category_sheet",
            default_name=DEFAULT_WORKSHEET_CATEGORIES,
            fallback_sheet_id=spreadsheet_id,
        )
        experiences_sheet_id, experiences_worksheet = _worksheet_config(
            import_cfg,
            key="experiences",
            legacy_key="experiences_sheet",
            default_name=DEFAULT_WORKSHEET_EXPERIENCES,
            fallback_sheet_id=spreadsheet_id,
        )
        manuals_sheet_id, manuals_worksheet = _worksheet_config(
            import_cfg,
            key="manuals",
            legacy_key="manuals_sheet",
            default_name=DEFAULT_WORKSHEET_MANUALS,
            fallback_sheet_id=spreadsheet_id,
        )
    except ScriptConfigError as exc:
        print(f"\nConfiguration error: {exc}", file=sys.stderr)
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
    api_client = None
    api_coordinated = False
    if not args.skip_api_coordination:
        logger.info("Checking for running API server at %s", args.api_url)
        api_client = CHLAPIClient(args.api_url)
        if api_client.check_health():
            logger.info("API server detected, coordinating with background workers...")
            # Pause workers
            if api_client.pause_workers():
                # Wait for pending jobs to complete
                if api_client.drain_queue(timeout=300):
                    api_coordinated = True
                else:
                    logger.warning("Queue drain incomplete, proceeding anyway")
            else:
                logger.warning("Failed to pause workers, proceeding anyway")
        else:
            logger.info("API server not running, skipping worker coordination")

    sheets = SheetsClient(str(credentials_path))
    logger.info("Using Google credentials from %s (%s)", credentials_path, credentials_source)

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

        logger.info(
            "Rebuild embeddings/FAISS when convenient via the Operations dashboard "
            "(use the FAISS Snapshot card to upload a fresh index) before enabling vector search."
        )

    finally:
        # Resume workers if they were paused (Phase 4 legacy hook)
        if api_coordinated and api_client:
            logger.info("Resuming background workers...")
            api_client.resume_workers()
def _worksheet_config(
    scope: dict,
    *,
    key: str,
    legacy_key: str,
    default_name: str,
    fallback_sheet_id: Optional[str],
) -> tuple[str, str]:
    worksheet_name: Optional[str] = None
    sheet_id: Optional[str] = None

    worksheets_cfg = scope.get("worksheets")
    if isinstance(worksheets_cfg, dict):
        entry = worksheets_cfg.get(key)
        if entry is None and key.endswith("ies"):
            entry = worksheets_cfg.get(key[:-3] + "y")
        if entry is None and key.endswith("s"):
            entry = worksheets_cfg.get(key[:-1])
        if isinstance(entry, str):
            worksheet_name = entry.strip() or default_name
        elif isinstance(entry, dict):
            worksheet_name = (entry.get("worksheet") or entry.get("name") or default_name).strip() or default_name
            override = (entry.get("sheet_id") or entry.get("id") or "").strip()
            if override:
                sheet_id = override
            else:
                inherit = (entry.get("spreadsheet_id") or entry.get("spreadsheet") or "").strip()
                if inherit:
                    sheet_id = inherit

    legacy_cfg = scope.get(legacy_key) or {}
    if worksheet_name is None:
        if isinstance(legacy_cfg, dict):
            worksheet_name = (legacy_cfg.get("worksheet") or default_name).strip() or default_name
        else:
            worksheet_name = default_name

    if sheet_id is None and isinstance(legacy_cfg, dict):
        legacy_id = (legacy_cfg.get("id") or "").strip()
        if legacy_id:
            sheet_id = legacy_id

    if sheet_id is None:
        if fallback_sheet_id and fallback_sheet_id.strip():
            sheet_id = fallback_sheet_id.strip()

    if not sheet_id:
        raise ScriptConfigError(
            f"Missing sheet ID for '{key}'. Provide import.spreadsheet_id or set worksheets.{key}.sheet_id."
        )

    return sheet_id, worksheet_name


if __name__ == "__main__":
    main()
