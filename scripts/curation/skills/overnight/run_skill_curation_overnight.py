#!/usr/bin/env python3
"""Run overnight skill curation pipeline (steps 3–6)."""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from scripts._config_loader import load_scripts_config
from src.common.config.config import get_config


def parse_args() -> tuple[argparse.Namespace, list[str]]:
    try:
        cfg, _ = load_scripts_config()
        cur = cfg.get("curation", {})
        default_db = cur.get("curation_db_path", "data/curation/chl_curation.db")
    except Exception:
        default_db = "data/curation/chl_curation.db"

    parser = argparse.ArgumentParser(description="Run skills overnight curation (atomicity → candidates → analyze → export).")
    parser.add_argument("--db-path", default=default_db, help="Path to curation SQLite DB")
    parser.add_argument("--with-rerank", action="store_true", help="Enable rerank in candidate grouping")
    parser.add_argument("--limit", type=int, default=0, help="Limit rows for LLM steps (0=all)")
    parser.add_argument("--dry-run", action="store_true", help="Dry run for LLM steps (no DB writes)")
    args, unknown = parser.parse_known_args()
    return args, unknown


def run_step(label: str, cmd: list[str]) -> None:
    print(f"\n==> {label}", flush=True)
    subprocess.run(cmd, check=True)


def main() -> int:
    args, passthrough = parse_args()
    config = get_config()
    if not bool(getattr(config, "skills_enabled", True)):
        print("Skills are disabled; skipping skills overnight.")
        return 0

    db_path = Path(args.db_path)
    if not db_path.exists():
        print(f"❌ Error: Curation DB not found: {db_path}")
        return 1

    py = sys.executable
    atomicity_cmd = [
        py,
        "scripts/curation/skills/prepass/atomicity_split_prepass.py",
        "--db-path",
        str(db_path),
        *passthrough,
    ]
    if args.limit:
        atomicity_cmd.extend(["--limit", str(args.limit)])
    if args.dry_run:
        atomicity_cmd.append("--dry-run")

    candidates_cmd = [
        py,
        "scripts/curation/skills/merge/build_skill_candidates.py",
        "--db-path",
        str(db_path),
    ]
    if args.with_rerank:
        candidates_cmd.append("--with-rerank")

    analyze_cmd = [
        py,
        "scripts/curation/skills/merge/analyze_relationships.py",
        "--db-path",
        str(db_path),
        *passthrough,
    ]
    if args.limit:
        analyze_cmd.extend(["--limit", str(args.limit)])
    if args.dry_run:
        analyze_cmd.append("--dry-run")

    export_cmd = [
        py,
        "scripts/curation/skills/export_curated.py",
        "--db-path",
        str(db_path),
    ]

    try:
        run_step("Skill atomicity split prepass", atomicity_cmd)
        run_step("Build skill candidates", candidates_cmd)
        run_step("Analyze relationships + auto-apply", analyze_cmd)
        if not args.dry_run:
            run_step("Export curated skills", export_cmd)
    except subprocess.CalledProcessError as exc:
        print(f"\n❌ Skills overnight failed: {exc}")
        return exc.returncode

    print("\n✅ Skills overnight complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
