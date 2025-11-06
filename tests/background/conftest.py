"""Pytest fixtures for background worker tests."""

import pytest
import tempfile
from pathlib import Path
from unittest.mock import Mock

from src.storage.schema import Experience, CategoryManual, Category
from src.storage.database import Database


@pytest.fixture
def temp_db():
    """Create a temporary in-memory database for testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    db = Database(db_path)
    db.init_database()
    db.create_tables()  # Create the schema

    yield db

    db.close()
    Path(db_path).unlink(missing_ok=True)


@pytest.fixture
def mock_embedding_service():
    """Create a mock embedding service for testing."""
    service = Mock()

    # Mock successful embedding
    service.upsert_for_experience = Mock(return_value=True)
    service.upsert_for_manual = Mock(return_value=True)

    return service


@pytest.fixture
def sample_category(temp_db):
    """Create a sample category for testing."""
    with temp_db.session_scope() as session:
        category = Category(
            code="TST",
            name="Test Category",
            description="Category for testing",
            created_at="2025-01-01T00:00:00Z"
        )
        session.add(category)
        session.commit()
        return category.code


@pytest.fixture
def sample_experiences(temp_db, sample_category):
    """Create sample experiences with pending embedding status."""
    with temp_db.session_scope() as session:
        experiences = [
            Experience(
                id=f"EXP-TST-{i:03d}",
                category_code=sample_category,
                section="useful",
                title=f"Test Experience {i}",
                playbook=f"Test playbook content {i}",
                context=None,
                source="local",
                sync_status=1,
                embedding_status="pending",
                created_at="2025-01-01T00:00:00Z",
                updated_at="2025-01-01T00:00:00Z"
            )
            for i in range(5)
        ]
        for exp in experiences:
            session.add(exp)
        session.commit()
        return [exp.id for exp in experiences]


@pytest.fixture
def sample_manuals(temp_db, sample_category):
    """Create sample manuals with pending embedding status."""
    with temp_db.session_scope() as session:
        manuals = [
            CategoryManual(
                id=f"MNL-TST-{i:03d}",
                category_code=sample_category,
                title=f"Test Manual {i}",
                content=f"Test manual content {i}",
                summary=f"Summary {i}",
                source="local",
                sync_status=1,
                embedding_status="pending",
                created_at="2025-01-01T00:00:00Z",
                updated_at="2025-01-01T00:00:00Z"
            )
            for i in range(5)
        ]
        for man in manuals:
            session.add(man)
        session.commit()
        return [man.id for man in manuals]
