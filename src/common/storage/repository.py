"""Repository layer for CHL (shared)."""

import os
import getpass
import json
from datetime import datetime, timezone
from typing import List, Optional

import numpy as np
from sqlalchemy import func
from sqlalchemy.orm import Session

from .schema import (
    Category,
    Experience,
    CategorySkill,
    Embedding,
    FAISSMetadata,
    JobHistory,
    AuditLog,
    TelemetrySample,
    WorkerMetric,
    utc_now,
)


def generate_experience_id(category_code: str) -> str:
    """Generate experience ID: EXP-{CATEGORY_CODE}-{YYYYMMDD}-{HHMMSSuuuuuu}."""
    now = datetime.now(timezone.utc)
    timestamp = now.strftime("%Y%m%d-%H%M%S") + f"{now.microsecond:06d}"
    return f"EXP-{category_code}-{timestamp}"


def generate_skill_id(category_code: str) -> str:
    """Generate skill ID: MNL-{CATEGORY_CODE}-{YYYYMMDD}-{HHMMSSuuuuuu}.

    Note: Prefix is MNL- (legacy) for backward compatibility.
    """
    now = datetime.now(timezone.utc)
    timestamp = now.strftime("%Y%m%d-%H%M%S") + f"{now.microsecond:06d}"
    return f"MNL-{category_code}-{timestamp}"


def get_author() -> Optional[str]:
    """Get author from OS username (robust)."""
    try:
        return getpass.getuser()
    except Exception:
        for key in ("USER", "USERNAME", "LOGNAME"):
            val = os.environ.get(key)
            if val:
                return val
        return "unknown"


class CategoryRepository:
    """Repository for category operations."""

    def __init__(self, session: Session):
        self.session = session

    def create(self, code: str, name: str, description: Optional[str] = None) -> Category:
        category = Category(
            code=code.upper(),
            name=name,
            description=description,
            created_at=utc_now(),
        )
        self.session.add(category)
        self.session.flush()
        return category

    def get_all(self) -> List[Category]:
        return self.session.query(Category).order_by(Category.code).all()

    def get_by_code(self, code: str) -> Optional[Category]:
        return self.session.query(Category).filter(Category.code == code.upper()).first()


class ExperienceRepository:
    """Repository for experience operations."""

    def __init__(self, session: Session):
        self.session = session

    def create(self, experience_data: dict) -> Experience:
        category_code = experience_data["category_code"]
        now = utc_now()

        ctx = experience_data.get("context")
        if not ctx:
            ctx_str = None
        elif isinstance(ctx, (dict, list)):
            ctx_str = json.dumps(ctx, ensure_ascii=False)
        else:
            ctx_str = str(ctx)

        experience = Experience(
            id=generate_experience_id(category_code),
            category_code=category_code,
            section=experience_data["section"],
            title=experience_data["title"],
            playbook=experience_data["playbook"],
            context=ctx_str,
            source=experience_data.get("source", "local"),
            sync_status=experience_data.get("sync_status", 1),
            author=experience_data.get("author", get_author()),
             embedding_status=experience_data.get("embedding_status", "pending"),
            created_at=now,
            updated_at=now,
            synced_at=experience_data.get("synced_at"),
        )
        self.session.add(experience)
        self.session.flush()
        return experience

    def get_by_id(self, experience_id: str) -> Optional[Experience]:
        return self.session.query(Experience).filter(Experience.id == experience_id).first()

    def get_by_category(
        self,
        category_code: str,
        section: Optional[str] = None,
    ) -> List[Experience]:
        query = self.session.query(Experience).filter(Experience.category_code == category_code)
        if section:
            query = query.filter(Experience.section == section)
        return query.order_by(Experience.created_at.desc()).all()

    def delete_by_category(self, category_code: str) -> int:
        result = (
            self.session.query(Experience)
            .filter(Experience.category_code == category_code)
            .delete(synchronize_session=False)
        )
        return result or 0

    def update(self, experience_id: str, updates: dict) -> Experience:
        experience = self.get_by_id(experience_id)
        if experience is None:
            raise ValueError(f"Experience not found: {experience_id}")

        allowed = {"title", "playbook", "context", "section"}
        invalid = set(updates) - allowed
        if invalid:
            raise ValueError(f"Unsupported fields: {', '.join(sorted(invalid))}")

        if "title" in updates:
            experience.title = str(updates["title"]).strip()
        if "playbook" in updates:
            experience.playbook = str(updates["playbook"])
        if "section" in updates:
            experience.section = str(updates["section"]).strip()
        if "context" in updates:
            ctx = updates["context"]
            if ctx is None:
                experience.context = None
            elif isinstance(ctx, (dict, list)):
                experience.context = json.dumps(ctx, ensure_ascii=False)
            else:
                experience.context = str(ctx)

        # Any update to an experience should trigger re-embedding.
        experience.embedding_status = "pending"
        experience.updated_at = utc_now()
        self.session.flush()
        return experience


