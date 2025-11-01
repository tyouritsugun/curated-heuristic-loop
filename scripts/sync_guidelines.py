"""Seed or update CHL guideline manuals from source markdown files."""
import argparse
from pathlib import Path
from typing import Optional

from src.config import get_config
from src.storage.database import Database
from src.storage.repository import CategoryRepository, CategoryManualRepository

GUIDELINES_CATEGORY_CODE = "GLN"
GENERATOR_GUIDE_TITLE = "Generator workflow guidelines"
EVALUATOR_GUIDE_TITLE = "Evaluator workflow guidelines"
GENERATOR_FILE = Path("generator.md")
EVALUATOR_FILE = Path("evaluator.md")


def _read_markdown(path: Path) -> Optional[str]:
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8").strip()


def _summarize(content: str) -> Optional[str]:
    for line in content.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:120]
    return None


def sync_guidelines(generator_path: Path = GENERATOR_FILE, evaluator_path: Path = EVALUATOR_FILE) -> None:
    config = get_config()
    db = Database(config.database_path, echo=False)
    db.init_database()

    generator_md = _read_markdown(generator_path)
    evaluator_md = _read_markdown(evaluator_path)

    if generator_md is None and evaluator_md is None:
        print(f"No markdown files found at {generator_path} or {evaluator_path}. Nothing to sync.")
        return

    with db.session_scope() as session:
        cat_repo = CategoryRepository(session)
        manual_repo = CategoryManualRepository(session)

        category = cat_repo.get_by_code(GUIDELINES_CATEGORY_CODE)
        if category is None:
            category = cat_repo.create(
                code=GUIDELINES_CATEGORY_CODE,
                name="chl_guidelines",
                description="Seeded generator/evaluator guidance manuals",
            )
            print(f"Created guidelines category: {GUIDELINES_CATEGORY_CODE}")

        existing_manuals = manual_repo.get_by_category(GUIDELINES_CATEGORY_CODE)
        existing_by_title = {manual.title: manual for manual in existing_manuals}
        retained_ids: set[str] = set()

        def upsert_manual(title: str, content: str) -> None:
            summary = _summarize(content)
            existing = existing_by_title.get(title)
            if existing:
                updated = manual_repo.update(
                    existing.id,
                    {
                        "content": content,
                        "summary": summary,
                    },
                )
                updated.sync_status = 1
                session.flush()
                retained_ids.add(updated.id)
                print(f"Updated manual: {title}")
            else:
                new_manual = manual_repo.create(
                    {
                        "category_code": GUIDELINES_CATEGORY_CODE,
                        "title": title,
                        "content": content,
                        "summary": summary,
                    }
                )
                retained_ids.add(new_manual.id)
                print(f"Created manual: {title}")

        if generator_md is not None:
            upsert_manual(GENERATOR_GUIDE_TITLE, generator_md)
        elif GENERATOR_GUIDE_TITLE in existing_by_title:
            manual_repo.delete(existing_by_title[GENERATOR_GUIDE_TITLE].id)
            print(f"Deleted manual: {GENERATOR_GUIDE_TITLE} (generator.md missing)")

        if evaluator_md is not None:
            upsert_manual(EVALUATOR_GUIDE_TITLE, evaluator_md)
        elif EVALUATOR_GUIDE_TITLE in existing_by_title:
            manual_repo.delete(existing_by_title[EVALUATOR_GUIDE_TITLE].id)
            print(f"Deleted manual: {EVALUATOR_GUIDE_TITLE} (evaluator.md missing)")

        # Remove any stale manuals that no longer correspond to the expected titles
        if retained_ids:
            for manual in existing_manuals:
                if manual.id not in retained_ids and manual.title not in (GENERATOR_GUIDE_TITLE, EVALUATOR_GUIDE_TITLE):
                    manual_repo.delete(manual.id)
                    print(f"Deleted manual: {manual.title} (stale)")


if __name__ == "__main__":
    # Standalone invocation is no longer supported. Use the unified seeding command:
    #   uv run python scripts/seed_default_content.py
    import sys
    print(
        "This script is no longer a CLI. Run 'uv run python scripts/seed_default_content.py' instead.",
        file=sys.stderr,
    )
    sys.exit(2)
