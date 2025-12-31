"""SQLAlchemy schema definitions for CHL (shared)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    Column,
    Integer,
    String,
    DateTime,
    Text,
    ForeignKey,
    JSON,
    Boolean,
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


def utc_now() -> datetime:
    """Return current UTC time with timezone info."""
    return datetime.now(timezone.utc)


class Category(Base):
    __tablename__ = "categories"

    id = Column(Integer, primary_key=True)
    code = Column(String(16), unique=True, nullable=False, index=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)


class Experience(Base):
    __tablename__ = "experiences"

    id = Column(String(64), primary_key=True)
    category_code = Column(String(16), ForeignKey("categories.code"), nullable=False, index=True)
    section = Column(String(32), nullable=False)  # useful, harmful, contextual
    title = Column(String(255), nullable=False)
    playbook = Column(Text, nullable=False)
    context = Column(Text, nullable=True)
    source = Column(String(32), nullable=False, default="local")
    sync_status = Column(Integer, nullable=False, default=1)
    author = Column(String(255), nullable=True)
    embedding_status = Column(String(32), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    synced_at = Column(DateTime(timezone=True), nullable=True)
    exported_at = Column(DateTime(timezone=True), nullable=True)

    category = relationship("Category", backref="experiences")


class CategorySkill(Base):
    __tablename__ = "category_skills"

    id = Column(String(64), primary_key=True)
    category_code = Column(String(16), ForeignKey("categories.code"), nullable=False, index=True)
    title = Column(String(255), nullable=False)
    content = Column(Text, nullable=False)
    summary = Column(Text, nullable=True)
    source = Column(String(32), nullable=False, default="local")
    sync_status = Column(Integer, nullable=False, default=1)
    author = Column(String(255), nullable=True)
    embedding_status = Column(String(32), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    synced_at = Column(DateTime(timezone=True), nullable=True)
    exported_at = Column(DateTime(timezone=True), nullable=True)

    category = relationship("Category", backref="skills")


class Embedding(Base):
    __tablename__ = "embeddings"

    id = Column(Integer, primary_key=True)
    entity_id = Column(String(64), nullable=False, index=True)
    entity_type = Column(String(32), nullable=False)  # experience or skill
    category_code = Column(String(16), nullable=False)
    vector = Column(String, nullable=False)  # stored as space-separated floats
    model_version = Column(String(128), nullable=False)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)


class FAISSMetadata(Base):
    __tablename__ = "faiss_metadata"

    id = Column(Integer, primary_key=True)
    entity_id = Column(String(64), nullable=False, index=True)
    entity_type = Column(String(32), nullable=False)
    internal_id = Column(Integer, nullable=False, index=True)
    deleted = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)


class CurationDecision(Base):
    __tablename__ = "curation_decisions"

    id = Column(Integer, primary_key=True)
    entry_id = Column(String(64), nullable=False, index=True)
    action = Column(String(32), nullable=False)
    target_id = Column(String(64), nullable=True)
    notes = Column(Text, nullable=True)
    user = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)


class ExperienceSplitProvenance(Base):
    __tablename__ = "experience_split_provenance"

    id = Column(Integer, primary_key=True)
    source_experience_id = Column(String(64), nullable=False, index=True)
    split_experience_id = Column(String(64), nullable=True, index=True)
    split_group_id = Column(String(64), nullable=False, index=True)
    decision = Column(String(16), nullable=False)  # split or atomic
    model = Column(String(255), nullable=True)
    prompt_path = Column(String(255), nullable=True)
    raw_response = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)


class JobHistory(Base):
    __tablename__ = "job_history"

    id = Column(Integer, primary_key=True)
    job_id = Column(String(64), unique=True, nullable=False, index=True)
    job_type = Column(String(64), nullable=False, index=True)
    status = Column(String(32), nullable=False)
    requested_by = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    cancelled_at = Column(DateTime(timezone=True), nullable=True)
    payload = Column(JSON, nullable=True)
    result = Column(JSON, nullable=True)
    error_detail = Column(Text, nullable=True)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id = Column(Integer, primary_key=True)
    event_type = Column(String(64), nullable=False)
    actor = Column(String(255), nullable=True)
    context = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)


class Setting(Base):
    __tablename__ = "settings"

    id = Column(Integer, primary_key=True)
    key = Column(String(64), unique=True, nullable=False)
    value_json = Column("value", Text, nullable=True)
    checksum = Column(String(128), nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)


class TelemetrySample(Base):
    __tablename__ = "telemetry_samples"

    id = Column(Integer, primary_key=True)
    metric = Column(String(64), nullable=False)
    value_json = Column(JSON, nullable=False)
    recorded_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)


class WorkerMetric(Base):
    __tablename__ = "worker_metrics"

    id = Column(Integer, primary_key=True)
    worker_id = Column(String(128), nullable=False, index=True)
    status = Column(String(32), nullable=False)
    heartbeat_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    queue_depth = Column(Integer, nullable=True)
    processed = Column(Integer, nullable=True)
    failed = Column(Integer, nullable=True)
    payload = Column(Text, nullable=True)


class OperationLock(Base):
    __tablename__ = "operation_locks"

    id = Column(Integer, primary_key=True)
    name = Column(String(64), unique=True, nullable=False)
    owner_id = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    expires_at = Column(String(64), nullable=True)
