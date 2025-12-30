#!/usr/bin/env python3
"""
AutoGen AssistantAgent for OpenAI-compatible and local endpoints.

Usage (smoke test):
    python -m scripts.curation.agents.autogen_openai_completion_agent \
        --prompt scripts/curation/agents/prompts/curation_prompt.yaml

Relies on scripts/scripts_config.yaml (curation.llm or curation_llm) and env vars:
  OPENAI_API_KEY / GEMINI_API_KEY / GOOGLE_API_KEY / ANTHROPIC_API_KEY / LLM_API_KEY
"""

from __future__ import annotations

import argparse
import yaml

from autogen import AssistantAgent

from scripts.curation.agents.settings_util import load_llm_settings


def build_llm_config():
    settings, cfg_path = load_llm_settings()
    if not settings.api_key:
        raise RuntimeError(
            "Missing API key for OpenAI-compatible provider. "
            "Set OPENAI_API_KEY (or GEMINI_API_KEY/GOOGLE_API_KEY/LLM_API_KEY) in your environment or .env."
        )
    cfg = {
        "config_list": [
            {
                "model": settings.model,
                "api_key": settings.api_key,
                "price": [0, 0],  # Suppress cost warning for preview models
            }
        ]
    }
    if settings.timeout is not None:
        cfg["timeout"] = settings.timeout
    if settings.api_base:
        cfg["config_list"][0]["base_url"] = settings.api_base
    return cfg, settings, cfg_path


def run_smoke_test(prompt_path: str, config_path: str | None = None) -> int:
    llm_config, settings, cfg_path = build_llm_config()
    agent = AssistantAgent(name="curation_agent", llm_config=llm_config)

    with open(prompt_path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    system_msg = data.get("system", "")
    user_msg = data.get("user", "")

    messages = []
    if system_msg:
        messages.append({"role": "system", "content": system_msg})
    messages.append({"role": "user", "content": user_msg})
    reply = agent.generate_reply(messages=messages)

    print(f"[config] {cfg_path}")
    print(f"[model] {settings.model} base={settings.api_base}")
    print(f"[response] {reply}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="OpenAI-compatible LLM smoke test.")
    parser.add_argument(
        "--prompt",
        default="scripts/curation/agents/prompts/curation_prompt.yaml",
        help="YAML with system/user messages (defaults to hello-world prompt)",
    )
    parser.add_argument("--config", default=None, help="Optional path to scripts_config.yaml")
    args = parser.parse_args()

    raise SystemExit(run_smoke_test(args.prompt, args.config))
