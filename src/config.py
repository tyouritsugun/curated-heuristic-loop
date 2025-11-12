"""Configuration management for CHL MCP Server

This module automatically loads environment variables from .env file using python-dotenv.
All configuration can be set via environment variables or .env file.

Example MCP configuration in ~/.cursor/mcp.json (using project venv):

{
  "chl": {
    "command": "/absolute/path/to/curated-heuristic-loop/.venv/bin/python",
    "args": ["src/server.py"],
    "env": {
      "CHL_EXPERIENCE_ROOT": "/absolute/path/to/curated-heuristic-loop/data",
      "CHL_DATABASE_PATH": "/absolute/path/to/curated-heuristic-loop/data/chl.db",
      "CHL_READ_DETAILS_LIMIT": "10"
    }
  }
}

Core environment variables:
- CHL_EXPERIENCE_ROOT: Path to data directory (optional; default <project_root>/data, auto-created if missing)
- CHL_DATABASE_PATH: Path to SQLite database file (optional; default: <experience_root>/chl.db; relative values resolve under <experience_root>)
- CHL_DATABASE_ECHO: Enable SQLAlchemy SQL logging (optional, default: false)
- CHL_READ_DETAILS_LIMIT: Max entries returned by read_entries (optional, default: 10)

Search & retrieval:
- CHL_SEARCH_MODE: Search mode (default: auto; options: auto, sqlite_only)
  - auto: Try vector search; fall back to SQLite if initialization fails
  - sqlite_only: Force text search; skip embedding/reranker/FAISS initialization
- CHL_SEARCH_TIMEOUT_MS: Query timeout in milliseconds (default: 5000)
- CHL_SEARCH_FALLBACK_RETRIES: Retries before fallback (default: 1)

Model selection (GGUF quantized):
- CHL_EMBEDDING_REPO: Advanced override for embedding repo (defaults to selection recorded by `scripts/setup-gpu.py`)
- CHL_EMBEDDING_QUANT: Advanced override for embedding quantization (defaults via setup)
- CHL_RERANKER_REPO: Advanced override for reranker repo (defaults via setup)
- CHL_RERANKER_QUANT: Advanced override for reranker quantization (defaults via setup)
- CHL_EMBEDDING_MODEL_AUTO_MIGRATE: Auto-rebuild on model change (default: 0)

Thresholds:
- CHL_DUPLICATE_THRESHOLD_UPDATE: Similarity threshold for updates (default: 0.85, range: 0.0-1.0)
- CHL_DUPLICATE_THRESHOLD_INSERT: Similarity threshold for inserts (default: 0.60, range: 0.0-1.0)
- CHL_TOPK_RETRIEVE: FAISS candidates (default: 100)
- CHL_TOPK_RERANK: Reranker candidates (default: 40)

API Client (Phase 2):
- CHL_API_BASE_URL: API server base URL (default: http://localhost:8000)
- CHL_API_TIMEOUT: HTTP request timeout in seconds (default: 30.0)
- CHL_API_HEALTH_CHECK_MAX_WAIT: Max seconds to wait for API health on startup (default: 30)
- CHL_API_CIRCUIT_BREAKER_THRESHOLD: Failures before circuit breaker opens (default: 5)
- CHL_API_CIRCUIT_BREAKER_TIMEOUT: Seconds before circuit breaker retries (default: 60)

Operations:
- CHL_OPERATIONS_MODE: Operations driver for import/export/index (default: scripts; options: scripts, noop)
- CHL_OPERATIONS_TIMEOUT_SEC: Max seconds per operation (default: 900; minimum enforced: 60)
- CHL_CATEGORIES_CACHE_TTL: Seconds to cache MCP categories/tool index (default: 30.0)

FAISS Persistence (Phase 3):
- CHL_FAISS_SAVE_POLICY: Save policy (default: immediate; options: immediate, periodic, manual)
- CHL_FAISS_SAVE_INTERVAL: Save interval in seconds for periodic mode (default: 300)
- CHL_FAISS_REBUILD_THRESHOLD: Tombstone ratio threshold for automatic rebuild (default: 0.10)

Note: Author is automatically populated from the OS username during core setup.
"""
import os
import json
import logging
from pathlib import Path
import re
from dotenv import load_dotenv

