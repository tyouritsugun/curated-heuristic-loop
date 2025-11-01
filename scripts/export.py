#!/usr/bin/env python3
"""Export the entire local SQLite dataset to Google Sheets."""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from logging.handlers import RotatingFileHandler

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from typing import List

from _config_loader import (
    DEFAULT_CONFIG_PATH,
    ScriptConfigError,
    load_scripts_config,
)
from src.storage.database import Database
from src.storage.schema import Category, Experience, CategoryManual
from src.storage.sheets_client import SheetsClient

# Column definitions (order matters for round-tripping via Sheets)
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


def _row(values: List[str]) -> List[str]:
    return ["" if v is None else str(v) for v in values]


def _configure_logging(log_path: Path, level: int, name: str) -> logging.Logger:
    """Configure console + rotating file handlers explicitly."""
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    root = logging.getLogger()
    root.setLevel(level)

    # Remove existing handlers to avoid duplicates or silent no-ops
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


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export local SQLite entries to Google Sheets.",
    )
    parser.add_argument(
        "--config",
        help=f"Path to YAML config file (default: {DEFAULT_CONFIG_PATH})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview counts without writing to Google Sheets.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug logging.",
    )

    args = parser.parse_args()

    try:
        config_dict, config_path = load_scripts_config(args.config)
    except ScriptConfigError as exc:
        print(f"\nConfiguration error: {exc}", file=sys.stderr)
        sys.exit(1)

    export_cfg = config_dict.get("export")
    if not isinstance(export_cfg, dict):
        print(
            "\nConfiguration error: 'export' section is missing or not a mapping "
            f"in {config_path}",
            file=sys.stderr,
        )
        sys.exit(1)

    # Resolve shared paths (allow section override > global > default)
    root_dir = config_path.parent.parent.resolve()
    default_data_path = root_dir / "data"

    data_path_value = export_cfg.get("data_path", config_dict.get("data_path"))
    data_path = (
        _resolve_path(data_path_value, config_path.parent)
        if data_path_value
        else default_data_path
    )
    database_filename = export_cfg.get("database_filename", "chl.db")
    if Path(database_filename).is_absolute():
        database_path = Path(database_filename)
    else:
        database_path = (data_path / database_filename).resolve()

    credentials_value = export_cfg.get(
        "google_credentials_path", config_dict.get("google_credentials_path")
    )
    if not credentials_value:
        print(
            "\nConfiguration error: google_credentials_path is required under "
            "'export' (or top-level) section.",
            file=sys.stderr,
        )
        sys.exit(1)
    credentials_path = _resolve_path(credentials_value, config_path.parent)

    common_sheet_id = export_cfg.get("sheet_id")

    category_cfg = export_cfg.get("category_sheet", {}) or {}
    if not isinstance(category_cfg, dict):
        print(
            "\nConfiguration error: 'category_sheet' must be a mapping in the export section.",
            file=sys.stderr,
        )
        sys.exit(1)
    category_sheet_id = category_cfg.get("id", common_sheet_id)
    category_worksheet = category_cfg.get("worksheet", DEFAULT_WORKSHEET_CATEGORIES)
    if not category_sheet_id:
        print(
            "\nConfiguration error: provide a sheet ID via export.sheet_id or export.category_sheet.id.",
            file=sys.stderr,
        )
        sys.exit(1)

    experiences_cfg = export_cfg.get("experiences_sheet", {}) or {}
    if not isinstance(experiences_cfg, dict):
        print(
            "\nConfiguration error: 'experiences_sheet' must be a mapping in the export section.",
            file=sys.stderr,
        )
        sys.exit(1)
    experiences_sheet_id = experiences_cfg.get("id", common_sheet_id)
    experiences_worksheet = experiences_cfg.get(
        "worksheet", DEFAULT_WORKSHEET_EXPERIENCES
    )
    if not experiences_sheet_id:
        print(
            "\nConfiguration error: provide a sheet ID via export.sheet_id or export.experiences_sheet.id.",
            file=sys.stderr,
        )
        sys.exit(1)

    manuals_cfg = export_cfg.get("manuals_sheet", {}) or {}
    if not isinstance(manuals_cfg, dict):
        print(
            "\nConfiguration error: 'manuals_sheet' must be a mapping in the export section.",
            file=sys.stderr,
        )
        sys.exit(1)
    manuals_sheet_id = manuals_cfg.get("id", common_sheet_id)
    manuals_worksheet = manuals_cfg.get("worksheet", DEFAULT_WORKSHEET_MANUALS)
    if not manuals_sheet_id:
        print(
            "\nConfiguration error: provide a sheet ID via export.sheet_id or export.manuals_sheet.id.",
            file=sys.stderr,
        )
        sys.exit(1)

    verbose = args.verbose or bool(export_cfg.get("verbose", False))
    log_level = logging.DEBUG if verbose else logging.INFO

    log_dir = data_path / "log"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "chl_server.log"

    dry_run = args.dry_run or bool(export_cfg.get("dry_run", False))
    logger = _configure_logging(log_path, log_level, "export")

    logger.info("Using config file: %s", config_path)
    logger.debug("Database path: %s", database_path)
    logger.debug("Data path: %s", data_path)
    logger.debug(
        "Target sheets (categories=%s/%s, experiences=%s/%s, manuals=%s/%s)",
        category_sheet_id,
        category_worksheet,
        experiences_sheet_id,
        experiences_worksheet,
        manuals_sheet_id,
        manuals_worksheet,
    )

    # Initialise database
    db = Database(str(database_path), echo=False)
    db.init_database()

    with db.session_scope() as session:
        categories = (
            session.query(Category)
            .order_by(Category.code)
            .all()
        )
        experiences = (
            session.query(Experience)
            .order_by(Experience.category_code, Experience.section, Experience.created_at)
            .all()
        )
        manuals = (
            session.query(CategoryManual)
            .order_by(CategoryManual.category_code, CategoryManual.created_at)
            .all()
        )

    logger.info(
        "Fetched %s categories, %s experiences, and %s manuals",
        len(categories),
        len(experiences),
        len(manuals),
    )

    category_rows = [CATEGORY_COLUMNS]
    for cat in categories:
        category_rows.append(
            _row(
                [
                    cat.code,
                    cat.name,
                    cat.description,
                    cat.created_at,
                ]
            )
        )

    experience_rows = [EXPERIENCE_COLUMNS]
    for exp in experiences:
        experience_rows.append(
            _row(
                [
                    exp.id,
                    exp.category_code,
                    exp.section,
                    exp.title,
                    exp.playbook,
                    exp.context,
                    exp.source,
                    exp.sync_status,
                    exp.author,
                    exp.embedding_status,
                    exp.created_at,
                    exp.updated_at,
                    exp.synced_at,
                    exp.exported_at,
                ]
            )
        )

    manual_rows = [MANUAL_COLUMNS]
    for manual in manuals:
        manual_rows.append(
            _row(
                [
                    manual.id,
                    manual.category_code,
                    manual.title,
                    manual.content,
                    manual.summary,
                    manual.source,
                    manual.sync_status,
                    manual.author,
                    manual.embedding_status,
                    manual.created_at,
                    manual.updated_at,
                    manual.synced_at,
                    manual.exported_at,
                ]
            )
        )

    if dry_run:
        print("\nExport preview (dry run)")
        print("========================")
        print(
            f"Categories: {len(categories)} rows -> "
            f"{category_sheet_id}/{category_worksheet}"
        )
        print(
            f"Experiences: {len(experiences)} rows -> "
            f"{experiences_sheet_id}/{experiences_worksheet}"
        )
        print(
            f"Manuals:     {len(manuals)} rows -> "
            f"{manuals_sheet_id}/{manuals_worksheet}"
        )
        print("No data was written to Google Sheets.\n")
        return

    sheets = SheetsClient(str(credentials_path))

    logger.info(
        "Writing categories to sheet %s (worksheet %s)",
        category_sheet_id,
        category_worksheet,
    )
    sheets.create_or_update_worksheet(
        sheet_id=category_sheet_id,
        worksheet_name=category_worksheet,
        data=category_rows,
        read_only_columns=[0],
    )

    logger.info(
        "Writing experiences to sheet %s (worksheet %s)",
        experiences_sheet_id,
        experiences_worksheet,
    )
    sheets.create_or_update_worksheet(
        sheet_id=experiences_sheet_id,
        worksheet_name=experiences_worksheet,
        data=experience_rows,
        read_only_columns=[0],
    )

    logger.info(
        "Writing manuals to sheet %s (worksheet %s)",
        manuals_sheet_id,
        manuals_worksheet,
    )
    sheets.create_or_update_worksheet(
        sheet_id=manuals_sheet_id,
        worksheet_name=manuals_worksheet,
        data=manual_rows,
        read_only_columns=[0],
    )

    logger.info("Export completed successfully.")


if __name__ == "__main__":
    main()
