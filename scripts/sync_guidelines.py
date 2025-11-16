"""Seed or update CHL guideline manuals via the HTTP operations API."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Optional

from src.common.api_client.client import CHLAPIClient, APIOperationError, APIConnectionError
from src.common.config.config import get_config

GENERATOR_FILE = Path("generator.md")
EVALUATOR_FILE = Path("evaluator.md")
EVALUATOR_CPU_FILE = Path("evaluator_cpu.md")


def _read_markdown(path: Path) -> Optional[str]:
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8").strip()


def sync_guidelines(
    generator_path: Path = GENERATOR_FILE,
    evaluator_path: Path = EVALUATOR_FILE,
    evaluator_cpu_path: Path = EVALUATOR_CPU_FILE,
    api_url: Optional[str] = None,
) -> bool:
    """Read markdown files and trigger the sync-guidelines operation via HTTP."""
    config = get_config()
    base_url = api_url or getattr(config, "api_base_url", "http://localhost:8000")
    client = CHLAPIClient(base_url=base_url)

    if not client.check_health():
        print(f"API server is not reachable at {base_url}. Start the API server and try again.")
        return False

    generator_md = _read_markdown(generator_path)
    evaluator_md = _read_markdown(evaluator_path)
    evaluator_cpu_md = _read_markdown(evaluator_cpu_path)

    if generator_md is None and evaluator_md is None and evaluator_cpu_md is None:
        print("No markdown files found. Nothing to sync.")
        return True

    payload = {
        "generator_content": generator_md,
        "evaluator_content": evaluator_md,
        "evaluator_cpu_content": evaluator_cpu_md,
    }

    try:
        job = client.start_operation("sync-guidelines", payload=payload)
    except (APIOperationError, APIConnectionError) as exc:
        print(f"Failed to queue sync-guidelines job: {exc}")
        return False

    job_id = job.get("job_id")
    if not job_id:
        print("API did not return a job_id for sync-guidelines")
        return False

    print(f"Sync-guidelines job queued with id={job_id}; waiting for completion...")
    while True:
        status = client.get_operation_job(job_id)
        state = status.get("status")
        if state in {"succeeded", "failed", "cancelled"}:
            break
        print(f"Job {job_id} status={state}; waiting...")
        time.sleep(1.0)

    if state != "succeeded":
        print(f"✗ Sync-guidelines job finished with status={state} error={status.get('error')}")
        return False

    result = status.get("result") or {}
    print(f"✓ Guidelines synced successfully: {result}")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Sync generator/evaluator guidelines via HTTP operations",
    )
    parser.add_argument("--api-url", help="Override API server URL")
    parser.add_argument("--generator", type=Path, default=GENERATOR_FILE, help="Path to generator.md")
    parser.add_argument("--evaluator", type=Path, default=EVALUATOR_FILE, help="Path to evaluator.md")
    parser.add_argument(
        "--evaluator-cpu",
        type=Path,
        default=EVALUATOR_CPU_FILE,
        help="Path to evaluator_cpu.md",
    )
    args = parser.parse_args()

    success = sync_guidelines(
        generator_path=args.generator,
        evaluator_path=args.evaluator,
        evaluator_cpu_path=args.evaluator_cpu,
        api_url=args.api_url,
    )
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
