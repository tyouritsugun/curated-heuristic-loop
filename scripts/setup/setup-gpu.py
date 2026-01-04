#!/usr/bin/env python
"""First-time setup for CHL MCP Server

This script initializes the database, downloads ML models (if not cached), and
prepares the environment for running the MCP server.

Usage:
    python scripts/setup/setup-gpu.py                      # Automatic setup using recommended/active models
    python scripts/setup/setup-gpu.py --download-models    # Ensure models are downloaded (non-interactive)
    python scripts/setup/setup-gpu.py --select-models      # Interactive menu to select model sizes
    python scripts/setup/setup-gpu.py --force-models       # Re-download models (advanced)

The setup script is fully automatic:
- Auto-creates data/ directory if missing
- Auto-detects cached models and skips download
- Auto-installs ML dependencies with: uv sync --python 3.11 --extra ml

Environment Variables (all optional with smart defaults):
    CHL_EXPERIENCE_ROOT: Path to data directory (default: <project_root>/data)
    CHL_DATABASE_PATH: Path to SQLite database (default: <experience_root>/chl.db; relative values resolve under <experience_root>)
    CHL_EMBEDDING_MODEL: Embedding model (default: Qwen/Qwen3-Embedding-0.6B)
    CHL_RERANKER_MODEL: Reranker model (default: Qwen/Qwen3-Reranker-0.6B)
    CHL_FAISS_INDEX_PATH: FAISS index directory (default: <experience_root>/faiss_index; relative values resolve under <experience_root>)

What this script does:
1. Check/create data directory structure
2. Initialize SQLite database (create tables)
3. Persist active embedding & reranker selection
4. Check if models are cached, download only if needed
5. Create FAISS index directory
6. Validate setup completeness
7. Print next steps

Example:
    # Simple automatic setup (recommended)
    python scripts/setup/setup-gpu.py

    # Force re-download models (if corrupted)
    python scripts/setup/setup-gpu.py --force-models
"""
import os
import sys
import json
import argparse
import logging
import os
import subprocess
import shutil
import platform
from datetime import datetime, timezone
from pathlib import Path

import sys

# Ensure repo root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[2]
root_str = str(PROJECT_ROOT)
if root_str not in sys.path:
    sys.path.insert(0, root_str)

from src.common.config.config import ensure_project_root_on_sys_path  # noqa: E402

ensure_project_root_on_sys_path()

from src.common.config.config import (
    DATA_DIR,
    MODEL_SELECTION_PATH,
    RUNTIME_CONFIG_PATH,
    get_config,
)  # noqa: E402
from src.common.config.categories import get_categories  # noqa: E402

try:
    from src.common.storage.database import Database
    from src.common.storage.repository import (
        CategoryRepository,
        ExperienceRepository,
        CategorySkillRepository,
    )
    from src.common.storage.schema import Experience, CategorySkill
    from src.api.services import gpu_installer
except ImportError as exc:  # Missing deps (e.g., sqlalchemy) before requirements install
    sys.stderr.write(
        "Missing dependencies for setup. Install platform requirements first, e.g.:\n"
        "  pip install -r requirements_apple.txt  # or requirements_nvidia.txt\n"
        f"Import error: {exc}\n"
    )
    raise SystemExit(1) from exc

# Configure logging
log_level = os.getenv("CHL_LOG_LEVEL", "INFO").upper()
level = getattr(logging, log_level, logging.INFO)
logging.basicConfig(
    level=level,
    format='%(levelname)s: %(message)s'
)
logger = logging.getLogger(__name__)

GPU_STATE_PATH = gpu_installer.GPU_STATE_PATH

SUPPORTED_GPU_BACKENDS = gpu_installer.SUPPORTED_GPU_BACKENDS
DEFAULT_GPU_PRIORITY = gpu_installer.DEFAULT_GPU_PRIORITY

# Supported model options (HF only)
EMBEDDING_MODELS = [
    ("Qwen/Qwen3-Embedding-0.6B", "fp16", "~1.2 GB", "HF Transformers, Metal-friendly (recommended)"),
    ("Qwen/Qwen3-Embedding-4B", "fp16", "~7.5 GB", "HF Transformers, better quality (heavier)"),
]

RERANKER_MODELS = [
    ("Qwen/Qwen3-Reranker-0.6B", "fp16", "~1.2 GB", "HF Transformers (yes/no logits) - fast, recommended"),
    ("Qwen/Qwen3-Reranker-4B", "fp16", "~7.5 GB", "HF Transformers, better quality (may be slow on Metal)"),
]

