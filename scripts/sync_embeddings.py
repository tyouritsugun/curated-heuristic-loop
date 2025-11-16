#!/usr/bin/env python
"""Trigger embedding sync via the API operations endpoint.

Usage:
    python scripts/sync_embeddings.py [--retry-failed] [--max-count N]

This script now triggers `/api/v1/operations/sync-embeddings`. The actual
sync (including FAISS updates) runs inside the API server.
"""
import sys
import argparse
import logging
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.common.config.config import get_config
from src.common.api_client.client import CHLAPIClient, APIOperationError, APIConnectionError

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def main():
    """Trigger embedding sync job via HTTP."""
    # Parse arguments
    parser = argparse.ArgumentParser(description='Sync embeddings for pending/failed entities via API')
    parser.add_argument(
        '--retry-failed',
        action='store_true',
        help='Retry failed embeddings in addition to pending'
    )
    parser.add_argument(
        '--max-count',
        type=int,
        default=None,
        help='Maximum number of entities to process'
    )
    args = parser.parse_args()

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

        payload: dict = {}
        if args.retry_failed:
            payload["retry_failed"] = True
        if args.max_count is not None:
            payload["max_count"] = args.max_count

        logger.info("Triggering sync-embeddings operation via API with payload=%s", payload)
        job = client.start_operation("sync-embeddings", payload=payload)
        job_id = job.get("job_id")
        if not job_id:
            raise APIOperationError("API did not return a job_id for sync-embeddings")

        logger.info("Sync job queued with id=%s; waiting for completion...", job_id)
        import time
        while True:
            status = client.get_operation_job(job_id)
            state = status.get("status")
            if state in {"succeeded", "failed", "cancelled"}:
                break
            logger.info("Job %s status=%s; waiting...", job_id, state)
            time.sleep(1.0)

        if state != "succeeded":
            logger.error("✗ Sync-embeddings job finished with status=%s error=%s", state, status.get("error"))
            sys.exit(1)

        logger.info("✓ Embedding sync completed successfully via API (job_id=%s)", job_id)

    except (APIOperationError, APIConnectionError) as exc:
        logger.error("✗ API operation failed: %s", exc)
        sys.exit(1)
    except Exception as exc:
        logger.error(f"✗ Embedding sync failed: {exc}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
