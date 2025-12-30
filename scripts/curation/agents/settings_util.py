from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

from dotenv import dotenv_values, find_dotenv

from scripts._config_loader import load_scripts_config


@dataclass
class LLMSettings:
    model: str
    api_base: Optional[str]
    api_key: Optional[str]
    timeout: Optional[int] = None


def load_llm_settings(config_path: Optional[str] = None) -> Tuple[LLMSettings, str]:
    """Load LLM settings from scripts_config.yaml with .env overrides for secrets."""
    dotenv_path = find_dotenv(usecwd=True) or None
    dotenv_vars = dotenv_values(dotenv_path) if dotenv_path else {}
    cfg, path = load_scripts_config(config_path)

    cur = cfg.get("curation", {})
    llm = cur.get("llm", {}) or cfg.get("curation_llm", {})

    model = (dotenv_vars.get("LLM_MODEL") or "").strip() or llm.get("model")
    if not model:
        raise RuntimeError(
            "Missing LLM model configuration. "
            f"Set LLM_MODEL in .env or 'curation_llm.model' in {path}"
        )
    model = model.strip()
    api_base = (dotenv_vars.get("LLM_API_BASE") or "").strip() or llm.get("api_base")
    api_key = (
        dotenv_vars.get("LLM_API_KEY")
        or dotenv_vars.get("OPENAI_API_KEY")
        or dotenv_vars.get("GEMINI_API_KEY")
        or dotenv_vars.get("GOOGLE_API_KEY")
        or dotenv_vars.get("ANTHROPIC_API_KEY")
    )
    timeout = llm.get("llm_response_timeout", llm.get("timeout"))
    settings = LLMSettings(
        model=model,
        api_base=api_base,
        api_key=api_key,
        timeout=timeout,
    )
    return settings, str(path)


__all__ = ["LLMSettings", "load_llm_settings"]
