"""Repository pattern for data access operations."""
import os
import getpass
from datetime import datetime, timezone
import json
import numpy as np
from typing import List, Optional

from sqlalchemy.orm import Session
from .schema import Category, Experience, CategoryManual, Embedding, utc_now


def generate_experience_id(category_code: str) -> str:
    """Generate experience ID: EXP-{CATEGORY_CODE}-{YYYYMMDD}-{HHMMSSuuuuuu}

    Example: EXP-FTH-20250115-104200123456
    """
    now = datetime.now(timezone.utc)
    timestamp = now.strftime("%Y%m%d-%H%M%S") + f"{now.microsecond:06d}"
    return f"EXP-{category_code}-{timestamp}"


def generate_manual_id(category_code: str) -> str:
    """Generate manual ID: MNL-{CATEGORY_CODE}-{YYYYMMDD}-{HHMMSSuuuuuu}

    Example: MNL-FTH-20250115-104200123456
    """
    now = datetime.now(timezone.utc)
    timestamp = now.strftime("%Y%m%d-%H%M%S") + f"{now.microsecond:06d}"
    return f"MNL-{category_code}-{timestamp}"


def get_author() -> Optional[str]:
    """Get author from OS username (robust)."""
    try:
        return getpass.getuser()
    except Exception:
        # Fallbacks
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
        """Create a new category."""
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
        """Get all categories."""
        return self.session.query(Category).order_by(Category.code).all()

    def get_by_code(self, code: str) -> Optional[Category]:
        """Get category by code."""
        return self.session.query(Category).filter(Category.code == code.upper()).first()


class ExperienceRepository:
    """Repository for experience operations."""

    def __init__(self, session: Session):
        self.session = session

    def create(self, experience_data: dict) -> Experience:
        """Create a new experience."""
        category_code = experience_data["category_code"]
        now = utc_now()

        # Normalize context: store NULL for falsy, JSON string for dict/list
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
        """Get experience by ID."""
        return self.session.query(Experience).filter(Experience.id == experience_id).first()

    def get_by_category(
        self, category_code: str, section: Optional[str] = None
    ) -> List[Experience]:
        """Get experiences by category and optional section."""
        query = self.session.query(Experience).filter(Experience.category_code == category_code)

        if section:
            query = query.filter(Experience.section == section)

        return query.order_by(Experience.updated_at.desc()).all()

    def update(self, experience_id: str, updates: dict) -> Experience:
        """Update an existing experience."""
        experience = self.get_by_id(experience_id)
        if not experience:
            raise ValueError(f"Experience not found: {experience_id}")

        # Update allowed fields with normalization
        if "title" in updates:
            experience.title = updates["title"]
        if "playbook" in updates:
            experience.playbook = updates["playbook"]
        if "section" in updates:
            experience.section = updates["section"]
        if "context" in updates:
            ctx = updates["context"]
            if not ctx:
                experience.context = None
            elif isinstance(ctx, (dict, list)):
                experience.context = json.dumps(ctx, ensure_ascii=False)
            else:
                experience.context = str(ctx)

        # Update metadata
        experience.updated_at = utc_now()

        # Mark as pending if updating a global entry
        if experience.source == "global":
            experience.sync_status = 1

        self.session.flush()
        return experience

    def delete(self, experience_id: str) -> None:
        """Delete an experience."""
        experience = self.get_by_id(experience_id)
        if experience:
            self.session.delete(experience)
            self.session.flush()

    def get_pending_for_export(self) -> List[Experience]:
        """Get experiences that need export (sync_status=1 or source='local')."""
        return (
            self.session.query(Experience)
            .filter((Experience.sync_status == 1) | (Experience.source == "local"))
            .order_by(Experience.category_code, Experience.section, Experience.created_at)
            .all()
        )

    def mark_synced(self, experience_ids: List[str]) -> None:
        """Mark experiences as synced."""
        now = utc_now()
        self.session.query(Experience).filter(Experience.id.in_(experience_ids)).update(
            {Experience.sync_status: 0, Experience.synced_at: now}, synchronize_session=False
        )
        self.session.flush()


class CategoryManualRepository:
    """Repository for category manual operations."""

    def __init__(self, session: Session):
        self.session = session

    def create(self, manual_data: dict) -> CategoryManual:
        """Create a new manual."""
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
        """Get manual by ID."""
        return self.session.query(CategoryManual).filter(CategoryManual.id == manual_id).first()

    def get_by_category(self, category_code: str) -> List[CategoryManual]:
        """Get all manuals for a category."""
        return (
            self.session.query(CategoryManual)
            .filter(CategoryManual.category_code == category_code)
            .order_by(CategoryManual.updated_at.desc())
            .all()
        )

    def update(self, manual_id: str, updates: dict) -> CategoryManual:
        """Update an existing manual."""
        manual = self.get_by_id(manual_id)
        if not manual:
            raise ValueError(f"Manual not found: {manual_id}")

        # Update allowed fields
        for field in ["title", "content", "summary"]:
            if field in updates:
                setattr(manual, field, updates[field])

        # Update metadata
        manual.updated_at = utc_now()

        # Mark as pending if updating a global entry
        if manual.source == "global":
            manual.sync_status = 1

        self.session.flush()
        return manual

    def delete(self, manual_id: str) -> None:
        """Delete a manual."""
        manual = self.get_by_id(manual_id)
        if manual:
            self.session.delete(manual)
            self.session.flush()

    def search(self, category_code: Optional[str], query: str) -> List[CategoryManual]:
        """
        Search manuals using simple LIKE-based search.

        Args:
            category_code: Filter by category (optional)
            query: Search query

        Returns:
            List of matching manuals ordered by updated_at DESC
        """
        # Build LIKE pattern
        pattern = f"%{query}%"

        # Build query
        q = self.session.query(CategoryManual).filter(
            (CategoryManual.title.like(pattern))
            | (CategoryManual.content.like(pattern))
            | (CategoryManual.summary.like(pattern))
        )

        # Filter by category if specified
        if category_code:
            q = q.filter(CategoryManual.category_code == category_code)

        return q.order_by(CategoryManual.updated_at.desc()).all()


