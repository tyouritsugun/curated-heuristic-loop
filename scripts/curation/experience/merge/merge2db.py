#!/usr/bin/env python3
"""Merge member exports and import into curation DB (with LLM health check)."""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

# Add project root to sys.path
REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from scripts.curation.agents.autogen_openai_completion_agent import run_smoke_test


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Merge member exports and import into curation DB.")
    parser.add_argument("--inputs", nargs="*", default=None, help="Optional member export directories")
    parser.add_argument(
        "--prompt-test",
        default="scripts/curation/agents/prompts/curation_prompt_test.yaml",
        help="Prompt used for LLM health check",
    )
    return parser.parse_args()


def run_step(label: str, cmd: list[str]) -> None:
    print(f"\n==> {label}", flush=True)
    subprocess.run(cmd, check=True)


def main() -> int:
    args = parse_args()
    py = sys.executable

    # LLM health check
    try:
        print("\n==> LLM health check", flush=True)
        run_smoke_test(args.prompt_test)
    except Exception as exc:
        print(f"\n❌ LLM health check failed: {exc}")
        return 1

    merge_cmd = [py, "scripts/curation/experience/merge/merge_exports.py"]
    if args.inputs:
        merge_cmd.extend(["--inputs", *args.inputs])

    import_cmd = [py, "scripts/curation/experience/merge/import_to_curation_db.py"]

    try:
        run_step("Merge member exports", merge_cmd)
        run_step("Import merged data", import_cmd)
    except subprocess.CalledProcessError as exc:
        print(f"\n❌ merge2db failed: {exc}")
        return exc.returncode

    print("\n✅ merge2db complete.")
    print()
    print("Next step: Run the overnight pipeline")
    print("  python scripts/curation/experience/overnight/run_curation_overnight.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
