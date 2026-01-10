#!/usr/bin/env python3
"""Merge member exports and import into curation DB (with LLM health check)."""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

def find_repo_root(start: Path) -> Path:
    for parent in [start, *start.parents]:
        if (parent / "pyproject.toml").exists():
            return parent
    return start.parents[3]


# Add project root to sys.path
REPO_ROOT = find_repo_root(Path(__file__).resolve())
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


def run_step(label: str, cmd: list[str], env: dict[str, str] | None = None) -> None:
    print(f"\n==> {label}", flush=True)
    subprocess.run(cmd, check=True, env=env)


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

    env = os.environ.copy()
    repo_root = str(REPO_ROOT)
    if env.get("PYTHONPATH"):
        env["PYTHONPATH"] = f"{repo_root}{os.pathsep}{env['PYTHONPATH']}"
    else:
        env["PYTHONPATH"] = repo_root

    merge_cmd = [py, "scripts/curation/experience/merge/merge_exports.py"]
    if args.inputs:
        merge_cmd.extend(["--inputs", *args.inputs])

    import_cmd = [py, "scripts/curation/experience/merge/import_to_curation_db.py"]

    try:
        run_step("Merge member exports", merge_cmd, env=env)
        run_step("Import merged data", import_cmd, env=env)
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
