"""Embedding generation service for experiences and manuals."""

import logging
import time
from typing import Dict, List, Optional

import numpy as np
from sqlalchemy.exc import OperationalError as SAOperationalError
from sqlalchemy.orm import Session

from src.common.storage.schema import Experience, CategoryManual
from src.common.storage.repository import (
    EmbeddingRepository,
    ExperienceRepository,
    CategoryManualRepository,
)
from .embedding_client import EmbeddingClient, EmbeddingClientError

logger = logging.getLogger(__name__)


class EmbeddingService:
    """Service for generating and managing embeddings."""

    def __init__(
        self,
        session: Session,
        embedding_client: EmbeddingClient,
        model_name: str,
        faiss_index_manager: Optional["FAISSIndexManager"] = None,
        max_tokens: int = 8000,
    ):
        self.session = session
        self.embedding_client = embedding_client
        self.model_name = model_name
        self.faiss_index_manager = faiss_index_manager
        self.max_tokens = max_tokens

        self.emb_repo = EmbeddingRepository(session)
        self.exp_repo = ExperienceRepository(session)
        self.manual_repo = CategoryManualRepository(session)

    def _is_sqlite_lock_error(self, exc: Exception) -> bool:
        msg = str(getattr(exc, "orig", exc)).lower()
        return (
            "database is locked" in msg
            or "database is busy" in msg
            or "database table is locked" in msg
        )

    def _with_lock_retry(self, func, desc: str, retries: int = 8, base_delay: float = 0.1):
        attempt = 0
        while True:
            try:
                return func()
            except SAOperationalError as exc:
                if self._is_sqlite_lock_error(exc) and attempt < retries:
                    try:
                        self.session.rollback()
                    except Exception:
                        pass
                    delay = base_delay * (2 ** attempt)
                    logger.warning(
                        "SQLite locked during %s; retrying in %.2fs (attempt %s/%s)",
                        desc,
                        delay,
                        attempt + 1,
                        retries,
                    )
                    time.sleep(delay)
                    attempt += 1
                    continue
                raise

    def generate_for_experience(self, experience_id: str) -> bool:
        try:
            exp = self.exp_repo.get_by_id(experience_id)
            if not exp:
                logger.error("Experience not found: %s", experience_id)
                return False

            try:
                exp.embedding_status = "processing"
                self._with_lock_retry(
                    lambda: self.session.flush(), desc="flush experience -> processing"
                )
                self._with_lock_retry(
                    lambda: self.session.commit(), desc="commit experience -> processing"
                )
            except Exception:
                try:
                    self.session.rollback()
                except Exception:
                    pass

            content = f"{exp.title}\n\n{exp.playbook}"
            try:
                embedding = self.embedding_client.encode_single(content)
            except EmbeddingClientError as exc:
                logger.error("Failed to generate embedding for %s: %s", experience_id, exc)
                exp.embedding_status = "failed"
                self.session.flush()
                return False

            def _upsert():
                self.emb_repo.create(
                    entity_id=experience_id,
                    entity_type="experience",
                    category_code=exp.category_code,
                    vector=embedding,
                    model_version=self.embedding_client.get_model_version(),
                )

            self._with_lock_retry(_upsert, desc="embedding upsert (experience)")

            exp.embedding_status = "embedded"
            self._with_lock_retry(
                lambda: self.session.flush(), desc="flush experience status"
            )

            try:
                self._with_lock_retry(
                    lambda: self.session.commit(), desc="commit experience embedding"
                )
            except Exception as exc:
                logger.error("Failed to commit embedding for %s: %s", experience_id, exc)
                try:
                    self.session.rollback()
                except Exception:
                    pass
                return False

            if self.faiss_index_manager:
                try:
                    self.faiss_index_manager.add(
                        entity_ids=[experience_id],
                        entity_types=["experience"],
                        embeddings=embedding.reshape(1, -1),
                    )
                except Exception as exc:
                    logger.warning(
                        "Failed to update FAISS index (embedding saved): %s", exc
                    )

            logger.info("Generated embedding for experience: %s", experience_id)
            return True
        except Exception as exc:
            logger.error("Failed to generate embedding for experience %s: %s", experience_id, exc)
            try:
                exp = self.exp_repo.get_by_id(experience_id)
                if exp:
                    exp.embedding_status = "failed"
                    self.session.flush()
            except Exception:
                pass
            return False

    def generate_for_manual(self, manual_id: str) -> bool:
        try:
            manual = self.manual_repo.get_by_id(manual_id)
            if not manual:
                logger.error("Manual not found: %s", manual_id)
                return False

            try:
                manual.embedding_status = "processing"
                self._with_lock_retry(
                    lambda: self.session.flush(), desc="flush manual -> processing"
                )
                self._with_lock_retry(
                    lambda: self.session.commit(), desc="commit manual -> processing"
                )
            except Exception:
                try:
                    self.session.rollback()
                except Exception:
                    pass

            content = manual.content
            try:
                embedding = self.embedding_client.encode_single(content)
            except EmbeddingClientError as exc:
                logger.error("Failed to generate embedding for %s: %s", manual_id, exc)
                manual.embedding_status = "failed"
                self.session.flush()
                return False

            def _upsert():
                self.emb_repo.create(
                    entity_id=manual_id,
                    entity_type="manual",
                    category_code=manual.category_code,
                    vector=embedding,
                    model_version=self.embedding_client.get_model_version(),
                )

            self._with_lock_retry(_upsert, desc="embedding upsert (manual)")

            manual.embedding_status = "embedded"
            self._with_lock_retry(
                lambda: self.session.flush(), desc="flush manual status"
            )

            try:
                self._with_lock_retry(
                    lambda: self.session.commit(), desc="commit manual embedding"
                )
            except Exception as exc:
                logger.error("Failed to commit embedding for %s: %s", manual_id, exc)
                try:
                    self.session.rollback()
                except Exception:
                    pass
                return False

            if self.faiss_index_manager:
                try:
                    self.faiss_index_manager.add(
                        entity_ids=[manual_id],
                        entity_types=["manual"],
                        embeddings=embedding.reshape(1, -1),
                    )
                except Exception as exc:
                    logger.warning(
                        "Failed to update FAISS index (embedding saved): %s", exc
                    )

            logger.info("Generated embedding for manual: %s", manual_id)
            return True
        except Exception as exc:
            logger.error("Failed to generate embedding for manual %s: %s", manual_id, exc)
            try:
                manual = self.manual_repo.get_by_id(manual_id)
                if manual:
                    manual.embedding_status = "failed"
                    self.session.flush()
            except Exception:
                pass
            return False

    def get_pending_experiences(self) -> List[Experience]:
        return (
            self.session.query(Experience)
            .filter(Experience.embedding_status == "pending")
            .all()
        )

    def get_pending_manuals(self) -> List[CategoryManual]:
        return (
            self.session.query(CategoryManual)
            .filter(CategoryManual.embedding_status == "pending")
            .all()
        )

    def get_failed_experiences(self) -> List[Experience]:
        return (
            self.session.query(Experience)
            .filter(Experience.embedding_status == "failed")
            .all()
        )

    def get_failed_manuals(self) -> List[CategoryManual]:
        return (
            self.session.query(CategoryManual)
            .filter(CategoryManual.embedding_status == "failed")
            .all()
        )

    def process_pending(self, max_count: Optional[int] = None) -> Dict[str, int]:
        stats = {"processed": 0, "succeeded": 0, "failed": 0}

        pending_exp = self.get_pending_experiences()
        pending_man = self.get_pending_manuals()

        all_pending = [("experience", exp.id) for exp in pending_exp] + [
            ("manual", man.id) for man in pending_man
        ]

        if max_count:
            all_pending = all_pending[:max_count]

        for entity_type, entity_id in all_pending:
            if entity_type == "experience":
                success = self.generate_for_experience(entity_id)
            else:
                success = self.generate_for_manual(entity_id)

            stats["processed"] += 1
            if success:
                stats["succeeded"] += 1
            else:
                stats["failed"] += 1

        logger.info(
            "Processed %s pending embeddings: %s succeeded, %s failed",
            stats["processed"],
            stats["succeeded"],
            stats["failed"],
        )
        return stats

    def retry_failed(self, max_count: Optional[int] = None) -> Dict[str, int]:
        stats = {"retried": 0, "succeeded": 0, "failed": 0}

        failed_exp = self.get_failed_experiences()
        failed_man = self.get_failed_manuals()

        all_failed = [("experience", exp.id) for exp in failed_exp] + [
            ("manual", man.id) for man in failed_man
        ]

        if max_count:
            all_failed = all_failed[:max_count]

        for entity_type, entity_id in all_failed:
            if entity_type == "experience":
                success = self.generate_for_experience(entity_id)
            else:
                success = self.generate_for_manual(entity_id)

            stats["retried"] += 1
            if success:
                stats["succeeded"] += 1
            else:
                stats["failed"] += 1

        logger.info(
            "Retried %s failed embeddings: %s succeeded, %s failed",
            stats["retried"],
            stats["succeeded"],
            stats["failed"],
        )
        return stats

