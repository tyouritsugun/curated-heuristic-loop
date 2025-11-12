#!/usr/bin/env python
"""First-time setup for CHL MCP Server

This script initializes the database, downloads ML models (if not cached), and
prepares the environment for running the MCP server.

Usage:
    python scripts/setup.py                  # Automatic setup with smallest models (recommended)
    python scripts/setup.py --download-models # Interactive menu to select model sizes
    python scripts/setup.py --force-models   # Re-download models (advanced)

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
    python scripts/setup.py

    # Force re-download models (if corrupted)
    python scripts/setup.py --force-models
"""
import os
import sys
import json
import argparse
import logging
import subprocess
import shutil
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config import get_config
from src.storage.database import Database
from src.storage.repository import (
    CategoryRepository,
    ExperienceRepository,
    CategoryManualRepository,
)
from src.storage.schema import Experience, CategoryManual

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(levelname)s: %(message)s'
)
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent
MODEL_SELECTION_PATH = PROJECT_ROOT / "data" / "model_selection.json"

# Supported GGUF model options (repo, quantization, VRAM requirement, description)
EMBEDDING_MODELS = [
    ("Qwen/Qwen3-Embedding-0.6B-GGUF", "Q8_0", "~600 MB", "Smallest, CPU-friendly (recommended)"),
    ("Qwen/Qwen3-Embedding-0.6B-GGUF", "f16", "~1.2 GB", "Smallest, best quality"),
    ("Qwen/Qwen3-Embedding-4B-GGUF", "Q4_K_M", "~2.5 GB", "Balanced (popular choice)"),
    ("Qwen/Qwen3-Embedding-4B-GGUF", "Q5_K_M", "~2.9 GB", "Good balance"),
    ("Qwen/Qwen3-Embedding-4B-GGUF", "Q8_0", "~4.3 GB", "Near-perfect quality"),
    ("Qwen/Qwen3-Embedding-4B-GGUF", "f16", "~8 GB", "Perfect quality, GPU needed"),
    ("Qwen/Qwen3-Embedding-8B-GGUF", "Q4_K_M", "~5 GB", "Best overall (if you have VRAM)"),
    ("Qwen/Qwen3-Embedding-8B-GGUF", "Q8_0", "~8.6 GB", "Best quality, GPU required"),
]

RERANKER_MODELS = [
    ("Mungert/Qwen3-Reranker-0.6B-GGUF", "Q4_K_M", "~300 MB", "Smallest, CPU-friendly (recommended)"),
    ("Mungert/Qwen3-Reranker-0.6B-GGUF", "Q8_0", "~600 MB", "Smallest, high quality"),
    ("Mungert/Qwen3-Reranker-4B-GGUF", "Q4_K_M", "~2.5 GB", "Balanced"),
    ("Mungert/Qwen3-Reranker-4B-GGUF", "Q8_0", "~4.3 GB", "Better quality"),
]

DEFAULT_SELECTION = {
    "embedding_repo": EMBEDDING_MODELS[0][0],
    "embedding_quant": EMBEDDING_MODELS[0][1],
    "reranker_repo": RERANKER_MODELS[0][0],
    "reranker_quant": RERANKER_MODELS[0][1],
}

# Starter category shelves (code, name, description)
DEFAULT_CATEGORIES = [
    ("FPD", "figma_page_design", "Capture heuristics for reviewing Figma designs and annotations."),
    ("DSD", "database_schema_design", "Patterns for relational schema design and evolution."),
    ("PGS", "page_specification", "End-to-end UX/page specification playbooks."),
    ("TMG", "ticket_management", "Ticket lifecycle, prioritization, and workflow management."),
    ("ADG", "architecture_design", "High-level application and system architecture decisions."),
    ("MGC", "migration_code", "Database/application migration guidance."),
    ("FTH", "frontend_html", "Frontend HTML/CSS implementation patterns."),
    ("LPW", "laravel_php_web", "Laravel PHP web app patterns (routes, controllers, models, jobs)."),
    ("PGT", "python_agent", "Python agent patterns and operational tips."),
    ("PPT", "playwright_page_test", "Playwright page test strategies."),
    ("EET", "e2e_test", "End-to-end testing guidance."),
    ("PRQ", "pull_request", "Pull request authoring and review heuristics."),
]

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
        "title": "Page specification checklist",
        "content": (
            "1. Identify primary user goal and success metrics.\n"
            "2. Summarize the user journey covering entry, Happy Path, edge cases.\n"
            "3. Document data dependencies (APIs, auth, feature flags).\n"
            "4. Capture accessibility and performance notes.\n"
            "5. End with open questions and follow-up owners."
        ),
        "summary": "Checklist the team uses when drafting a new page specification.",
    }
]


