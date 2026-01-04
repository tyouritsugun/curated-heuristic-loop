#!/usr/bin/env python
"""CPU-only first-time setup for CHL MCP Server

This script initializes the database and prepares the environment for running
the MCP server in CPU mode (no vector search, no ML dependencies).

Usage:
    python scripts/setup/setup-cpu.py

This setup is appropriate for CPU-only machines without GPU acceleration.
It skips all ML model downloads and FAISS initialization.

Prerequisites:
    Run scripts/setup/check_api_env.py and select CPU mode first.
    This creates data/runtime_config.json with backend="cpu".

Environment Variables:
    CHL_EXPERIENCE_ROOT: Path to data directory (default: <project_root>/data)
    CHL_DATABASE_PATH: Path to SQLite database (default: <experience_root>/chl.db)

What this script does:
1. Check/create data directory structure (no FAISS directory)
2. Initialize SQLite database (create tables)
3. Seed starter categories and sample content
4. Validate setup completeness
5. Print next steps

Example:
    python scripts/setup/setup-cpu.py
"""
import os
import sys
import json
import logging
import os
import shutil
import sqlite3
from pathlib import Path

# Ensure repo root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[2]
root_str = str(PROJECT_ROOT)
if root_str not in sys.path:
    sys.path.insert(0, root_str)

# Ensure project root is importable and config is available
from src.common.config.config import ensure_project_root_on_sys_path, get_config  # noqa: E402
from src.common.config.categories import get_categories  # noqa: E402

ensure_project_root_on_sys_path()

from src.common.storage.database import Database
from src.common.storage.repository import (
    CategoryRepository,
    ExperienceRepository,
    CategorySkillRepository,
)
from src.common.storage.schema import Experience, CategorySkill

# Configure logging
log_level = os.getenv("CHL_LOG_LEVEL", "INFO").upper()
level = getattr(logging, log_level, logging.INFO)
logging.basicConfig(
    level=level,
    format='%(levelname)s: %(message)s'
)
logger = logging.getLogger(__name__)

DEFAULT_CATEGORIES = get_categories()

# Sample seed data (inserted only if tables are empty)
DEFAULT_EXPERIENCES = [
    {
        "category_code": "FTH",
        "section": "useful",
        "title": "Review responsive breakpoints before coding",
        "playbook": (
            "When implementing a new page, confirm the design's breakpoint specs in Figma "
            "and note flex/grid expectations before writing HTML/CSS."
        ),
        "context": {
            "why": "Prevents rework caused by guessing breakpoints during build."
        },
    },
    {
        "category_code": "PGS",
        "section": "contextual",
        "title": "Capture open questions in the page specification",
        "playbook": (
            "Every page spec should end with a short 'Open Questions' list capturing product or API "
            "unknowns discovered during planning."
        ),
        "context": {
            "tip": "Helps the evaluator follow up with PM/UX before implementation starts."
        },
    },
]

DEFAULT_MANUALS = [
    {
        "category_code": "PGS",
        "name": "page-specification-checklist",
        "description": "Checklist for drafting a new page specification.",
        "content": (
            "1. Identify primary user goal and success metrics.\n"
            "2. Summarize the user journey covering entry, Happy Path, edge cases.\n"
            "3. Document data dependencies (APIs, auth, feature flags).\n"
            "4. Capture accessibility and performance notes.\n"
            "5. End with open questions and follow-up owners."
        ),
        "metadata": {
            "version": "1.0",
            "author": "CHL Team",
            "tags": ["checklist", "page-spec", "qa"],
            "chl.category_code": "PGS",
        },
    }
]


def print_header():
    """Print setup header"""
    print("\n" + "="*60)
    print("  CHL MCP Server - CPU-Only Setup")
    print("="*60 + "\n")


def check_runtime_config() -> bool:
    """Verify runtime_config.json exists and has backend=cpu"""
    runtime_config_path = PROJECT_ROOT / "data" / "runtime_config.json"

    if not runtime_config_path.exists():
        print("✗ Runtime configuration not found")
        print("\nPlease run diagnostics first:")
        print("  python scripts/setup/check_api_env.py")
        print("  (Select option 1 for CPU-only mode)")
        print("\nThis creates data/runtime_config.json with backend='cpu'.")
        return False

    try:
        with runtime_config_path.open("r") as f:
            config = json.load(f)

        backend = config.get("backend")
        if backend != "cpu":
            print(f"✗ Runtime config has backend='{backend}', expected 'cpu'")
            print("\nFor CPU-only mode, run diagnostics and select option 1:")
            print("  python scripts/setup/check_api_env.py")
            return False

        print(f"✓ CPU-only mode confirmed (backend='cpu' from {runtime_config_path})")
        return True
    except (json.JSONDecodeError, OSError) as e:
        print(f"✗ Failed to read runtime config: {e}")
        return False


