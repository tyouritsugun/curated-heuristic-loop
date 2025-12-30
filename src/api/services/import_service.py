"""Service for importing data from Google Sheets into the database."""

from __future__ import annotations

import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from src.common.storage.schema import (
    Category,
    CategorySkill,
    Embedding,
    Experience,
    FAISSMetadata,
    utc_now,
)

logger = logging.getLogger(__name__)


class ImportService:
    """Handles importing data from external sources (Google Sheets) into the database."""

    def __init__(self, data_path: Path, faiss_index_path: Optional[Path] = None):
        """Initialize the import service.

        Args:
            data_path: Path to the data directory containing faiss_index/
        """
        self.data_path = data_path
        if faiss_index_path:
            self.faiss_index_dir = Path(faiss_index_path)
        else:
            self.faiss_index_dir = data_path / "faiss_index"

    def import_from_sheets(
        self,
        session: Session,
        categories_rows: List[Dict[str, Any]],
        experiences_rows: List[Dict[str, Any]],
        skills_rows: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Import data from Google Sheets format into the database.

        This is a destructive operation that:
        1. Clears FAISS index files
        2. Deletes all existing data (embeddings, experiences, manuals, categories)
        3. Inserts new data from sheets

        Args:
            session: Database session
            categories_rows: List of category dicts from sheets
            experiences_rows: List of experience dicts from sheets
            skills_rows: List of manual dicts from sheets

        Returns:
            Dict with counts of imported items
        """
        logger.info("Starting import from sheets")

        # Clear FAISS index files before clearing database
        if self.faiss_index_dir.exists():
            logger.info("Clearing FAISS index files from %s", self.faiss_index_dir)
            shutil.rmtree(self.faiss_index_dir)
            self.faiss_index_dir.mkdir(parents=True, exist_ok=True)
            logger.info("FAISS index directory cleared and recreated")

        # Clear existing data
        logger.info("Clearing existing categories, experiences, manuals, and embeddings")
        session.query(Embedding).delete()
        session.query(FAISSMetadata).delete()
        session.query(Experience).delete()
        session.query(CategorySkill).delete()
        session.query(Category).delete()
        session.flush()

        now_iso = utc_now()

        # Import categories
        categories_count = 0
        for row in categories_rows:
            code = self._require_value(row, "code", "Category").upper()
            name = self._require_value(row, "name", "Category")
            category = Category(
                code=code,
                name=name,
                description=self._str_or_none(row.get("description")),
                created_at=self._datetime_or_none(row.get("created_at")) or now_iso,
            )
            session.add(category)
            categories_count += 1

        # Import experiences
        experiences_count = 0
        for row in experiences_rows:
            try:
                exp = Experience(
                    id=self._require_value(row, "id", "Experience"),
                    category_code=self._require_value(row, "category_code", "Experience").upper(),
                    section=self._require_value(row, "section", "Experience"),
                    title=self._require_value(row, "title", "Experience"),
                    playbook=self._require_value(row, "playbook", "Experience"),
                    context=self._str_or_none(row.get("context")),
                    source=self._str_or_none(row.get("source")) or "local",
                    sync_status=self._int_or_default(row.get("sync_status"), default=1),
                    author=self._str_or_none(row.get("author")),
                    embedding_status="pending",
                    created_at=self._datetime_or_none(row.get("created_at")) or now_iso,
                    updated_at=self._datetime_or_none(row.get("updated_at")) or now_iso,
                    synced_at=self._datetime_or_none(row.get("synced_at")),
                    exported_at=self._datetime_or_none(row.get("exported_at")),
                )
            except ValueError as exc:
                raise ValueError(
                    f"Invalid experience row (id={row.get('id', '<missing>')}) - {exc}"
                ) from exc
            session.add(exp)
            experiences_count += 1

        # Import manuals
        manuals_count = 0
        for row in skills_rows:
            try:
                manual = CategorySkill(
                    id=self._require_value(row, "id", "Manual"),
                    category_code=self._require_value(row, "category_code", "Manual").upper(),
                    title=self._require_value(row, "title", "Manual"),
                    content=self._require_value(row, "content", "Manual"),
                    summary=self._str_or_none(row.get("summary")),
                    source=self._str_or_none(row.get("source")) or "local",
                    sync_status=self._int_or_default(row.get("sync_status"), default=1),
                    author=self._str_or_none(row.get("author")),
                    embedding_status="pending",
                    created_at=self._datetime_or_none(row.get("created_at")) or now_iso,
                    updated_at=self._datetime_or_none(row.get("updated_at")) or now_iso,
                    synced_at=self._datetime_or_none(row.get("synced_at")),
                    exported_at=self._datetime_or_none(row.get("exported_at")),
                )
            except ValueError as exc:
                raise ValueError(
                    f"Invalid manual row (id={row.get('id', '<missing>')}) - {exc}"
                ) from exc
            session.add(manual)
            manuals_count += 1

        session.commit()

        logger.info(
            "Import completed: %d categories, %d experiences, %d manuals",
            categories_count,
            experiences_count,
            manuals_count,
        )

        return {
            "categories": categories_count,
            "experiences": experiences_count,
            "manuals": manuals_count,
        }

    @staticmethod
    def _str_or_none(value: str | None) -> str | None:
        """Convert empty strings to None."""
        if value is None:
            return None
        value = str(value).strip()
        return value if value else None

    @staticmethod
    def _datetime_or_none(value: str | datetime | None) -> datetime | None:
        """Convert ISO datetime string to datetime object, or return None."""
        if value is None:
            return None
        if isinstance(value, datetime):
            return value
        value_str = str(value).strip()
        if not value_str:
            return None
        try:
            return datetime.fromisoformat(value_str.replace('Z', '+00:00'))
        except (ValueError, AttributeError):
            logger.warning("Failed to parse datetime value: %s", value_str)
            return None

    @staticmethod
    def _require_value(row: Dict[str, Any], key: str, entity_type: str) -> str:
        """Get a required value from a row, raising if missing."""
        value = row.get(key)
        if not value:
            raise ValueError(f"{entity_type} requires '{key}'")
        return str(value).strip()

    @staticmethod
    def _int_or_default(value: str | None, default: int = 0) -> int:
        """Convert to int or return default."""
        if value is None or str(value).strip() == "":
            return default
        try:
            return int(str(value).strip())
        except ValueError as exc:
            raise ValueError(f"Invalid integer value: {value}") from exc