# Auto-load .env from project root (before Config class initialization)
PROJECT_ROOT = Path(__file__).parent.parent
load_dotenv(PROJECT_ROOT / ".env")

MODEL_SELECTION_PATH = PROJECT_ROOT / "data" / "model_selection.json"
logger = logging.getLogger(__name__)


def _load_model_selection() -> dict:
    """Load persisted model selection from setup (if present)."""
    try:
        if MODEL_SELECTION_PATH.exists():
            with MODEL_SELECTION_PATH.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                # Only keep string values for expected keys
                return {
                    key: value
                    for key, value in data.items()
                    if key in {"embedding_repo", "embedding_quant", "reranker_repo", "reranker_quant"}
                    and isinstance(value, str)
                }
    except (json.JSONDecodeError, OSError):
        pass
    return {}


class Config:
    """Configuration holder for CHL MCP Server

    Configuration is loaded from environment variables set by MCP container.
    See module docstring for example settings.json configuration.
    """

    def __init__(self):
        # Required settings - loaded from MCP environment
        # Default to <project_root>/data if not set
        default_root = Path(__file__).parent.parent / "data"
        self.experience_root = os.getenv("CHL_EXPERIENCE_ROOT", str(default_root))

        # Database settings (default under experience_root; resolve relative paths under experience_root)
        db_env = os.getenv("CHL_DATABASE_PATH")
        if db_env:
            db_path = Path(db_env)
            if not db_path.is_absolute():
                db_path = Path(self.experience_root) / db_path
        else:
            db_path = Path(self.experience_root) / "chl.db"
        self.database_path = str(db_path)
        self.database_echo = os.getenv("CHL_DATABASE_ECHO", "false").lower() == "true"

        # Optional settings with defaults
        self.read_details_limit = int(os.getenv("CHL_READ_DETAILS_LIMIT", "10"))

        # Search & provider settings
        self.search_mode = os.getenv("CHL_SEARCH_MODE", "auto").lower()
        self.search_timeout_ms = int(os.getenv("CHL_SEARCH_TIMEOUT_MS", "5000"))
        self.search_fallback_retries = int(os.getenv("CHL_SEARCH_FALLBACK_RETRIES", "1"))

        # Model settings (GGUF models)
        model_selection = _load_model_selection()
        default_embedding_repo = model_selection.get("embedding_repo", "Qwen/Qwen3-Embedding-0.6B-GGUF")
        default_embedding_quant = model_selection.get("embedding_quant", "Q8_0")
        default_reranker_repo = model_selection.get("reranker_repo", "Mungert/Qwen3-Reranker-0.6B-GGUF")
        default_reranker_quant = model_selection.get("reranker_quant", "Q4_K_M")

        # Embedding model configuration
        self.embedding_repo = os.getenv("CHL_EMBEDDING_REPO", default_embedding_repo)
        self.embedding_quant = os.getenv("CHL_EMBEDDING_QUANT", default_embedding_quant)

        # Reranker model configuration
        self.reranker_repo = os.getenv("CHL_RERANKER_REPO", default_reranker_repo)
        self.reranker_quant = os.getenv("CHL_RERANKER_QUANT", default_reranker_quant)

        # Model migration settings
        self.embedding_model_auto_migrate = os.getenv("CHL_EMBEDDING_MODEL_AUTO_MIGRATE", "0") == "1"

        # Threshold settings
        self.duplicate_threshold_update = float(os.getenv("CHL_DUPLICATE_THRESHOLD_UPDATE", "0.85"))
        self.duplicate_threshold_insert = float(os.getenv("CHL_DUPLICATE_THRESHOLD_INSERT", "0.60"))
        self.topk_retrieve = int(os.getenv("CHL_TOPK_RETRIEVE", "100"))
        self.topk_rerank = int(os.getenv("CHL_TOPK_RERANK", "40"))

        # Path settings (default under experience_root; resolve relative paths under experience_root)
        faiss_env = os.getenv("CHL_FAISS_INDEX_PATH")
        if faiss_env:
            faiss_path = Path(faiss_env)
            if not faiss_path.is_absolute():
                faiss_path = Path(self.experience_root) / faiss_path
        else:
            faiss_path = Path(self.experience_root) / "faiss_index"
        self.faiss_index_path = str(faiss_path)

        # API client configuration (Phase 2)
        self.api_base_url = os.getenv("CHL_API_BASE_URL", "http://localhost:8000")
        self.api_timeout = float(os.getenv("CHL_API_TIMEOUT", "30.0"))
        self.api_health_check_max_wait = int(os.getenv("CHL_API_HEALTH_CHECK_MAX_WAIT", "30"))
        self.api_circuit_breaker_threshold = int(os.getenv("CHL_API_CIRCUIT_BREAKER_THRESHOLD", "5"))
        self.api_circuit_breaker_timeout = int(os.getenv("CHL_API_CIRCUIT_BREAKER_TIMEOUT", "60"))

        # Logging
        # CHL_LOG_LEVEL: DEBUG, INFO, WARNING, ERROR, CRITICAL (default: INFO)
        self.log_level = os.getenv("CHL_LOG_LEVEL", "INFO").upper()

        # FAISS persistence configuration (Phase 3)
        self.faiss_save_policy = os.getenv("CHL_FAISS_SAVE_POLICY", "immediate")
        self.faiss_save_interval = int(os.getenv("CHL_FAISS_SAVE_INTERVAL", "300"))
        self.faiss_rebuild_threshold = float(os.getenv("CHL_FAISS_REBUILD_THRESHOLD", "0.10"))

        # Validate configuration
        self._validate_paths()
        self._validate_search_config()
        self._validate_faiss_config()
    
    def _validate_paths(self):
        """Validate that configured paths exist"""
        exp_root = Path(self.experience_root)
        if not exp_root.exists():
            # Auto-create directory during setup/initialization
            try:
                exp_root.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                raise ValueError(
                    f"Experience root directory does not exist and could not be created: "
                    f"{self.experience_root}. Error: {e}"
                )

        # Export/sync credentials are optional until those workflows are configured.

    def _validate_search_config(self):
        """Validate search-related configuration with helpful error messages"""
        # Validate search mode
        valid_modes = ("auto", "sqlite_only")
        if self.search_mode not in valid_modes:
            raise ValueError(
                f"Invalid CHL_SEARCH_MODE='{self.search_mode}'. "
                f"Must be one of: {', '.join(valid_modes)}"
            )

        # Validate thresholds are in [0.0, 1.0]
        if not (0.0 <= self.duplicate_threshold_update <= 1.0):
            raise ValueError(
                f"Invalid CHL_DUPLICATE_THRESHOLD_UPDATE={self.duplicate_threshold_update}. "
                f"Must be in range [0.0, 1.0]."
            )

        if not (0.0 <= self.duplicate_threshold_insert <= 1.0):
            raise ValueError(
                f"Invalid CHL_DUPLICATE_THRESHOLD_INSERT={self.duplicate_threshold_insert}. "
                f"Must be in range [0.0, 1.0]."
            )

        # Validate GGUF repo names match HuggingFace format (org/model-name)
        hf_pattern = re.compile(r'^[\w-]+/[\w.-]+$')

        if not hf_pattern.match(self.embedding_repo):
            raise ValueError(
                f"Invalid CHL_EMBEDDING_REPO='{self.embedding_repo}'. "
                f"Must match HuggingFace format: org/model-name"
            )

        if not hf_pattern.match(self.reranker_repo):
            raise ValueError(
                f"Invalid CHL_RERANKER_REPO='{self.reranker_repo}'. "
                f"Must match HuggingFace format: org/model-name"
            )

        # Validate quantization types
        valid_quants = ["Q2_K", "Q3_K_S", "Q3_K_M", "Q3_K_L", "Q4_0", "Q4_1",
                        "Q4_K_S", "Q4_K_M", "Q5_0", "Q5_1", "Q5_K_S", "Q5_K_M",
                        "Q6_K", "Q8_0", "F16", "f16"]

        if self.embedding_quant not in valid_quants:
            raise ValueError(
                f"Invalid CHL_EMBEDDING_QUANT='{self.embedding_quant}'. "
                f"Must be one of: {', '.join(valid_quants)}"
            )

        if self.reranker_quant not in valid_quants:
            raise ValueError(
                f"Invalid CHL_RERANKER_QUANT='{self.reranker_quant}'. "
                f"Must be one of: {', '.join(valid_quants)}"
            )

        # Validate positive integers
        if self.search_timeout_ms <= 0:
            raise ValueError(
                f"Invalid CHL_SEARCH_TIMEOUT_MS={self.search_timeout_ms}. Must be > 0."
            )

        if self.search_fallback_retries < 0:
            raise ValueError(
                f"Invalid CHL_SEARCH_FALLBACK_RETRIES={self.search_fallback_retries}. Must be >= 0."
            )

        if self.topk_retrieve <= 0:
            raise ValueError(
                f"Invalid CHL_TOPK_RETRIEVE={self.topk_retrieve}. Must be > 0."
            )

        if self.topk_rerank <= 0:
            raise ValueError(
                f"Invalid CHL_TOPK_RERANK={self.topk_rerank}. Must be > 0."
            )

        # Create FAISS index directory if it doesn't exist (skip in sqlite_only mode)
        if self.search_mode != "sqlite_only":
            faiss_path = Path(self.faiss_index_path)
            if not faiss_path.exists():
                try:
                    faiss_path.mkdir(parents=True, exist_ok=True)
                except Exception as e:
                    raise ValueError(
                        f"Cannot create FAISS index directory '{self.faiss_index_path}': {e}"
                    ) from e

    def _validate_faiss_config(self):
        """Validate FAISS persistence configuration"""
        # Validate save policy
        valid_policies = ("immediate", "periodic", "manual")
        if self.faiss_save_policy not in valid_policies:
            raise ValueError(
                f"Invalid CHL_FAISS_SAVE_POLICY='{self.faiss_save_policy}'. "
                f"Must be one of: {', '.join(valid_policies)}"
            )

        # Validate save interval (must be positive)
        if self.faiss_save_interval <= 0:
            raise ValueError(
                f"Invalid CHL_FAISS_SAVE_INTERVAL={self.faiss_save_interval}. "
                f"Must be > 0 seconds."
            )

        # Validate rebuild threshold (must be between 0.0 and 1.0)
        if not (0.0 <= self.faiss_rebuild_threshold <= 1.0):
            raise ValueError(
                f"Invalid CHL_FAISS_REBUILD_THRESHOLD={self.faiss_rebuild_threshold}. "
                f"Must be in range [0.0, 1.0]."
            )

    # ========================================================================
    # Computed Properties - Single Source of Truth for Model Names
    # ========================================================================

    @property
    def embedding_model(self) -> str:
        """Full embedding model name in format 'repo:quant'.

        This is the canonical format used for:
        - FAISS index file naming
        - Database embedding records
        - Model identification in metadata

        Example: "Qwen/Qwen3-Embedding-4B-GGUF:Q4_K_M"
        """
        return f"{self.embedding_repo}:{self.embedding_quant}"

    @property
    def reranker_model(self) -> str:
        """Full reranker model name in format 'repo:quant'.

        Example: "Mungert/Qwen3-Reranker-4B-GGUF:Q4_K_M"
        """
        return f"{self.reranker_repo}:{self.reranker_quant}"


def get_config() -> Config:
    """Get configuration instance"""
    return Config()
