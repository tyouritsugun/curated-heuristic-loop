#!/usr/bin/env python3
"""Run the curation merge pipeline with one command."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

# Add project root to sys.path
REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from scripts._config_loader import load_scripts_config


def parse_args() -> argparse.Namespace:
    try:
        cfg, _ = load_scripts_config()
        cur = cfg.get("curation", {})
        default_db = cur.get("curation_db_path", "data/curation/chl_curation.db")
    except Exception:
        default_db = "data/curation/chl_curation.db"

    parser = argparse.ArgumentParser(description="Run merge pipeline for team curation (steps 2–5).")
    parser.add_argument("--db-path", default=default_db, help="Path to curation SQLite DB")
    parser.add_argument("--inputs", nargs="*", default=None, help="Optional member export directories")
    parser.add_argument("--force-db", action="store_true", help="Overwrite existing curation DB")
    parser.add_argument("--skip-auto-dedup", action="store_true", help="Skip auto-merge high-similarity pass")
    parser.add_argument("--with-rerank", action="store_true", help="Enable rerank scoring when building communities")
    return parser.parse_args()


def run_step(label: str, cmd: list[str]) -> None:
    print(f"\n==> {label}", flush=True)
    subprocess.run(cmd, check=True)


def main() -> int:
    args = parse_args()
    py = sys.executable

    merge_cmd = [py, "scripts/curation/experience/merge/merge_exports.py"]
    if args.inputs:
        merge_cmd.extend(["--inputs", *args.inputs])

    init_cmd = [py, "scripts/curation/experience/merge/init_curation_db.py", "--db-path", args.db_path]
    if args.force_db:
        init_cmd.append("--force")

    import_cmd = [py, "scripts/curation/experience/merge/import_to_curation_db.py", "--db-path", args.db_path]
    build_index_cmd = [py, "scripts/curation/experience/merge/build_curation_index.py", "--db-path", args.db_path]
    dedup_cmd = [py, "scripts/curation/experience/merge/find_pending_dups.py", "--db-path", args.db_path]
    build_comm_cmd = [
        py,
        "scripts/curation/experience/merge/build_communities.py",
        "--db-path",
        args.db_path,
        "--refresh-neighbors",
    ]
    if args.with_rerank:
        build_comm_cmd.append("--with-rerank")

    try:
        run_step("Merge member exports", merge_cmd)
        run_step("Initialize curation DB", init_cmd)
        run_step("Import merged data", import_cmd)
        run_step("Build embeddings + FAISS", build_index_cmd)
        if not args.skip_auto_dedup:
            run_step("Auto-merge obvious duplicates", dedup_cmd)
            run_step("Rebuild embeddings + FAISS", build_index_cmd)
        run_step("Build communities", build_comm_cmd)
    except subprocess.CalledProcessError as exc:
        print(f"\n❌ Pipeline failed: {exc}")
        return exc.returncode

    print("\n✅ Merge pipeline complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