DEFAULT_SELECTION = {
    "embedding_repo": EMBEDDING_MODELS[0][0],
    "embedding_quant": EMBEDDING_MODELS[0][1],
    "reranker_repo": RERANKER_MODELS[0][0],
    "reranker_quant": RERANKER_MODELS[0][1],
}

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


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _env_flag(name: str) -> bool:
    value = os.getenv(name)
    if value is None:
        return False
    value = value.strip().lower()
    return value in {"1", "true", "yes", "on"}


def parse_gpu_priority(value: str | None) -> list[str]:
    return gpu_installer.parse_gpu_priority(value)


def load_gpu_state() -> dict | None:
    return gpu_installer.load_gpu_state(GPU_STATE_PATH)


def save_gpu_state(state: dict) -> None:
    gpu_installer.save_gpu_state(state, GPU_STATE_PATH)


# Issue #241 – GPU detection & wheel selection groundwork
def ensure_gpu_state(
    priority: list[str],
    backend_override: str | None,
    force_detect: bool,
) -> tuple[dict, bool]:
    return gpu_installer.ensure_gpu_state(priority, backend_override, force_detect, state_path=GPU_STATE_PATH)


def print_gpu_detection_summary(state: dict, cached: bool, priority: list[str]) -> None:
    print("\n" + "="*60)
    print("  GPU Detection")
    print("="*60)
    source = state.get("status", "detected")
    if cached:
        source = f"cached ({source})"
    print(f"Backend: {state.get('backend', 'unknown')} [{source}]")
    version = state.get("version") or "unknown"
    print(f"Version: {version}")
    wheel = state.get("wheel") or "cpu"
    print(f"Wheel suffix: {wheel}")
    if state.get("driver_version"):
        print(f"Driver: {state['driver_version']}")
    print(f"Priority: {', '.join(priority)}")
    diagnostics = state.get("diagnostics") or []
    if diagnostics:
        print("Diagnostics:")
        for item in diagnostics:
            print(f"  - {item}")
    print()


def format_model_display(repo: str | None, quant: str | None) -> str:
    """Return a user-friendly string for repo/quant combination."""
    if not repo or not quant:
        return "Unknown"
    model_name = repo.split("/")[1].replace("-GGUF", "") if "/" in repo else repo
    return f"{model_name} [{quant}]"


def is_repo_snapshot_cached(repo_id: str | None) -> bool:
    """Check if a HF repo has any cached snapshot locally."""
    if not repo_id:
        return False
    cache_dir = Path.home() / ".cache" / "huggingface" / "hub"
    model_cache_name = f"models--{repo_id.replace('/', '--')}"
    snapshots = cache_dir / model_cache_name / "snapshots"
    if not snapshots.exists():
        return False
    return any(snapshots.glob("*"))


def is_model_cached(repo: str | None, quant: str | None) -> bool:
    """Check cache status for HF Transformer repos."""
    return is_repo_snapshot_cached(repo)


def load_selected_models() -> dict:
    """Load persisted model selection from disk (if available)."""
    selection = DEFAULT_SELECTION.copy()
    try:
        if MODEL_SELECTION_PATH.exists():
            with MODEL_SELECTION_PATH.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                for key in ("embedding_repo", "embedding_quant", "reranker_repo", "reranker_quant"):
                    value = data.get(key)
                    if isinstance(value, str):
                        selection[key] = value
    except json.JSONDecodeError as e:
        logger.warning(f"Ignoring invalid model selection file: {MODEL_SELECTION_PATH} ({e})")
    except Exception as e:
        logger.warning(f"Failed to load model selection file: {MODEL_SELECTION_PATH} ({e})")
    return selection


