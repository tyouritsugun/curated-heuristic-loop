"""Configuration management for CHL (shared across API and MCP).

This module is a thin re-export of the legacy `src.config` to avoid
breaking existing behavior while we complete the directory migration.
"""

from src.config import *  # type: ignore  # noqa: F401,F403