def format_model_display(repo: str | None, quant: str | None) -> str:
    """Return a user-friendly string for repo/quant combination."""
    if not repo or not quant:
        return "Unknown"
    model_name = repo.split("/")[1].replace("-GGUF", "") if "/" in repo else repo
    return f"{model_name} [{quant}]"


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
                from src.storage.schema import Experience, CategoryManual, Category
                exp_count_ = session.query(Experience).count()
                manual_count_ = session.query(CategoryManual).count()
                cat_count_ = session.query(Category).count()
                return exp_count_, manual_count_, cat_count_

        try:
            exp_count, manual_count, cat_count = _do_counts()
        except Exception as count_err:
            logger.error(f"Count failed (schema missing?): {count_err}")
            raise

        stats = {
            'categories': cat_count,
            'experiences': exp_count,
            'manuals': manual_count
        }

        print(f"✓ Database initialized: {config.database_path}")
        print(f"  - {stats['categories']} categories")
        print(f"  - {stats['experiences']} experiences")
        print(f"  - {stats['manuals']} manuals")

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
                for code, name, description in DEFAULT_CATEGORIES:
                    category_repo.create(code=code, name=name, description=description)
                    category_codes.add(code)
                print(f"✓ Seeded {len(DEFAULT_CATEGORIES)} starter categories")
            else:
                print(f"  (Categories already present: {len(categories)})")

            exp_repo = ExperienceRepository(session)
            manual_repo = CategoryManualRepository(session)

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

            manual_seeded = 0
            if session.query(CategoryManual).count() == 0:
                for data in DEFAULT_MANUALS:
                    if data["category_code"] in category_codes:
                        manual_repo.create(data)
                        manual_seeded += 1
                if manual_seeded:
                    print(f"✓ Added {manual_seeded} sample manual")
            else:
                manual_total = session.query(CategoryManual).count()
                print(f"  (Manuals already present: {manual_total})")

        if exp_seeded or manual_seeded:
            logger.info(
                "Seed data added. Use the Operations dashboard to rebuild or upload a FAISS snapshot "
                "before enabling vector search."
            )

        return True
    except Exception as e:
        logger.error(f"Failed to seed starter content: {e}")
        return False


