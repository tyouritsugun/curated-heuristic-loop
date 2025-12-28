#!/usr/bin/env python3
"""One-command overnight Phase 3 run (uses defaults from scripts_config.yaml)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main() -> int:
    py = sys.executable
    script = Path("scripts/curation/overnight/run_phase3.py")
    if not script.exists():
        print("❌ scripts/curation/overnight/run_phase3.py not found")
        return 1

    user_args = sys.argv[1:]
    is_help = "-h" in user_args or "--help" in user_args
    if not is_help:
        print("Starting overnight Phase 3 run with defaults...", flush=True)
        print("- To adjust behavior, edit scripts/scripts_config.yaml", flush=True)
        print("- To adjust the prompt, edit scripts/curation/agents/prompts/curation_prompt.yaml", flush=True)

    try:
        args = [py, str(script)]
        if "--reset-state" not in user_args:
            args.append("--reset-state")
        if "--db-copy" not in user_args:
            args.extend(["--db-copy", "data/curation-copy/chl_curation.db"])
            if "--state-file" not in user_args:
                args.extend(["--state-file", "data/curation-copy/.phase3_state.json"])
            if "--refresh-db-copy" not in user_args:
                args.append("--refresh-db-copy")
        args.extend(user_args)
        subprocess.run(args, check=True)
    except subprocess.CalledProcessError as exc:
        print(f"\n❌ Phase 3 run failed: {exc}")
        return exc.returncode

    if not is_help:
        print("\n✅ Phase 3 overnight run complete.")
        print("Check data/curation/morning_report.md in the morning.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
