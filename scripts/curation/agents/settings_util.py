from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional, Tuple

from dotenv import load_dotenv

from scripts._config_loader import load_scripts_config


@dataclass
class LLMSettings:
    model: str
    api_base: Optional[str]
    api_key: Optional[str]


def load_llm_settings(config_path: Optional[str] = None) -> Tuple[LLMSettings, str]:
    """Load LLM settings from scripts_config.yaml with env overrides for secrets."""
    load_dotenv()
    cfg, path = load_scripts_config(config_path)

    cur = cfg.get("curation", {})
    llm = cur.get("llm", {}) or cfg.get("curation_llm", {})

    model = os.getenv("LLM_MODEL", llm.get("model", "gpt-4.1-mini")).strip()
    api_base = os.getenv("LLM_API_BASE", llm.get("api_base"))
    api_key = (
        os.getenv("LLM_API_KEY")
        or os.getenv("OPENAI_API_KEY")
        or os.getenv("GEMINI_API_KEY")
        or os.getenv("GOOGLE_API_KEY")
        or os.getenv("ANTHROPIC_API_KEY")
        or llm.get("api_key")
    )
    settings = LLMSettings(model=model, api_base=api_base, api_key=api_key)
    return settings, str(path)


__all__ = ["LLMSettings", "load_llm_settings"]