def save_selected_models(
    embedding_repo: str,
    embedding_quant: str,
    reranker_repo: str,
    reranker_quant: str,
) -> None:
    """Persist current model selection to disk for future runs."""
    selection = {
        "embedding_repo": embedding_repo,
        "embedding_quant": embedding_quant,
        "reranker_repo": reranker_repo,
        "reranker_quant": reranker_quant,
    }
    try:
        existing = None
        if MODEL_SELECTION_PATH.exists():
            try:
                with MODEL_SELECTION_PATH.open("r", encoding="utf-8") as f:
                    existing = json.load(f)
            except json.JSONDecodeError:
                existing = None
        if isinstance(existing, dict):
            matches = all(existing.get(k) == v for k, v in selection.items())
            if matches:
                logger.info(f"Active model selection unchanged (already set in {MODEL_SELECTION_PATH})")
                return
        MODEL_SELECTION_PATH.parent.mkdir(parents=True, exist_ok=True)
        with MODEL_SELECTION_PATH.open("w", encoding="utf-8") as f:
            json.dump(selection, f, indent=2)
        logger.info(f"Saved active model selection to {MODEL_SELECTION_PATH}")
    except Exception as e:
        logger.warning(f"Unable to persist model selection: {e}")


def print_header():
    """Print setup header"""
    print("\n" + "="*60)
    print("  CHL MCP Server - First-Time Setup")
    print("="*60 + "\n")


def check_create_directories(config) -> bool:
    """Check and create necessary directories"""
    logger.info("Checking directories...")

    try:
        # Data directory
        data_dir = Path(config.experience_root)
        if not data_dir.exists():
            logger.info(f"Creating data directory: {data_dir}")
            data_dir.mkdir(parents=True, exist_ok=True)
        print(f"✓ Data directory: {data_dir}")

        # FAISS index directory
        faiss_dir = Path(config.faiss_index_path)
        if not faiss_dir.exists():
            logger.info(f"Creating FAISS index directory: {faiss_dir}")
            faiss_dir.mkdir(parents=True, exist_ok=True)
        print(f"✓ FAISS index directory: {faiss_dir}")

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


def initialize_database(config) -> tuple[bool, dict]:
    """Initialize database and create tables"""
    logger.info("Initializing database...")

    try:
        db = Database(config.database_path, echo=False)
        db.init_database()
        # Ensure base tables exist for fresh installs
        db.create_tables()
        # No migrations/back-compat: base tables are created from current schema

        # Count entities (retry once if a missing column is detected)
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


# Migration support removed (no backward compatibility)


def _seed_default_content(config) -> bool:
    """Ensure starter categories and sample content exist (internal helper)."""
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

        if exp_seeded or skill_seeded:
            logger.info(
                "Seed data added. Use the Operations dashboard to rebuild or upload a FAISS snapshot "
                "before enabling vector search."
            )

        return True
    except Exception as e:
        logger.error(f"Failed to seed starter content: {e}")
        return False


def _build_initial_embeddings_and_index(config) -> bool:
    """Generate embeddings for existing content and build initial FAISS index.

    This runs offline during setup so that once the API server starts, the
    FAISS index already contains vectors for any seeded experiences/skills.
    """
    logger.info("Building initial embeddings and FAISS index (if needed)...")
    try:
        # Late imports to keep module import surface minimal
        from src.api.gpu.embedding_client import EmbeddingClient, EmbeddingClientError
        from src.api.gpu.embedding_service import EmbeddingService
        from src.api.gpu.faiss_manager import initialize_faiss_with_recovery
        from src.common.storage.schema import Experience, CategorySkill

        db = Database(config.database_path, echo=False)
        db.init_database()

        # Quick check: if there is no content yet, nothing to embed
        with db.session_scope() as session:
            exp_count = session.query(Experience).count()
            man_count = session.query(CategorySkill).count()
            if exp_count == 0 and man_count == 0:
                logger.info("No experiences/skills found; skipping initial embedding/index build.")
                return True

        # Initialize embedding client once (outside session scope)
        try:
            embedding_client = EmbeddingClient(
                model_repo=config.embedding_repo,
                quantization=config.embedding_quant,
                n_ctx=2048,
                n_gpu_layers=getattr(config, "embedding_n_gpu_layers", 0),
            )
        except EmbeddingClientError as exc:
            logger.error("Embedding client initialization failed: %s", exc)
            return False

        # Build embeddings and FAISS index inside a DB session
        with db.session_scope() as session:
            faiss_manager = initialize_faiss_with_recovery(
                config,
                session,
                embedding_client=embedding_client,
                session_factory=db.get_session,
            )
            if faiss_manager is None:
                logger.warning("FAISS manager unavailable; skipping initial index build.")
                return True

            service = EmbeddingService(
                session=session,
                embedding_client=embedding_client,
                model_name=config.embedding_model,
                faiss_index_manager=faiss_manager,
            )

            # Mark any content with unknown embedding status as pending so it is picked up
            session.query(Experience).filter(Experience.embedding_status.is_(None)).update(
                {"embedding_status": "pending"}, synchronize_session=False
            )
            session.query(CategorySkill).filter(CategorySkill.embedding_status.is_(None)).update(
                {"embedding_status": "pending"}, synchronize_session=False
            )
            session.flush()

            stats = service.process_pending()
            logger.info(
                "Initial embedding pass: processed=%s, succeeded=%s, failed=%s",
                stats.get("processed"),
                stats.get("succeeded"),
                stats.get("failed"),
            )

            try:
                faiss_manager.save()
                logger.info("Initial FAISS index saved to %s", faiss_manager.index_path)
            except Exception as exc:
                logger.warning("Failed to save FAISS index: %s", exc)

        return True
    except Exception as e:
        logger.error(f"Failed to build initial embeddings/FAISS index: {e}", exc_info=True)
        return False


