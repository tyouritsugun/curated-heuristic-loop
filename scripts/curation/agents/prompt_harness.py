#!/usr/bin/env python3
"""
Phase 3 Stage 1: single-community prompt + validation harness.

Purpose
-------
- Build the LLM prompt payload for one community.
- Optionally call the configured OpenAI-compatible endpoint.
- Validate the response against the Step 2 contract without touching the DB.

Usage examples
--------------
# Preview prompt and validate a mocked response
python -m scripts.curation.agents.prompt_harness \
  --community-id COMM-001 \
  --mock-response '{"decision":"merge_subset","merges":[["EXP-A","EXP-B"]],"notes":"example"}'

# Call the real LLM (requires API key/model in scripts_config.yaml or env)
python -m scripts.curation.agents.prompt_harness \
  --community-id COMM-001 \
  --call-llm \
  --save-prompt /tmp/prompt.txt

Notes
-----
- No DB mutations. Reads DB only to enrich member text.
- Exit code 0 on success/valid response; 1 on validation failure.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from scripts._config_loader import load_scripts_config
from scripts.curation.agents.autogen_openai_completion_agent import build_llm_config
from scripts.curation.agents.prompt_utils import (
    build_prompt_messages,
    fetch_member_records,
    load_community,
    validate_response,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage 1 prompt + validation harness (no DB writes).")
    parser.add_argument("--community-id", required=True, help="Community ID to test (e.g., COMM-001)")
    parser.add_argument("--communities", default=None, help="Path to communities JSON (defaults from config)")
    parser.add_argument("--db-path", default=None, help="Path to curation DB (defaults from config)")
    parser.add_argument("--round-index", type=int, default=1, help="Round index for prompt context")
    parser.add_argument("--call-llm", action="store_true", help="Actually call the configured LLM endpoint")
    parser.add_argument("--mock-response", help="Raw JSON string to validate instead of calling LLM")
    parser.add_argument("--save-prompt", help="Optional file to write the rendered user prompt")
    parser.add_argument("--prompt", default=None, help="Path to YAML prompt template (system/user keys)")
    parser.add_argument(
        "--strict-members",
        action="store_true",
        help="Fail if any community members are missing from the DB",
    )
    args = parser.parse_args()

    cfg, _ = load_scripts_config()
    cur = cfg.get("curation", {})
    communities_path = Path(args.communities or cur.get("community_data_file", "data/curation/communities.json"))
    db_path = Path(args.db_path or cur.get("curation_db_path", "data/curation/chl_curation.db"))

    community = load_community(communities_path, args.community_id)
    members = fetch_member_records(db_path, community.get("members", []))
    missing = set(community.get("members", [])) - set(members.keys())
    if missing:
        print(f"⚠️  Missing {len(missing)} member records in DB: {sorted(missing)}")
        if args.strict_members:
            print("❌ Aborting due to --strict-members.")
            return 1

    prompt_path = Path(args.prompt) if args.prompt else None
    messages = build_prompt_messages(community, members, round_index=args.round_index, prompt_path=prompt_path)

    if args.save_prompt:
        Path(args.save_prompt).write_text(messages[-1]["content"], encoding="utf-8")
        print(f"Prompt user message saved to {args.save_prompt}")

    if args.mock_response:
        raw_reply = args.mock_response
        print("[mock reply] ", raw_reply)
        ok, errs, warnings, normalized = validate_response(raw_reply, community.get("members", []))
    elif args.call_llm:
        llm_config, settings, cfg_path = build_llm_config()
        print(f"[config] {cfg_path}")
        print(f"[model] {settings.model} base={settings.api_base}")
        try:
            from autogen import AssistantAgent
        except Exception as exc:  # pragma: no cover - dependency issue
            raise SystemExit(f"autogen is required to call LLM: {exc}")
        agent = AssistantAgent(name="phase3_agent", llm_config=llm_config)

        max_retries = max(0, int(settings.max_retries or 0))
        retry_delays = settings.retry_delays or []
        retry_backoff = (settings.retry_backoff or "exponential").lower()

        def delay_for(attempt_index: int) -> float:
            if attempt_index <= len(retry_delays):
                return float(retry_delays[attempt_index - 1])
            base = 5.0
            if retry_backoff == "linear":
                return base * attempt_index
            return base * (2 ** (attempt_index - 1))

        ok = False
        errs: list[str] = []
        warnings: list[str] = []
        normalized = {}
        raw_reply = ""
        for attempt in range(1, max_retries + 2):
            try:
                raw_reply = agent.generate_reply(messages=messages)
                print("[llm raw]", raw_reply)
            except Exception as exc:
                errs = [f"LLM call failed on attempt {attempt}: {exc}"]
                if attempt <= max_retries:
                    time.sleep(delay_for(attempt))
                    continue
                break

            ok, errs, warnings, normalized = validate_response(raw_reply, community.get("members", []))
            if ok:
                break
            if attempt <= max_retries:
                time.sleep(delay_for(attempt))
        # fallthrough: ok/errs/warnings populated
    else:
        print("No LLM call. Use --mock-response or --call-llm.")
        return 0

    if ok:
        print("✅ Response is valid.")
        if warnings:
            print("⚠️  Warnings:")
            for warning in warnings:
                print(" -", warning)
        if normalized and normalized != json.loads(raw_reply):
            print("[normalized]", normalized)
        return 0
    print("❌ Response failed validation:")
    for err in errs:
        print(" -", err)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