class CategorySkillRepository:
    """Repository for category skill operations."""

    def __init__(self, session: Session):
        self.session = session

    def create(self, skill_data: dict) -> CategorySkill:
        category_code = skill_data["category_code"]
        now = utc_now()

        skill = CategorySkill(
            id=generate_skill_id(category_code),
            category_code=category_code,
            title=skill_data["title"],
            content=skill_data["content"],
            summary=skill_data.get("summary"),
            source=skill_data.get("source", "local"),
            sync_status=skill_data.get("sync_status", 1),
            author=skill_data.get("author", get_author()),
            embedding_status=skill_data.get("embedding_status", "pending"),
            created_at=now,
            updated_at=now,
            synced_at=skill_data.get("synced_at"),
        )
        self.session.add(skill)
        self.session.flush()
        return skill

    def get_by_id(self, skill_id: str) -> Optional[CategorySkill]:
        return self.session.query(CategorySkill).filter(CategorySkill.id == skill_id).first()

    def get_by_category(self, category_code: str) -> List[CategorySkill]:
        return (
            self.session.query(CategorySkill)
            .filter(CategorySkill.category_code == category_code)
            .order_by(CategorySkill.created_at.desc())
            .all()
        )

    def delete_by_category(self, category_code: str) -> int:
        result = (
            self.session.query(CategorySkill)
            .filter(CategorySkill.category_code == category_code)
            .delete(synchronize_session=False)
        )
        return result or 0

    def delete(self, skill_id: str) -> int:
        result = (
            self.session.query(CategorySkill)
            .filter(CategorySkill.id == skill_id)
            .delete(synchronize_session=False)
        )
        return result or 0

    def update(self, skill_id: str, updates: dict) -> CategorySkill:
        skill = self.get_by_id(skill_id)
        if skill is None:
            raise ValueError(f"Skill not found: {skill_id}")

        allowed = {"title", "content", "summary"}
        invalid = set(updates) - allowed
        if invalid:
            raise ValueError(f"Unsupported fields: {', '.join(sorted(invalid))}")

        if "title" in updates:
            skill.title = str(updates["title"]).strip()
        if "content" in updates:
            skill.content = str(updates["content"])
        if "summary" in updates:
            summary = updates["summary"]
            skill.summary = None if summary is None else str(summary)

        # Any update to a skill should trigger re-embedding.
        skill.embedding_status = "pending"
        skill.updated_at = utc_now()
        self.session.flush()
        return skill


