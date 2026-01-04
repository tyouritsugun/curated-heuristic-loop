"""Embedding generation service for experiences and skills."""

import logging
import time
from typing import Dict, List, Optional

import numpy as np
from sqlalchemy.exc import OperationalError as SAOperationalError
from sqlalchemy.orm import Session

from src.common.storage.schema import Experience, CategorySkill
from src.common.storage.repository import (
    EmbeddingRepository,
    ExperienceRepository,
    CategorySkillRepository,
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
        skills_enabled: bool = True,
    ):
        self.session = session
        self.embedding_client = embedding_client
        self.model_name = model_name
        self.faiss_index_manager = faiss_index_manager
        self.max_tokens = max_tokens
        self.skills_enabled = skills_enabled

        self.emb_repo = EmbeddingRepository(session)
        self.exp_repo = ExperienceRepository(session)
        self.skill_repo = CategorySkillRepository(session)

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

    def generate_for_skill(self, skill_id: str) -> bool:
        if not self.skills_enabled:
            logger.info("Skipping skill embedding; skills are disabled.")
            return False
        try:
            skill = self.skill_repo.get_by_id(skill_id)
            if not skill:
                logger.error("Skill not found: %s", skill_id)
                return False

            try:
                skill.embedding_status = "processing"
                self._with_lock_retry(
                    lambda: self.session.flush(), desc="flush skill -> processing"
                )
                self._with_lock_retry(
                    lambda: self.session.commit(), desc="commit skill -> processing"
                )
            except Exception:
                try:
                    self.session.rollback()
                except Exception:
                    pass

            content = f"{skill.name}\n\n{skill.description}\n\n{skill.content}"
            try:
                embedding = self.embedding_client.encode_single(content)
            except EmbeddingClientError as exc:
                logger.error("Failed to generate embedding for %s: %s", skill_id, exc)
                skill.embedding_status = "failed"
                self.session.flush()
                return False

            def _upsert():
                self.emb_repo.create(
                    entity_id=skill_id,
                    entity_type="skill",
                    category_code=skill.category_code,
                    vector=embedding,
                    model_version=self.embedding_client.get_model_version(),
                )

            self._with_lock_retry(_upsert, desc="embedding upsert (skill)")

            skill.embedding_status = "embedded"
            self._with_lock_retry(
                lambda: self.session.flush(), desc="flush skill status"
            )

            try:
                self._with_lock_retry(
                    lambda: self.session.commit(), desc="commit skill embedding"
                )
            except Exception as exc:
                logger.error("Failed to commit embedding for %s: %s", skill_id, exc)
                try:
                    self.session.rollback()
                except Exception:
                    pass
                return False

            if self.faiss_index_manager:
                try:
                    self.faiss_index_manager.add(
                        entity_ids=[skill_id],
                        entity_types=["skill"],
                        embeddings=embedding.reshape(1, -1),
                    )
                except Exception as exc:
                    logger.warning(
                        "Failed to update FAISS index (embedding saved): %s", exc
                    )

            logger.info("Generated embedding for skill: %s", skill_id)
            return True
        except Exception as exc:
            logger.error("Failed to generate embedding for skill %s: %s", skill_id, exc)
            try:
                skill = self.skill_repo.get_by_id(skill_id)
                if skill:
                    skill.embedding_status = "failed"
                    self.session.flush()
            except Exception:
                pass
            return False

    def get_pending_experiences(self) -> List[Experience]:
        return (
            self.session.query(Experience)
            .filter(
                (Experience.embedding_status == "pending")
                | (Experience.embedding_status.is_(None))
            )
            .all()
        )

    def get_pending_skills(self) -> List[CategorySkill]:
        if not self.skills_enabled:
            return []
        return (
            self.session.query(CategorySkill)
            .filter(
                (CategorySkill.embedding_status == "pending")
                | (CategorySkill.embedding_status.is_(None))
            )
            .all()
        )

    def get_failed_experiences(self) -> List[Experience]:
        return (
            self.session.query(Experience)
            .filter(Experience.embedding_status == "failed")
            .all()
        )

    def get_failed_skills(self) -> List[CategorySkill]:
        if not self.skills_enabled:
            return []
        return (
            self.session.query(CategorySkill)
            .filter(CategorySkill.embedding_status == "failed")
            .all()
        )

    def process_pending(self, max_count: Optional[int] = None) -> Dict[str, int]:
        stats = {"processed": 0, "succeeded": 0, "failed": 0}

        pending_exp = self.get_pending_experiences()
        pending_skills = self.get_pending_skills()

        all_pending = [("experience", exp.id) for exp in pending_exp] + [
            ("skill", skill.id) for skill in pending_skills
        ]

        if max_count:
            all_pending = all_pending[:max_count]

        for entity_type, entity_id in all_pending:
            if entity_type == "experience":
                success = self.generate_for_experience(entity_id)
            else:
                success = self.generate_for_skill(entity_id)

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
        failed_skills = self.get_failed_skills()

        all_failed = [("experience", exp.id) for exp in failed_exp] + [
            ("skill", skill.id) for skill in failed_skills
        ]

        if max_count:
            all_failed = all_failed[:max_count]

        for entity_type, entity_id in all_failed:
            if entity_type == "experience":
                success = self.generate_for_experience(entity_id)
            else:
                success = self.generate_for_skill(entity_id)

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