class EmbeddingRepository:
    """Repository for embedding operations."""

    def __init__(self, session: Session):
        self.session = session

    def create(
        self,
        entity_id: str,
        entity_type: str,
        model_name: str,
        model_version: str,
        embedding: np.ndarray,
    ) -> Embedding:
        """Create or update embedding for an entity

        Args:
            entity_id: Experience or manual ID
            entity_type: 'experience' or 'manual'
            model_name: HuggingFace model identifier
            model_version: Model version/commit hash
            embedding: numpy array of embedding vector

        Returns:
            Created or updated Embedding object
        """
        # Check if embedding already exists
        existing = self.get_by_entity(entity_id, entity_type, model_name)

        if existing:
            # Update existing embedding
            existing.model_version = model_version
            existing.embedding_dimension = len(embedding)
            existing.embedding_data = embedding.tobytes()
            existing.created_at = utc_now()  # Update timestamp
            self.session.flush()
            return existing

        # Create new embedding
        emb = Embedding(
            entity_id=entity_id,
            entity_type=entity_type,
            model_name=model_name,
            model_version=model_version,
            embedding_dimension=len(embedding),
            embedding_data=embedding.tobytes(),
            created_at=utc_now(),
        )
        self.session.add(emb)
        self.session.flush()
        return emb

    def get_by_entity(
        self, entity_id: str, entity_type: str, model_name: str
    ) -> Optional[Embedding]:
        """Get embedding for a specific entity and model"""
        return (
            self.session.query(Embedding)
            .filter(
                Embedding.entity_id == entity_id,
                Embedding.entity_type == entity_type,
                Embedding.model_name == model_name,
            )
            .first()
        )

    def get_all_by_model(self, model_name: str, entity_type: Optional[str] = None) -> List[Embedding]:
        """Get all embeddings for a specific model

        Args:
            model_name: HuggingFace model identifier
            entity_type: Filter by 'experience' or 'manual' (None for both)

        Returns:
            List of Embedding objects
        """
        q = self.session.query(Embedding).filter(Embedding.model_name == model_name)

        if entity_type:
            q = q.filter(Embedding.entity_type == entity_type)

        return q.all()

    def delete_by_entity(self, entity_id: str, entity_type: str, model_name: str) -> None:
        """Delete embedding for an entity with specific model"""
        embedding = self.get_by_entity(entity_id, entity_type, model_name)
        if embedding:
            self.session.delete(embedding)
            self.session.flush()

    def delete_all_for_entity(self, entity_id: str, entity_type: str) -> int:
        """Delete all embeddings for an entity (across all models)

        Args:
            entity_id: Experience or manual ID
            entity_type: 'experience' or 'manual'

        Returns:
            Number of embeddings deleted
        """
        deleted_count = self.session.query(Embedding).filter(
            Embedding.entity_id == entity_id,
            Embedding.entity_type == entity_type
        ).delete(synchronize_session=False)
        self.session.flush()
        return deleted_count

    def to_numpy(self, embedding: Embedding) -> np.ndarray:
        """Convert embedding data back to numpy array

        Args:
            embedding: Embedding object

        Returns:
            numpy array of shape (embedding_dimension,)
        """
        return np.frombuffer(embedding.embedding_data, dtype=np.float32)

    def count_by_status(self, entity_type: Optional[str] = None) -> dict:
        """Count entities by embedding status

        Args:
            entity_type: Filter by 'experience' or 'manual' (None for both)

        Returns:
            Dict with counts: {'pending': N, 'embedded': M, 'failed': K}
        """
        # This queries the entity tables, not embeddings table
        # Need to join with experiences/manuals to get status
        from sqlalchemy import func

        result = {'pending': 0, 'embedded': 0, 'failed': 0}

        if entity_type in (None, 'experience'):
            exp_counts = (
                self.session.query(Experience.embedding_status, func.count())
                .group_by(Experience.embedding_status)
                .all()
            )
            for status, count in exp_counts:
                result[status] = result.get(status, 0) + count

        if entity_type in (None, 'manual'):
            manual_counts = (
                self.session.query(CategoryManual.embedding_status, func.count())
                .group_by(CategoryManual.embedding_status)
                .all()
            )
            for status, count in manual_counts:
                result[status] = result.get(status, 0) + count

        return result