def select_models_interactive(current_selection: dict | None = None) -> tuple[str, str, str, str]:
    """Interactive model selection menu (HF only)

    Returns:
        tuple[str, str, str, str]: (embedding_repo, embedding_quant, reranker_repo, reranker_quant)
    """
    current_selection = current_selection or DEFAULT_SELECTION
    current_embedding = (
        current_selection.get("embedding_repo"),
        current_selection.get("embedding_quant"),
    )
    current_reranker = (
        current_selection.get("reranker_repo"),
        current_selection.get("reranker_quant"),
    )

    print("\n" + "="*60)
    print("  Model Selection")
    print("="*60)
    print("\nChoose models based on your hardware capabilities:")
    print("(Larger models provide better quality but require more memory)\n")

    print("Select Embedding Model:")
    try:
        embedding_default_idx = next(
            idx for idx, (repo, quant, _, _) in enumerate(EMBEDDING_MODELS)
            if (repo, quant) == current_embedding
        )
    except StopIteration:
        embedding_default_idx = 0
        if current_embedding[0]:
            print(f"⚠ Current embedding {format_model_display(*current_embedding)} is not in curated list.")

    for i, (repo, quant, vram, desc) in enumerate(EMBEDDING_MODELS, 1):
        model_name = repo.replace("-GGUF", "").split("/")[1]
        cached = is_model_cached(repo, quant)
        markers = []
        if (repo, quant) == current_embedding:
            markers.append("active")
        if cached:
            markers.append("cached")
        status = f" ({', '.join(markers)})" if markers else ""
        print(f"  {i}. {model_name} [{quant}]{status}")
        print(f"     VRAM: {vram} | {desc}")

    while True:
        try:
            choice = input(
                f"\nEnter choice (1-{len(EMBEDDING_MODELS)}) "
                f"[default: {embedding_default_idx + 1}]: "
            ).strip()
            if not choice:
                choice_idx = embedding_default_idx
            else:
                choice_idx = int(choice) - 1
            if 0 <= choice_idx < len(EMBEDDING_MODELS):
                embedding_repo, embedding_quant = (
                    EMBEDDING_MODELS[choice_idx][0],
                    EMBEDDING_MODELS[choice_idx][1],
                )
                break
            else:
                print(f"❌ Invalid choice. Please enter 1-{len(EMBEDDING_MODELS)}.")
        except (ValueError, KeyboardInterrupt):
            default_label = format_model_display(
                EMBEDDING_MODELS[embedding_default_idx][0],
                EMBEDDING_MODELS[embedding_default_idx][1],
            )
            print(f"\n❌ Invalid input. Using default ({default_label})")
            embedding_repo, embedding_quant = (
                EMBEDDING_MODELS[embedding_default_idx][0],
                EMBEDDING_MODELS[embedding_default_idx][1],
            )
            break

    print("\n" + "-"*60)
    print("Select Reranker Model:")
    try:
        reranker_default_idx = next(
            idx for idx, (repo, quant, _, _) in enumerate(RERANKER_MODELS)
            if (repo, quant) == current_reranker
        )
    except StopIteration:
        reranker_default_idx = 0
        if current_reranker[0]:
            print(f"⚠ Current reranker {format_model_display(*current_reranker)} is not in curated list.")

    for i, (repo, quant, vram, desc) in enumerate(RERANKER_MODELS, 1):
        model_name = repo.replace("-GGUF", "").split("/")[1]
        cached = is_model_cached(repo, quant)
        markers = []
        if (repo, quant) == current_reranker:
            markers.append("active")
        if cached:
            markers.append("cached")
        status = f" ({', '.join(markers)})" if markers else ""
        print(f"  {i}. {model_name} [{quant}]{status}")
        print(f"     VRAM: {vram} | {desc}")

    while True:
        try:
            choice = input(
                f"\nEnter choice (1-{len(RERANKER_MODELS)}) "
                f"[default: {reranker_default_idx + 1}]: "
            ).strip()
            if not choice:
                choice_idx = reranker_default_idx
            else:
                choice_idx = int(choice) - 1
            if 0 <= choice_idx < len(RERANKER_MODELS):
                reranker_repo, reranker_quant = (
                    RERANKER_MODELS[choice_idx][0],
                    RERANKER_MODELS[choice_idx][1],
                )
                break
            else:
                print(f"❌ Invalid choice. Please enter 1-{len(RERANKER_MODELS)}.")
        except (ValueError, KeyboardInterrupt):
            default_label = format_model_display(
                RERANKER_MODELS[reranker_default_idx][0],
                RERANKER_MODELS[reranker_default_idx][1],
            )
            print(f"\n❌ Invalid input. Using default ({default_label})")
            reranker_repo, reranker_quant = (
                RERANKER_MODELS[reranker_default_idx][0],
                RERANKER_MODELS[reranker_default_idx][1],
            )
            break

    print("\n" + "="*60)
    print("Selected models:")
    print(f"  Embedding: {format_model_display(embedding_repo, embedding_quant)}")
    print(f"  Reranker: {format_model_display(reranker_repo, reranker_quant)}")
    print("="*60 + "\n")

    return embedding_repo, embedding_quant, reranker_repo, reranker_quant


