"""Configuration management for CHL MCP Server

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
- CHL_SEARCH_TIMEOUT_MS: Query timeout in milliseconds (default: 5000)
- CHL_SEARCH_FALLBACK_RETRIES: Retries before fallback (default: 1)

Model selection (GGUF quantized):
- CHL_EMBEDDING_REPO: Advanced override for embedding repo (defaults to selection recorded by `scripts/setup.py`)
- CHL_EMBEDDING_QUANT: Advanced override for embedding quantization (defaults via setup)
- CHL_RERANKER_REPO: Advanced override for reranker repo (defaults via setup)
- CHL_RERANKER_QUANT: Advanced override for reranker quantization (defaults via setup)
- CHL_EMBEDDING_MODEL_AUTO_MIGRATE: Auto-rebuild on model change (default: 0)

Thresholds:
- CHL_DUPLICATE_THRESHOLD_UPDATE: Similarity threshold for updates (default: 0.85, range: 0.0-1.0)
- CHL_DUPLICATE_THRESHOLD_INSERT: Similarity threshold for inserts (default: 0.60, range: 0.0-1.0)
- CHL_TOPK_RETRIEVE: FAISS candidates (default: 100)
- CHL_TOPK_RERANK: Reranker candidates (default: 40)

Note: Author is automatically populated from the OS username during core setup.
"""
import os
import json
from pathlib import Path
import re

MODEL_SELECTION_PATH = Path(__file__).parent.parent / "data" / "model_selection.json"


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

        # Legacy compatibility (kept for backward compatibility)
        self.embedding_model = f"{self.embedding_repo}:{self.embedding_quant}"
        self.reranker_model = f"{self.reranker_repo}:{self.reranker_quant}"
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

        # Inline embedding on writes/updates (enabled by default)
        # Set CHL_EMBED_ON_WRITE=0 to disable
        self.embed_on_write = os.getenv("CHL_EMBED_ON_WRITE", "1") == "1"

        # Logging
        # CHL_LOG_LEVEL: DEBUG, INFO, WARNING, ERROR, CRITICAL (default: INFO)
        self.log_level = os.getenv("CHL_LOG_LEVEL", "INFO").upper()

        # Validate configuration
        self._validate_paths()
        self._validate_search_config()
    
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
        # Validate search provider
        # No extra validation needed; inline is the only mode

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

        # Create FAISS index directory if it doesn't exist
        faiss_path = Path(self.faiss_index_path)
        if not faiss_path.exists():
            try:
                faiss_path.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                raise ValueError(
                    f"Cannot create FAISS index directory '{self.faiss_index_path}': {e}"
                ) from e

def get_config() -> Config:
    """Get configuration instance"""
    return Config()
