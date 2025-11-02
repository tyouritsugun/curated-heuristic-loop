"""Handlers for retrieving generator/evaluator guideline manuals."""
from typing import Literal

from src.mcp.utils import create_error_response
from src.storage.repository import CategoryManualRepository, CategoryRepository
from src.storage.database import Database

GUIDELINES_CATEGORY = "GLN"
GUIDE_TITLE_MAP = {
    "generator": "Generator workflow guidelines",
    "evaluator": "Evaluator workflow guidelines",
}


def make_get_guidelines_handler(db: Database):
    """Return an MCP handler that fetches seeded guideline manuals."""

    def get_guidelines(guide_type: Literal["generator", "evaluator"], version: str | None = None) -> dict:
        """Return generator/evaluator workflow guidelines seeded in the CHL database."""
        if db is None:
            return create_error_response(
                "SERVER_ERROR",
                "Server not initialized",
                hint="Call init_server() before requesting guidelines.",
                retryable=True,
            )

        title = GUIDE_TITLE_MAP.get(guide_type)
        if title is None:
            return create_error_response(
                "INVALID_REQUEST",
                f"Unknown guide type '{guide_type}'. Use 'generator' or 'evaluator'.",
                hint="Valid options are 'generator' and 'evaluator'.",
                retryable=True,
            )

        with db.session_scope() as session:
            cat_repo = CategoryRepository(session)
            manual_repo = CategoryManualRepository(session)

            category = cat_repo.get_by_code(GUIDELINES_CATEGORY)
            if not category:
                return create_error_response(
                    "NOT_FOUND",
                    "Guidelines category not found. Run 'uv run python scripts/seed_default_content.py' to seed it.",
                    hint="Seed default content with `uv run python scripts/seed_default_content.py`.",
                    retryable=True,
                )

            manuals = manual_repo.get_by_category(GUIDELINES_CATEGORY)
            manual = next((m for m in manuals if m.title == title), None)
            if not manual:
                return create_error_response(
                    "NOT_FOUND",
                    (
                        "Guideline manual not found. Update generator.md/evaluator.md and run "
                        "'uv run python scripts/seed_default_content.py'."
                    ),
                    hint="Refresh guidelines with `uv run python scripts/seed_default_content.py` after updating the source docs.",
                    retryable=True,
                )

            return {
                "meta": {
                    "code": category.code,
                    "name": category.name,
                },
                "manual": {
                    "id": manual.id,
                    "title": manual.title,
                    "content": manual.content,
                    "summary": manual.summary,
                    "updated_at": manual.updated_at,
                    "author": manual.author,
                },
            }

    get_guidelines.__doc__ = (
        "Return the generator or evaluator workflow manual from the GLN category.\n\n"
        "Example:\n"
        "    get_guidelines(guide_type='generator')"
    )
    get_guidelines.__name__ = "get_guidelines"
    return get_guidelines
