#!/usr/bin/env python3
"""Run overnight curation for experiences + skills."""
from __future__ import annotations

import argparse
import subprocess
import sys

from src.common.config.config import get_config


def parse_args() -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(description="Overnight curation for experiences + skills.")
    parser.add_argument(
        "--with-skills",
        action="store_true",
        help="(Deprecated) Skills run by default unless --no-skills is set.",
    )
    parser.add_argument(
        "--no-skills",
        action="store_true",
        help="Skip skills overnight even if enabled.",
    )
    args, unknown = parser.parse_known_args()
    return args, unknown


def main() -> int:
    args, passthrough = parse_args()
    py = sys.executable
    config = get_config()

    print("==> Running overnight curation for experiences", flush=True)
    overnight_cmd = [py, "scripts/curation/experience/overnight/run_curation_overnight.py", *passthrough]
    try:
        subprocess.run(overnight_cmd, check=True)
    except subprocess.CalledProcessError as exc:
        print(f"\n❌ Experience overnight failed: {exc}")
        return exc.returncode

    run_skills = not args.no_skills
    if run_skills:
        if not bool(getattr(config, "skills_enabled", True)):
            print("\n⚠️ Skills are disabled. Skipping skills overnight.", flush=True)
        else:
            print("\n==> Running overnight curation for skills", flush=True)
            skills_cmd = [py, "scripts/curation/skills/overnight/run_skill_curation_overnight.py", *passthrough]
            try:
                subprocess.run(skills_cmd, check=True)
            except subprocess.CalledProcessError as exc:
                print(f"\n❌ Skills overnight failed: {exc}")
                return exc.returncode

    _write_curation_summary()
    print("\n✅ Overnight run complete.")
    return 0


def _write_curation_summary() -> None:
    from pathlib import Path
    import csv
    from datetime import datetime, timezone

    approved_dir = Path("data/curation/approved")
    approved_dir.mkdir(parents=True, exist_ok=True)

    def count_rows(path: Path, delimiter: str) -> int:
        if not path.exists():
            return 0
        with path.open("r", encoding="utf-8") as fh:
            reader = csv.reader(fh, delimiter=delimiter)
            rows = list(reader)
        return max(len(rows) - 1, 0)

    experiences_tsv = approved_dir / "experiences.tsv"
    skills_tsv = approved_dir / "skills.tsv"
    exp_count = count_rows(experiences_tsv, "\t")
    skill_count = count_rows(skills_tsv, "\t")

    summary_path = approved_dir / "curation_summary.md"
    summary_lines = [
        "# Curation Summary",
        "",
        f"- Generated at: {datetime.now(timezone.utc).isoformat()}",
        f"- Experiences (approved TSV): {exp_count} rows",
        f"- Skills (approved TSV): {skill_count} rows",
        "",
        "## Files",
        f"- {experiences_tsv}",
        f"- {skills_tsv}",
    ]
    summary_path.write_text("\n".join(summary_lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
