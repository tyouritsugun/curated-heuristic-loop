"""SQLAlchemy ORM models for CHL database schema."""
from datetime import datetime, timezone
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    ForeignKey,
    Integer,
    String,
    Text,
    LargeBinary,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class Category(Base):
    """Category metadata and definitions."""

    __tablename__ = "categories"

    code = Column(String(3), primary_key=True)
    name = Column(String, unique=True, nullable=False)
    description = Column(Text, nullable=True)
    created_at = Column(Text, nullable=False)

    # Relationships
    experiences = relationship("Experience", back_populates="category", cascade="all, delete-orphan")
    manuals = relationship("CategoryManual", back_populates="category", cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint("length(code) = 3", name="ck_category_code_length"),
        CheckConstraint("code = upper(code)", name="ck_category_code_uppercase"),
    )

    def __repr__(self):
        return f"<Category(code='{self.code}', name='{self.name}')>"


class Experience(Base):
    """Atomic patterns and heuristics."""

    __tablename__ = "experiences"

    id = Column(String, primary_key=True)
    category_code = Column(String(3), ForeignKey("categories.code", ondelete="CASCADE"), nullable=False)
    section = Column(String, nullable=False)
    title = Column(String(120), nullable=False)
    playbook = Column(String(2000), nullable=False)
    context = Column(Text, nullable=True)  # JSON string

    # Provenance
    source = Column(String, nullable=False, default="local")
    sync_status = Column(Integer, nullable=False, default=1)
    author = Column(String, nullable=True)

    # Search/Embedding metadata
    embedding_status = Column(String, nullable=False, default="pending")

    # Timestamps
    created_at = Column(Text, nullable=False)
    updated_at = Column(Text, nullable=False)
    synced_at = Column(Text, nullable=True)
    exported_at = Column(Text, nullable=True)  # Last export timestamp

    # Relationships
    category = relationship("Category", back_populates="experiences")

    __table_args__ = (
        CheckConstraint("section IN ('useful', 'harmful', 'contextual')", name="ck_experience_section"),
        CheckConstraint("source IN ('local', 'global')", name="ck_experience_source"),
        CheckConstraint("sync_status IN (0, 1, 2)", name="ck_experience_sync_status"),
        CheckConstraint("embedding_status IN ('pending', 'embedded', 'failed')", name="ck_experience_embedding_status"),
        CheckConstraint("length(title) >= 1 AND length(title) <= 120", name="ck_experience_title_length"),
        CheckConstraint("length(playbook) >= 1 AND length(playbook) <= 2000", name="ck_experience_playbook_length"),
    )

    def __repr__(self):
        return f"<Experience(id='{self.id}', category='{self.category_code}', section='{self.section}')>"


class CategoryManual(Base):
    """Long-form context and domain knowledge."""

    __tablename__ = "category_manuals"

    id = Column(String, primary_key=True)
    category_code = Column(String(3), ForeignKey("categories.code", ondelete="CASCADE"), nullable=False)
    title = Column(String(120), nullable=False)
    content = Column(Text, nullable=False)
    summary = Column(Text, nullable=True)

    # Provenance
    source = Column(String, nullable=False, default="local")
    sync_status = Column(Integer, nullable=False, default=1)
    author = Column(String, nullable=True)

    # Search/Embedding metadata
    embedding_status = Column(String, nullable=False, default="pending")

    # Timestamps
    created_at = Column(Text, nullable=False)
    updated_at = Column(Text, nullable=False)
    synced_at = Column(Text, nullable=True)
    exported_at = Column(Text, nullable=True)  # Last export timestamp

    # Relationships
    category = relationship("Category", back_populates="manuals")

    __table_args__ = (
        CheckConstraint("source IN ('local', 'global')", name="ck_manual_source"),
        CheckConstraint("sync_status IN (0, 1, 2)", name="ck_manual_sync_status"),
        CheckConstraint("embedding_status IN ('pending', 'embedded', 'failed')", name="ck_manual_embedding_status"),
        CheckConstraint("length(title) >= 1 AND length(title) <= 120", name="ck_manual_title_length"),
        CheckConstraint("length(content) >= 1", name="ck_manual_content_length"),
    )

    def __repr__(self):
        return f"<CategoryManual(id='{self.id}', category='{self.category_code}', title='{self.title[:30]}...')>"


class Embedding(Base):
    """Vector embeddings for semantic search."""

    __tablename__ = "embeddings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    entity_id = Column(String, nullable=False)
    entity_type = Column(String, nullable=False)
    model_name = Column(String, nullable=False)
    model_version = Column(String, nullable=False)
    embedding_dimension = Column(Integer, nullable=False)
    embedding_data = Column(LargeBinary, nullable=False)  # numpy array as bytes
    created_at = Column(Text, nullable=False)

    __table_args__ = (
        CheckConstraint("entity_type IN ('experience', 'manual')", name="ck_embedding_entity_type"),
        # Unique constraint: one embedding per entity+model combination
        # This allows re-embedding with different models
        # Column-level unique constraints are defined via Index
        UniqueConstraint("entity_id", "entity_type", "model_name", name="uq_embedding_entity_model"),
    )

    def __repr__(self):
        return f"<Embedding(entity_id='{self.entity_id}', entity_type='{self.entity_type}', model='{self.model_name}')>"


class FAISSMetadata(Base):
    """FAISS index metadata for ID mapping and tombstone tracking."""

    __tablename__ = "faiss_metadata"

    id = Column(Integer, primary_key=True, autoincrement=True)
    entity_id = Column(String, nullable=False)
    entity_type = Column(String, nullable=False)
    faiss_internal_id = Column(Integer, nullable=False, unique=True)
    created_at = Column(Text, nullable=False)
    deleted = Column(Boolean, nullable=False, default=False)  # Tombstone marker

    __table_args__ = (
        CheckConstraint("entity_type IN ('experience', 'manual')", name="ck_faiss_entity_type"),
    )

    def __repr__(self):
        return f"<FAISSMetadata(entity_id='{self.entity_id}', faiss_id={self.faiss_internal_id}, deleted={self.deleted})>"


def utc_now() -> str:
    """Return current UTC timestamp in ISO 8601 format."""
    return datetime.now(timezone.utc).isoformat()
