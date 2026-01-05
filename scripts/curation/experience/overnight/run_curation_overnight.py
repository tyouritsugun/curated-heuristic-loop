#!/usr/bin/env python3
"""One-command overnight run (steps 3–8)."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Overnight curation (steps 3–8).")
    parser.add_argument("--db-path", default=None, help="Optional curation DB path")
    parser.add_argument("--skip-atomicity-pre-pass", action="store_true", help="Skip atomicity pre-pass")
    parser.add_argument("--with-rerank", action="store_true", help="Enable rerank when building communities")
    parser.add_argument("--resume", action="store_true", help="Resume existing state (skip --reset-state)")
    return parser.parse_args()


def main() -> int:
    py = sys.executable
    script = Path("scripts/curation/experience/overnight/run_curation_loop.py")
    if not script.exists():
        print("❌ scripts/curation/experience/overnight/run_curation_loop.py not found")
        return 1

    args = parse_args()
    db_args: list[str] = []
    if args.db_path:
        db_args = ["--db-path", args.db_path]

    print("Starting overnight run (steps 3–8)...", flush=True)
    print("- To adjust behavior, edit scripts/scripts_config.yaml", flush=True)
    print("- To adjust the prompt, edit scripts/curation/agents/prompts/curation_prompt.yaml", flush=True)

    try:
        if not args.skip_atomicity_pre_pass:
            subprocess.run(
                [py, "scripts/curation/experience/prepass/atomicity_split_prepass.py", *db_args],
                check=True,
            )

        subprocess.run([py, "scripts/curation/experience/merge/build_curation_index.py", *db_args], check=True)
        subprocess.run([py, "scripts/curation/experience/merge/find_pending_dups.py", *db_args], check=True)

        build_comm_cmd = [py, "scripts/curation/experience/merge/build_communities.py", *db_args]
        if args.with_rerank:
            build_comm_cmd.append("--with-rerank")
        subprocess.run(build_comm_cmd, check=True)

        loop_cmd = [py, str(script)]
        if not args.resume:
            loop_cmd.append("--reset-state")
        loop_cmd.extend(db_args)
        subprocess.run(loop_cmd, check=True)

        subprocess.run([py, "scripts/curation/experience/export_curated.py", *db_args], check=True)
    except subprocess.CalledProcessError as exc:
        print(f"\n❌ Overnight run failed: {exc}")
        return exc.returncode

    print("\n✅ Overnight run complete.")
    print("Check data/curation/morning_report.md in the morning.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