class EmbeddingRepository:
    """Repository for embedding operations."""

    def __init__(self, session: Session):
        self.session = session

    @staticmethod
    def _encode_vector(vector: np.ndarray) -> str:
        return " ".join(f"{v:.8f}" for v in vector.astype(float).tolist())

    @staticmethod
    def _decode_vector(vector_str: str) -> np.ndarray:
        return np.array([float(x) for x in vector_str.split()], dtype=float)

    def create(
        self,
        entity_id: str,
        entity_type: str,
        category_code: str,
        vector: np.ndarray,
        model_version: str,
    ) -> Embedding:
        embedding = Embedding(
            entity_id=entity_id,
            entity_type=entity_type,
            category_code=category_code,
            vector=self._encode_vector(vector),
            model_version=model_version,
            created_at=utc_now(),
        )
        self.session.add(embedding)
        self.session.flush()
        return embedding

    def get_by_entity(self, entity_id: str, entity_type: str) -> Optional[Embedding]:
        return (
            self.session.query(Embedding)
            .filter(
                Embedding.entity_id == entity_id,
                Embedding.entity_type == entity_type,
            )
            .first()
        )

    def delete_by_entity(self, entity_id: str, entity_type: str) -> int:
        result = (
            self.session.query(Embedding)
            .filter(
                Embedding.entity_id == entity_id,
                Embedding.entity_type == entity_type,
            )
            .delete(synchronize_session=False)
        )
        return result or 0

    def count_by_status(self) -> dict:
        """Aggregate embedding_status counts across experiences and manuals."""
        counts: dict[str, int] = {}

        exp_rows = (
            self.session.query(Experience.embedding_status, func.count(Experience.id))
            .group_by(Experience.embedding_status)
            .all()
        )
        man_rows = (
            self.session.query(CategoryManual.embedding_status, func.count(CategoryManual.id))
            .group_by(CategoryManual.embedding_status)
            .all()
        )

        for status, total in exp_rows + man_rows:
            key = status or "unknown"
            counts[key] = counts.get(key, 0) + (total or 0)

        for bucket in ("pending", "embedded", "failed"):
            counts.setdefault(bucket, 0)
        return counts

    def get_all_by_model(
        self,
        model_version: str,
        entity_type: Optional[str] = None,
    ) -> List[Embedding]:
        """Get all embeddings for a specific model version and optional entity type."""
        query = self.session.query(Embedding).filter(
            Embedding.model_version == model_version
        )
        if entity_type:
            query = query.filter(Embedding.entity_type == entity_type)
        return query.all()

    def to_numpy(self, embedding: Embedding) -> np.ndarray:
        """Convert an Embedding object to numpy array."""
        return self._decode_vector(embedding.vector)




class JobHistoryRepository:
    """Repository for job history operations."""

    def __init__(self, session: Session):
        self.session = session

    def create(self, job_id: str, job_type: str, requested_by: Optional[str], payload: Optional[dict]) -> JobHistory:
        job = JobHistory(
            job_id=job_id,
            job_type=job_type,
            status="pending",
            requested_by=requested_by,
            created_at=utc_now(),
            payload=payload,
        )
        self.session.add(job)
        self.session.flush()
        return job

    def get(self, job_id: str) -> Optional[JobHistory]:
        return self.session.query(JobHistory).filter(JobHistory.job_id == job_id).first()

    def list_recent(self, limit: int = 20) -> List[JobHistory]:
        return (
            self.session.query(JobHistory)
            .order_by(JobHistory.created_at.desc())
            .limit(limit)
            .all()
        )


class AuditLogRepository:
    """Repository for audit log entries."""

    def __init__(self, session: Session):
        self.session = session

    def log(self, event_type: str, actor: Optional[str], context) -> AuditLog:
        if context is not None and not isinstance(context, str):
            try:
                context_str = json.dumps(context, ensure_ascii=False)
            except TypeError:
                context_str = str(context)
        else:
            context_str = context

        log_entry = AuditLog(
            event_type=event_type,
            actor=actor,
            context=context_str,
            created_at=utc_now(),
        )
        self.session.add(log_entry)
        self.session.flush()
        return log_entry

    def list_recent(self, limit: int = 20) -> List[AuditLog]:
        return (
            self.session.query(AuditLog)
            .order_by(AuditLog.created_at.desc())
            .limit(limit)
            .all()
        )


class TelemetryRepository:
    """Repository for telemetry samples."""

    def __init__(self, session: Session):
        self.session = session

    def add_sample(self, sample_type: str, payload: dict) -> TelemetrySample:
        sample = TelemetrySample(
            sample_type=sample_type,
            payload=payload,
            created_at=utc_now(),
        )
        self.session.add(sample)
        self.session.flush()
        return sample


class WorkerMetricRepository:
    """Repository for worker metrics."""

    def __init__(self, session: Session):
        self.session = session

    def record_metric(
        self,
        worker_id: str,
        status: str,
        *,
        queue_depth: Optional[int] = None,
        processed: Optional[int] = None,
        failed: Optional[int] = None,
        payload: Optional[str] = None,
    ) -> WorkerMetric:
        metric = WorkerMetric(
            worker_id=worker_id,
            status=status,
            queue_depth=queue_depth,
            processed=processed,
            failed=failed,
            payload=payload,
            heartbeat_at=utc_now(),
        )
        self.session.add(metric)
        self.session.flush()
        return metric
