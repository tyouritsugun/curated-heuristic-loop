"""Repository layer for CHL (shared)."""

import os
import getpass
import json
from datetime import datetime, timezone
from typing import List, Optional

import numpy as np
from sqlalchemy.orm import Session

from .schema import (
    Category,
    Experience,
    CategoryManual,
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


def generate_manual_id(category_code: str) -> str:
    """Generate manual ID: MNL-{CATEGORY_CODE}-{YYYYMMDD}-{HHMMSSuuuuuu}."""
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


class CategoryManualRepository:
    """Repository for category manual operations."""

    def __init__(self, session: Session):
        self.session = session

    def create(self, manual_data: dict) -> CategoryManual:
        category_code = manual_data["category_code"]
        now = utc_now()

        manual = CategoryManual(
            id=generate_manual_id(category_code),
            category_code=category_code,
            title=manual_data["title"],
            content=manual_data["content"],
            summary=manual_data.get("summary"),
            source=manual_data.get("source", "local"),
            sync_status=manual_data.get("sync_status", 1),
            author=manual_data.get("author", get_author()),
            created_at=now,
            updated_at=now,
            synced_at=manual_data.get("synced_at"),
        )
        self.session.add(manual)
        self.session.flush()
        return manual

    def get_by_id(self, manual_id: str) -> Optional[CategoryManual]:
        return self.session.query(CategoryManual).filter(CategoryManual.id == manual_id).first()

    def get_by_category(self, category_code: str) -> List[CategoryManual]:
        return (
            self.session.query(CategoryManual)
            .filter(CategoryManual.category_code == category_code)
            .order_by(CategoryManual.created_at.desc())
            .all()
        )

    def delete_by_category(self, category_code: str) -> int:
        result = (
            self.session.query(CategoryManual)
            .filter(CategoryManual.category_code == category_code)
            .delete(synchronize_session=False)
        )
        return result or 0


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


class FAISSMetadataRepository:
    """Repository for FAISS index metadata."""

    def __init__(self, session: Session):
        self.session = session

    def get_or_create(self, index_path: str, dimension: int, metric: str) -> FAISSMetadata:
        meta = (
            self.session.query(FAISSMetadata)
            .filter(FAISSMetadata.index_path == index_path)
            .first()
        )
        now = utc_now()
        if meta is None:
            meta = FAISSMetadata(
                index_path=index_path,
                dimension=dimension,
                metric=metric,
                trained=False,
                total_vectors=0,
                deleted_vectors=0,
                created_at=now,
                updated_at=now,
            )
            self.session.add(meta)
            self.session.flush()
        return meta

    def update_stats(
        self,
        index_path: str,
        total_vectors: Optional[int] = None,
        deleted_vectors: Optional[int] = None,
        trained: Optional[bool] = None,
    ) -> Optional[FAISSMetadata]:
        meta = (
            self.session.query(FAISSMetadata)
            .filter(FAISSMetadata.index_path == index_path)
            .first()
        )
        if not meta:
            return None

        if total_vectors is not None:
            meta.total_vectors = total_vectors
        if deleted_vectors is not None:
            meta.deleted_vectors = deleted_vectors
        if trained is not None:
            meta.trained = trained
        meta.updated_at = utc_now()
        self.session.flush()
        return meta


class JobHistoryRepository:
    """Repository for job history operations."""

    def __init__(self, session: Session):
        self.session = session

    def create(self, job_type: str, requested_by: Optional[str], payload: Optional[dict]) -> JobHistory:
        job = JobHistory(
            job_type=job_type,
            status="pending",
            requested_by=requested_by,
            created_at=utc_now(),
            payload=payload,
        )
        self.session.add(job)
        self.session.flush()
        return job

    def get(self, job_id: int) -> Optional[JobHistory]:
        return self.session.query(JobHistory).filter(JobHistory.id == job_id).first()

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

    def record_metric(self, worker_name: str, metric_name: str, value: float) -> WorkerMetric:
        metric = WorkerMetric(
            worker_name=worker_name,
            metric_name=metric_name,
            value=value,
            created_at=utc_now(),
        )
        self.session.add(metric)
        self.session.flush()
        return metric

