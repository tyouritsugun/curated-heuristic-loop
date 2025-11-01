"""Helpers for loading shared script configuration."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Tuple

DEFAULT_CONFIG_PATH = Path(__file__).parent / "scripts_config.yaml"

try:
    import yaml  # type: ignore
except ImportError as exc:  # pragma: no cover - dependency declared in pyproject
    yaml = None
    _import_error = exc
else:
    _import_error = None


class ScriptConfigError(RuntimeError):
    """Raised when script configuration cannot be loaded or parsed."""


def load_scripts_config(path: str | None = None) -> Tuple[Dict[str, Any], Path]:
    """Load YAML configuration shared across scripts.

    Args:
        path: Optional override path. When omitted, uses scripts_config.yaml
              inside the scripts/ directory.

    Returns:
        Tuple of (configuration dictionary, resolved config path).

    Raises:
        ScriptConfigError: If the file is missing, PyYAML is unavailable, or the
            document cannot be parsed into a mapping.
    """
    config_path = Path(path).expanduser() if path else DEFAULT_CONFIG_PATH

    if not config_path.exists():
        raise ScriptConfigError(f"Configuration file not found: {config_path}")

    if yaml is None:
        raise ScriptConfigError(
            "PyYAML is required to load script configuration "
            f"({config_path}). Install dependencies with `uv sync`."
        ) from _import_error

    try:
        with config_path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except Exception as exc:
        raise ScriptConfigError(f"Failed to parse config file {config_path}: {exc}") from exc

    if not isinstance(data, dict):
        raise ScriptConfigError(
            f"Configuration file {config_path} must contain a YAML mapping at the top level."
        )

    return data, config_path
