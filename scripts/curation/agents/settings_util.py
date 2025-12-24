from __future__ import annotations

import os
from dataclasses import dataclass
from typing import List, Optional, Tuple

from dotenv import load_dotenv

from scripts._config_loader import load_scripts_config


@dataclass
class LLMSettings:
    model: str
    api_base: Optional[str]
    api_key: Optional[str]
    timeout: Optional[int] = None
    max_retries: int = 2
    retry_backoff: str = "exponential"
    retry_delays: Optional[List[int]] = None


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
    timeout = llm.get("timeout")
    max_retries = int(llm.get("max_retries", 2) or 2)
    retry_backoff = llm.get("retry_backoff", "exponential")
    retry_delays = llm.get("retry_delays")

    settings = LLMSettings(
        model=model,
        api_base=api_base,
        api_key=api_key,
        timeout=timeout,
        max_retries=max_retries,
        retry_backoff=retry_backoff,
        retry_delays=retry_delays,
    )
    return settings, str(path)


__all__ = ["LLMSettings", "load_llm_settings"]