def download_models(config, force_models=False, embedding_repo=None, embedding_quant=None,
                    reranker_repo=None, reranker_quant=None) -> bool:
    """Download embedding and reranker models (HF) if not already cached

    Args:
        config: Configuration object
        force_models: Force re-download even if cached
        embedding_repo: GGUF model repo (e.g., "Qwen/Qwen3-Embedding-0.6B-GGUF")
        embedding_quant: Quantization type (e.g., "Q4_K_M", "Q8_0", "F16")
        reranker_repo: GGUF reranker repo
        reranker_quant: Reranker quantization type
    """

    try:
        # Check if ML dependencies available; auto-install if missing
        try:
            from huggingface_hub import hf_hub_download
        except ImportError:
            logger.warning("ML dependencies not installed; attempting auto-install via uv")
            print("\n⚠ ML dependencies not found")
            if os.getenv("CHL_AUTO_INSTALL_ML", "1") == "1":
                uv_path = shutil.which("uv")
                if not uv_path:
                    print("  'uv' not found on PATH. Install uv or run:")
                    print("    curl -LsSf https://astral.sh/uv/install.sh | sh")
                    return False
                print("  Installing ML extras with: uv sync --python 3.11 --extra ml")
                try:
                    proc = subprocess.run(
                        [uv_path, "sync", "--python", "3.11", "--extra", "ml"],
                        check=False,
                    )
                except Exception as e:
                    logger.error(f"Failed to run 'uv sync': {e}")
                    return False
                if proc.returncode != 0:
                    print("  ✗ 'uv sync' failed. Please run manually:")
                    print("    uv sync --python 3.11 --extra ml")
                    return False
                # Retry import after successful sync
                try:
                    from huggingface_hub import hf_hub_download  # type: ignore
                except ImportError:
                    print("  ✗ ML extras installed, but imports still unavailable. Please rerun setup.")
                    return False
                print("  ✓ ML extras installed")
            else:
                print("  To enable vector search, install ML extras:")
                print("    uv sync --python 3.11 --extra ml")
                return False

        # Use defaults if no custom models provided
        if not embedding_repo:
            embedding_repo = "Qwen/Qwen3-Embedding-0.6B"
            embedding_quant = "fp16"
        if not reranker_repo:
            reranker_repo = "Qwen/Qwen3-Reranker-0.6B"
            reranker_quant = "fp16"

        # Check if models are already cached
        embedding_cached = is_model_cached(embedding_repo, embedding_quant)
        reranker_cached = is_model_cached(reranker_repo, reranker_quant)

        if embedding_cached and reranker_cached and not force_models:
            print("\n✓ Models already cached")
            print(f"  - Embedding: {embedding_repo.split('/')[1]} [{embedding_quant}]")
            print(f"  - Reranker: {reranker_repo.split('/')[1]} [{reranker_quant}]")
            print("  (Use --force-models to re-download)")
            return True

        # Models need to be downloaded
        if force_models:
            print("\nForce re-downloading models...")
        else:
            print("\nDownloading models (this may take a few minutes)...")
        print("Models will be cached in ~/.cache/huggingface/")

        # Download embedding model
        if not embedding_cached or force_models:
            logger.info(f"Downloading embedding model: {embedding_repo} [{embedding_quant}]")
            print(f"\n  [1/2] {embedding_repo.split('/')[1]} [{embedding_quant}]")
            try:
                from huggingface_hub import snapshot_download  # type: ignore

                snapshot_path = snapshot_download(repo_id=embedding_repo, repo_type="model")
                print(f"        ✓ Downloaded snapshot to: {snapshot_path}")
            except Exception as e:
                logger.error(f"Failed to download embedding model: {e}")
                print(f"        ✗ Failed: {e}")
                return False
        else:
            print(f"\n  [1/2] {embedding_repo.split('/')[1]} [{embedding_quant}]")
            print(f"        ✓ Already cached")

        # Download reranker model
        if not reranker_cached or force_models:
            logger.info(f"Downloading reranker model: {reranker_repo} [{reranker_quant}]")
            print(f"\n  [2/2] {reranker_repo.split('/')[1]} [{reranker_quant}]")
            try:
                from huggingface_hub import snapshot_download  # type: ignore

                snapshot_path = snapshot_download(repo_id=reranker_repo, repo_type="model")
                print(f"        ✓ Downloaded snapshot to: {snapshot_path}")
            except Exception as e:
                logger.error(f"Failed to download reranker model: {e}")
                print(f"        ✗ Failed: {e}")
                return False
        else:
            print(f"\n  [2/2] {reranker_repo.split('/')[1]} [{reranker_quant}]")
            print(f"        ✓ Already cached")

        print("\n✓ Models ready")
        return True

    except Exception as e:
        logger.error(f"Model download failed: {e}")
        return False