def select_models_interactive(current_selection: dict | None = None) -> tuple[str, str, str, str]:
    """Interactive model selection menu with GGUF quantization

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
        filename = get_gguf_filename(repo, quant)
        cached = is_gguf_cached(repo, filename)
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
        filename = get_gguf_filename(repo, quant)
        cached = is_gguf_cached(repo, filename)
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


def get_gguf_filename(repo: str, quant: str) -> str:
    """Get GGUF filename based on repo and quantization"""
    # Extract model name and provider
    org = repo.split("/")[0]
    model_name = repo.split("/")[1].replace("-GGUF", "")

    # Provider-specific quantization naming
    if org == "Qwen":
        # Official Qwen: uppercase for Q variants, lowercase for f16
        quant_str = "f16" if quant.upper() == "F16" else quant.upper()
    else:
        # Community repos (Mungert, etc.): all lowercase
        quant_str = quant.lower()

    # Pattern: ModelName-Quantization.gguf
    return f"{model_name}-{quant_str}.gguf"


def download_models(config, force_models=False, embedding_repo=None, embedding_quant=None,
                    reranker_repo=None, reranker_quant=None) -> bool:
    """Download GGUF embedding and reranker models if not already cached

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
            embedding_repo = "Qwen/Qwen3-Embedding-0.6B-GGUF"
            embedding_quant = "Q8_0"  # 0.6B only has Q8_0 and f16
        if not reranker_repo:
            reranker_repo = "Mungert/Qwen3-Reranker-0.6B-GGUF"
            reranker_quant = "Q4_K_M"

        # Check if models are already cached
        embedding_filename = get_gguf_filename(embedding_repo, embedding_quant)
        embedding_cached = is_gguf_cached(embedding_repo, embedding_filename)

        reranker_filename = get_gguf_filename(reranker_repo, reranker_quant)
        reranker_cached = is_gguf_cached(reranker_repo, reranker_filename)

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
            print("\nDownloading GGUF models (this may take 5-10 minutes)...")
        print("Models will be cached in ~/.cache/huggingface/")

        # Download embedding model
        if not embedding_cached or force_models:
            logger.info(f"Downloading embedding model: {embedding_repo} [{embedding_quant}]")
            print(f"\n  [1/2] {embedding_repo.split('/')[1]} [{embedding_quant}]")
            try:
                downloaded_path = hf_hub_download(
                    repo_id=embedding_repo,
                    filename=embedding_filename,
                    repo_type="model"
                )
                print(f"        ✓ Downloaded: {embedding_filename}")
                print(f"        Path: {downloaded_path}")
            except Exception as e:
                logger.error(f"Failed to download embedding model: {e}")
                print(f"        ✗ Failed: {e}")
                print(f"        Tried to download: {embedding_filename}")
                return False
        else:
            print(f"\n  [1/2] {embedding_repo.split('/')[1]} [{embedding_quant}]")
            print(f"        ✓ Already cached")

        # Download reranker model
        if not reranker_cached or force_models:
            logger.info(f"Downloading reranker model: {reranker_repo} [{reranker_quant}]")
            print(f"\n  [2/2] {reranker_repo.split('/')[1]} [{reranker_quant}]")
            try:
                downloaded_path = hf_hub_download(
                    repo_id=reranker_repo,
                    filename=reranker_filename,
                    repo_type="model"
                )
                print(f"        ✓ Downloaded: {reranker_filename}")
                print(f"        Path: {downloaded_path}")
            except Exception as e:
                logger.error(f"Failed to download reranker model: {e}")
                print(f"        ✗ Failed: {e}")
                print(f"        Tried to download: {reranker_filename}")
                return False
        else:
            print(f"\n  [2/2] {reranker_repo.split('/')[1]} [{reranker_quant}]")
            print(f"        ✓ Already cached")

        print("\n✓ Models ready")
        return True

    except Exception as e:
        logger.error(f"Model download failed: {e}")
        return False


def is_gguf_cached(repo_id: str, filename: str) -> bool:
    """Check if a GGUF model file is already cached locally"""
    from pathlib import Path

    cache_dir = Path.home() / ".cache" / "huggingface" / "hub"
    # HuggingFace cache format: models--<org>--<name>
    model_cache_name = f"models--{repo_id.replace('/', '--')}"
    model_path = cache_dir / model_cache_name

    if not model_path.exists():
        return False

    # Check if the specific GGUF file exists in snapshots
    for snapshot_dir in model_path.glob("snapshots/*"):
        gguf_file = snapshot_dir / filename
        if gguf_file.exists():
            return True

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

    embedding_filename = get_gguf_filename(config.embedding_repo, config.embedding_quant)
    if not is_gguf_cached(config.embedding_repo, embedding_filename):
        issues.append(
            f"Embedding model not cached: {config.embedding_repo} [{config.embedding_quant}]"
        )

    reranker_filename = get_gguf_filename(config.reranker_repo, config.reranker_quant)
    if not is_gguf_cached(config.reranker_repo, reranker_filename):
        issues.append(
            f"Reranker model not cached: {config.reranker_repo} [{config.reranker_quant}]"
        )

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

    print("  1. Start the FastAPI server:")
    print("     uv run uvicorn src.api_server:app --host 127.0.0.1 --port 8000\n")
    print("  2. Visit http://127.0.0.1:8000/settings to finish onboarding, then open /operations to rebuild or upload a FAISS snapshot.")
    print("  3. (Optional) Rebuild index from scratch:")
    print("     python scripts/rebuild_index.py\n")

    selection = load_selected_models()
    print("\nActive models:")
    print(f"  Embedding: {format_model_display(selection['embedding_repo'], selection['embedding_quant'])}")
    print(f"  Reranker: {format_model_display(selection['reranker_repo'], selection['reranker_quant'])}")
    print("\nTo change models later, run:")
    print("  python scripts/setup.py --download-models\n")

    print("For more information, see:")
    print("  - doc/script_commands.md")
    print("  - doc/concept.md\n")


def check_platform():
    """Check if platform is supported"""
    import platform
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
        help='Show interactive menu to select model sizes (0.6B, 4B, 8B)'
    )
    parser.add_argument(
        '--force-models',
        action='store_true',
        help='Force re-download models even if cached (advanced)'
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
        interactive = bool(args.download_models and sys.stdin and sys.stdin.isatty())
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
