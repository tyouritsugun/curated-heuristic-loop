#!/usr/bin/env python
"""Trigger FAISS index rebuild via the API operations endpoint.

Usage:
    python scripts/ops/rebuild_index.py

This script now delegates rebuild work to `/api/v1/operations/rebuild-index`.
The API server must be running and configured with GPU/FAISS support.
"""
import logging
import sys
from pathlib import Path

from src.common.config.config import ensure_project_root_on_sys_path, get_config

ensure_project_root_on_sys_path()

from src.common.api_client.client import CHLAPIClient, APIOperationError, APIConnectionError

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def main():
    """Trigger FAISS index rebuild job via HTTP."""
    try:
        config = get_config()
        client = CHLAPIClient(base_url=getattr(config, "api_base_url", "http://localhost:8000"))

        if not client.check_health():
            logger.error(
                "API server is not reachable at %s. "
                "Start the API server and try again.",
                getattr(config, "api_base_url", "http://localhost:8000"),
            )
            sys.exit(1)

        logger.info("Triggering rebuild-index operation via API...")
        job = client.start_operation("rebuild-index")
        job_id = job.get("job_id")
        if not job_id:
            raise APIOperationError("API did not return a job_id for rebuild-index")

        logger.info("Rebuild job queued with id=%s; waiting for completion...", job_id)
        # Simple polling loop; in practice you might add a timeout CLI arg.
        while True:
            status = client.get_operation_job(job_id)
            state = status.get("status")
            if state in {"succeeded", "failed", "cancelled"}:
                break
            logger.info("Job %s status=%s; waiting...", job_id, state)
            import time
            time.sleep(1.0)

        if state != "succeeded":
            logger.error("✗ Rebuild-index job finished with status=%s error=%s", state, status.get("error"))
            sys.exit(1)

        logger.info("✓ FAISS index rebuild completed successfully via API (job_id=%s)", job_id)

    except (APIOperationError, APIConnectionError) as exc:
        logger.error("✗ API operation failed: %s", exc)
        sys.exit(1)
    except Exception as exc:
        logger.error(f"✗ Index rebuild failed: {exc}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