def validate_setup(config) -> bool:
    """Validate setup completeness"""
    logger.info("Validating setup...")

    issues = []

    # Check data directory
    if not Path(config.experience_root).exists():
        issues.append(f"Data directory missing: {config.experience_root}")

    # Check database
    if not Path(config.database_path).exists():
        issues.append(f"Database not created: {config.database_path}")

    # Check FAISS directory
    if not Path(config.faiss_index_path).exists():
        issues.append(f"FAISS directory missing: {config.faiss_index_path}")

    if not is_repo_snapshot_cached(config.embedding_repo):
        issues.append(f"Embedding model not cached: {config.embedding_repo} [snapshot]")

    if not is_repo_snapshot_cached(config.reranker_repo):
        issues.append(f"Reranker model not cached: {config.reranker_repo} [snapshot]")

    if issues:
        print("\n✗ Setup validation failed:")
        for issue in issues:
            print(f"  - {issue}")
        return False

    print("\n✓ Setup validation passed")
    return True


def check_platform():
    """Check if platform is supported"""
    system = platform.system()
    machine = platform.machine()

    if system == "Darwin" and machine == "x86_64":
        logger.error("Intel Mac (x86_64) is not supported")
        print("\n" + "="*60)
        print("  Platform Not Supported")
        print("="*60)
        print("\nIntel Macs (x86_64) are not supported due to PyTorch")
        print("compatibility limitations.")
        print("\nSupported platforms:")
        print("  - macOS: Apple Silicon (ARM/M1/M2/M3)")
        print("  - Linux: x86_64 or ARM64")
        print("  - Windows: x86_64")
        print("\nPlease use a supported platform to run CHL MCP Server.")
        print()
        return False
    return True


