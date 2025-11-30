#!/usr/bin/env python3
"""DataPipe Demo - Simulates a reproducible pipeline bug for CHL A/B test."""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path


def main() -> None:
    # Generate run metadata
    run_id = f"DP-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    pipeline_stage = "transform"

    output_dir = Path("data/output")
    output_dir.mkdir(parents=True, exist_ok=True)

    input_file = "data/sample_input.csv"

    # Write metadata file the assistant can fetch later
    metadata = {
        "run_id": run_id,
        "pipeline_stage": pipeline_stage,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    metadata_path = output_dir / "run_meta.json"
    metadata_path.write_text(json.dumps(metadata, indent=2))

    # Simulate a simple FileNotFoundError and emit a short log
    error_msg = f"FileNotFoundError: {input_file} does not exist"
    now = datetime.now().isoformat()
    log_lines = [
        f"[{now}] INFO  DataPipe started",
        f"[{now}] INFO  Run ID: {run_id}",
        f"[{now}] INFO  Stage: {pipeline_stage}",
        f"[{now}] ERROR {error_msg}",
        "Traceback (most recent call last):",
        '  File "scripts/setup/demo_datapipe_bug.py", line 53, in main',
        f'    with open("{input_file}", "r") as f:',
        f"FileNotFoundError: [Errno 2] No such file or directory: '{input_file}'",
    ]
    # Pad log to ~50 lines for ticket excerpt demos
    for i in range(6, 50):
        log_lines.append(f"[{now}] DEBUG placeholder log line {i}")
    log_path = output_dir / "app.log"
    log_path.write_text("\n".join(log_lines))

    # Print concise, copy/paste-friendly output for the A/B test
    print(f"[{run_id}] Starting DataPipe at stage: {pipeline_stage}")
    print(f"[{run_id}] Reading input: {input_file}\n")

    print("=" * 60)
    print(f"ERROR: {error_msg}")
    print("=" * 60)

    print("\nArtifacts saved to:")
    print(f"  - {metadata_path}")
    print(f"  - {log_path}")
    print("\nRun ID:", run_id)
    print("Pipeline Stage:", pipeline_stage)
    print(f"\nSee {log_path} for full stack trace")

    sys.exit(1)


if __name__ == "__main__":
    main()