def check_create_directories(config) -> bool:
    """Check and create necessary directories (no FAISS directory)"""
    logger.info("Checking directories...")

    try:
        # Data directory
        data_dir = Path(config.experience_root)
        if not data_dir.exists():
            logger.info(f"Creating data directory: {data_dir}")
            data_dir.mkdir(parents=True, exist_ok=True)
        print(f"✓ Data directory: {data_dir}")

        # Note: FAISS directory is NOT created in CPU-only mode
        print("  (FAISS directory skipped in CPU-only mode)")

        return True
    except Exception as e:
        logger.error(f"Failed to create directories: {e}")
        return False


def setup_credentials(config) -> bool:
    """Setup Google credentials from GOOGLE_CREDENTIAL_PATH environment variable

    Reads GOOGLE_CREDENTIAL_PATH from environment, copies to data/credentials/,
    sets permissions, and validates JSON structure.

    Returns:
        bool: True if credentials are set up successfully
    """
    logger.info("Setting up Google credentials...")

    # Check if GOOGLE_CREDENTIAL_PATH is set
    credential_env_path = os.getenv("GOOGLE_CREDENTIAL_PATH")
    if not credential_env_path:
        print("⚠ GOOGLE_CREDENTIAL_PATH not set in environment")
        print("  Google Sheets operations will require manual credential setup")
        print("  See .env.sample for configuration")
        return True  # Not fatal - user may configure later

    try:
        source_path = Path(credential_env_path)

        # Handle relative paths from project root
        if not source_path.is_absolute():
            source_path = PROJECT_ROOT / source_path

        # Check if source file exists
        if not source_path.exists():
            print(f"⚠ Credential file not found: {source_path}")
            print("  Create the file or update GOOGLE_CREDENTIAL_PATH in .env")
            return True  # Not fatal - user may add file later

        # Create credentials directory
        cred_dir = Path(config.experience_root) / "credentials"
        cred_dir.mkdir(parents=True, exist_ok=True)

        # Target path for credentials
        target_path = cred_dir / "service-account.json"

        # Copy credential file
        import shutil
        shutil.copy2(source_path, target_path)

        # Set chmod 600 on copied credential file (owner read/write only)
        target_path.chmod(0o600)

        # Validate JSON structure
        with target_path.open("r", encoding="utf-8") as f:
            cred_data = json.load(f)

        # Basic validation of service account JSON
        required_fields = ["type", "project_id", "private_key", "client_email"]
        missing = [f for f in required_fields if f not in cred_data]

        if missing:
            print(f"⚠ Credential file missing required fields: {', '.join(missing)}")
            print("  Please verify your service account JSON is valid")
            return True  # Not fatal - file exists but may need fixing

        if cred_data.get("type") != "service_account":
            print("⚠ Credential file type is not 'service_account'")
            print("  Please verify you're using a service account JSON key")
            return True  # Not fatal

        print(f"✓ Google credentials configured: {target_path}")
        print(f"  Service account: {cred_data.get('client_email', 'unknown')}")
        print(f"  Project: {cred_data.get('project_id', 'unknown')}")

        return True

    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in credential file: {e}")
        print(f"✗ Credential file is not valid JSON: {e}")
        return False
    except Exception as e:
        logger.error(f"Failed to setup credentials: {e}")
        print(f"✗ Failed to setup credentials: {e}")
        return False


def _legacy_skill_schema_detected(db_path: Path) -> bool:
    if not db_path.exists():
        return False
    try:
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='category_skills'"
            ).fetchone()
            if row is None:
                return False
            columns = {col[1] for col in conn.execute("PRAGMA table_info(category_skills)")}
            return "name" not in columns or "description" not in columns
    except Exception as exc:
        logger.warning("Failed to inspect database schema at %s: %s", db_path, exc)
        return False


def _reset_legacy_db(config) -> None:
    db_path = Path(config.database_path)
    if db_path.exists():
        db_path.unlink()
        for suffix in ("-wal", "-shm"):
            stale_path = Path(f"{db_path}{suffix}")
            if stale_path.exists():
                stale_path.unlink()
    print("⚠ Legacy skills schema detected; removed existing DB:")
    print(f"  {db_path}")

    faiss_dir = Path(config.faiss_index_path)
    if faiss_dir.exists():
        shutil.rmtree(faiss_dir)
        print("⚠ Legacy FAISS index removed:")
        print(f"  {faiss_dir}")
        print("  Rebuild embeddings after setup if you need vector search.")