def main():
    """Main setup workflow"""
    # Parse arguments
    parser = argparse.ArgumentParser(
        description='First-time setup for CHL MCP Server',
        epilog='By default, setup uses smallest models (0.6B) and auto-detects cached models.'
    )
    parser.add_argument(
        '--download-models',
        action='store_true',
        help='Ensure models are downloaded using the current active selection (non-interactive)'
    )
    parser.add_argument(
        '--select-models',
        action='store_true',
        help='Show interactive menu to select model sizes (0.6B, 4B, 8B)'
    )
    parser.add_argument(
        '--force-models',
        action='store_true',
        help='Force re-download models even if cached (advanced)'
    )
    parser.add_argument(
        '--gpu-backend',
        choices=SUPPORTED_GPU_BACKENDS,
        help='Override auto-detection and force backend (metal/cuda/rocm/cpu)'
    )
    parser.add_argument(
        '--gpu-priority',
        help='Comma-separated priority order (default: metal,cuda,rocm,cpu)'
    )
    parser.add_argument(
        '--force-detect',
        action='store_true',
        help='Ignore cached gpu_state.json and re-run hardware probes'
    )
    args = parser.parse_args()

    print_header()

    # Check platform compatibility
    if not check_platform():
        sys.exit(1)

    try:
        # Load configuration
        logger.info("Loading configuration...")
        config = get_config()

        priority_value = args.gpu_priority or os.getenv("CHL_GPU_PRIORITY")
        priority = parse_gpu_priority(priority_value)
        backend_override = args.gpu_backend or os.getenv("CHL_GPU_BACKEND")
        force_detect = args.force_detect or _env_flag("CHL_FORCE_GPU_DETECT")

        try:
            gpu_state, gpu_cached = ensure_gpu_state(priority, backend_override, force_detect)
        except ValueError as exc:
            logger.error(str(exc))
            sys.exit(1)

        print_gpu_detection_summary(gpu_state, gpu_cached, priority)

        # 1. Check/create directories
        if not check_create_directories(config):
            sys.exit(1)

        # 2. Setup credentials (optional, from GOOGLE_CREDENTIAL_PATH env)
        if not setup_credentials(config):
            sys.exit(1)

        # 3. Initialize database
        success, db_stats = initialize_database(config)
        if not success:
            sys.exit(1)

        # 4. Determine active model selection (optionally reconfigure)
        active_selection = {
            "embedding_repo": config.embedding_repo,
            "embedding_quant": config.embedding_quant,
            "reranker_repo": config.reranker_repo,
            "reranker_quant": config.reranker_quant,
        }

        embedding_repo = active_selection["embedding_repo"]
        embedding_quant = active_selection["embedding_quant"]
        reranker_repo = active_selection["reranker_repo"]
        reranker_quant = active_selection["reranker_quant"]

        print("\n" + "="*60)
        print("  Model Setup")
        print("="*60)
        interactive = bool(args.select_models and sys.stdin and sys.stdin.isatty())
        if interactive:
            try:
                embedding_repo, embedding_quant, reranker_repo, reranker_quant = select_models_interactive(active_selection)
            except Exception as e:
                logger.warning(f"Interactive selection failed, falling back to defaults: {e}")
                print("\n❕ Falling back to active/default model selection")
        else:
            print("\nActive model selection:")
            print(f"  Embedding: {format_model_display(embedding_repo, embedding_quant)}")
            print(f"  Reranker: {format_model_display(reranker_repo, reranker_quant)}")

        # 5. Download models (auto-detects if already cached)
        models_downloaded = download_models(
            config,
            force_models=args.force_models,
            embedding_repo=embedding_repo,
            embedding_quant=embedding_quant,
            reranker_repo=reranker_repo,
            reranker_quant=reranker_quant
        )
        if not models_downloaded:
            print("\n✗ Model download incomplete. Embedding and reranker are required.")
            # Continue to validation so users see concrete guidance; do not exit abruptly
            # sys.exit(1)

        # Persist selected models for future runs
        save_selected_models(embedding_repo, embedding_quant, reranker_repo, reranker_quant)
        config.embedding_repo = embedding_repo
        config.embedding_quant = embedding_quant
        config.reranker_repo = reranker_repo
        config.reranker_quant = reranker_quant
        # Note: config.embedding_model and config.reranker_model are now computed properties
        # derived from the repo and quant values above (single source of truth)

        # 6. Validate setup
        if not validate_setup(config):
            sys.exit(1)

        # 7. Seed default content (categories, sample experiences/skills)
        if not _seed_default_content(config):
            sys.exit(1)

        # 8. Build initial embeddings and FAISS index for existing content
        if not _build_initial_embeddings_and_index(config):
            # Not fatal for overall setup, but we log and continue so that
            # operators can still start the API server and inspect logs.
            logger.warning("Initial embedding/FAISS index build encountered issues; see logs for details.")

    except KeyboardInterrupt:
        print("\n\nSetup interrupted by user")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Setup failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