def initialize_database(config) -> tuple[bool, dict]:
    """Initialize database and create tables"""
    logger.info("Initializing database...")

    try:
        db_path = Path(config.database_path)
        if _legacy_skill_schema_detected(db_path):
            _reset_legacy_db(config)

        db = Database(config.database_path, echo=False)
        db.init_database()
        # Ensure base tables exist for fresh installs
        db.create_tables()

        # Count entities
        def _do_counts():
            with db.session_scope() as session:
                from src.common.storage.schema import Experience, CategorySkill, Category
                exp_count_ = session.query(Experience).count()
                skill_count_ = session.query(CategorySkill).count()
                cat_count_ = session.query(Category).count()
                return exp_count_, skill_count_, cat_count_

        try:
            exp_count, skill_count, cat_count = _do_counts()
        except Exception as count_err:
            logger.error(f"Count failed (schema missing?): {count_err}")
            raise

        stats = {
            'categories': cat_count,
            'experiences': exp_count,
            'skills': skill_count
        }

        print(f"✓ Database initialized: {config.database_path}")
        print(f"  - {stats['categories']} categories")
        print(f"  - {stats['experiences']} experiences")
        print(f"  - {stats['skills']} skills")

        return True, stats
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}")
        return False, {}


def seed_default_content(config) -> bool:
    """Ensure starter categories and sample content exist."""
    logger.info("Checking default content seed...")
    try:
        db = Database(config.database_path, echo=False)
        db.init_database()
        with db.session_scope() as session:
            category_repo = CategoryRepository(session)
            categories = category_repo.get_all()
            category_codes = {c.code for c in categories}

            if not categories:
                for cat in DEFAULT_CATEGORIES:
                    category_repo.create(
                        code=cat["code"],
                        name=cat["name"],
                        description=cat["description"],
                    )
                    category_codes.add(cat["code"])
                print(f"✓ Seeded {len(DEFAULT_CATEGORIES)} starter categories")
            else:
                print(f"  (Categories already present: {len(categories)})")

            exp_repo = ExperienceRepository(session)
            skill_repo = CategorySkillRepository(session)

            exp_seeded = 0
            if session.query(Experience).count() == 0:
                for data in DEFAULT_EXPERIENCES:
                    if data["category_code"] in category_codes:
                        exp_repo.create(data)
                        exp_seeded += 1
                if exp_seeded:
                    print(f"✓ Added {exp_seeded} sample experiences")
            else:
                exp_total = session.query(Experience).count()
                print(f"  (Experiences already present: {exp_total})")

            skill_seeded = 0
            if session.query(CategorySkill).count() == 0:
                for data in DEFAULT_MANUALS:
                    if data["category_code"] in category_codes:
                        skill_repo.create(data)
                        skill_seeded += 1
                if skill_seeded:
                    print(f"✓ Added {skill_seeded} sample skill")
            else:
                skill_total = session.query(CategorySkill).count()
                print(f"  (Skills already present: {skill_total})")

        return True
    except Exception as e:
        logger.error(f"Failed to seed starter content: {e}")
        return False


def validate_setup(config) -> bool:
    """Validate setup completeness (CPU-only version)"""
    logger.info("Validating setup...")

    issues = []

    # Check data directory
    if not Path(config.experience_root).exists():
        issues.append(f"Data directory missing: {config.experience_root}")

    # Check database
    if not Path(config.database_path).exists():
        issues.append(f"Database not created: {config.database_path}")

    # Note: FAISS and ML models are NOT checked in CPU-only mode

    if issues:
        print("\n✗ Setup validation failed:")
        for issue in issues:
            print(f"  - {issue}")
        return False

    print("\n✓ Setup validation passed")
    return True


def print_next_steps():
    """Print next steps for user"""
    print("\n" + "="*60)
    print("  Setup Complete!")
    print("="*60)

    print("\nNext steps:\n")

    print("  1. Start the FastAPI server (backend auto-detected from runtime_config.json):")
    print("     python -m uvicorn src.api.server:app --host 127.0.0.1 --port 8000\n")
    print("  2. Visit http://127.0.0.1:8000/settings to verify configuration")
    print("  3. Use the web UI to add experiences and guidelines\n")

    print("\nNote: CPU-only mode uses SQLite text search (keyword matching).")
    print("For semantic search capabilities, see scripts/setup/setup-gpu.py and doc/manual.md.\n")

    print("For more information, see:")
    print("  - doc/manual.md (Section 9: CPU-Only Mode)")
    print("  - doc/concept.md\n")


def main():
    """Main setup workflow for CPU-only mode"""
    print_header()

    # 1. Verify runtime_config.json has backend=cpu
    if not check_runtime_config():
        sys.exit(1)

    try:
        # Load configuration
        logger.info("Loading configuration...")
        config = get_config()

        # 2. Check/create directories (no FAISS)
        if not check_create_directories(config):
            sys.exit(1)

        # 3. Setup credentials (optional, from GOOGLE_CREDENTIAL_PATH env)
        if not setup_credentials(config):
            sys.exit(1)

        # 4. Initialize database
        success, db_stats = initialize_database(config)
        if not success:
            sys.exit(1)

        # 5. Seed default content
        if not seed_default_content(config):
            sys.exit(1)

        # 6. Validate setup
        if not validate_setup(config):
            sys.exit(1)

        # 7. Print next steps
        print_next_steps()

    except KeyboardInterrupt:
        print("\n\nSetup interrupted by user")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Setup failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
