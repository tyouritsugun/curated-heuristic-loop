#!/usr/bin/env python3
"""Import Google Sheet data via API (HTTP-based, Phase 0 architecture).

This script:
1. Reads data from Google Sheets
2. Sends it to /api/v1/operations/import-sheets via HTTP
3. Polls for completion

The actual import work (clearing DB, inserting data) happens server-side.
"""
import sys
import argparse
import logging
import os
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.common.config.config import get_config
from src.common.api_client.client import CHLAPIClient, APIOperationError, APIConnectionError
from src.common.storage.sheets_client import SheetsClient

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Default worksheet names
DEFAULT_WORKSHEET_EXPERIENCES = "Experiences"
DEFAULT_WORKSHEET_MANUALS = "Manuals"
DEFAULT_WORKSHEET_CATEGORIES = "Categories"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import data from Google Sheets via API",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip confirmation prompt (dangerous).",
    )
    parser.add_argument(
        "--api-url",
        default="http://localhost:8000",
        help="API server URL (default: http://localhost:8000)",
    )
    args = parser.parse_args()

    try:
        config = get_config()
        client = CHLAPIClient(base_url=args.api_url)

        # Check API health
        if not client.check_health():
            logger.error(
                "API server is not reachable at %s. "
                "Start the API server and try again.",
                args.api_url,
            )
            sys.exit(1)

        # Read credentials path from environment (required)
        credentials_env = os.getenv("GOOGLE_CREDENTIAL_PATH")
        if not credentials_env:
            logger.error(
                "GOOGLE_CREDENTIAL_PATH not set in .env file. "
                "Copy .env.sample to .env and set your credentials path."
            )
            sys.exit(1)

        # Resolve credentials path relative to project root
        project_root = Path(__file__).parent.parent
        credentials_path = Path(credentials_env)
        if not credentials_path.is_absolute():
            credentials_path = (project_root / credentials_path).resolve()

        if not credentials_path.exists():
            logger.error(
                f"Credential file not found: {credentials_path}. "
                "Check GOOGLE_CREDENTIAL_PATH in .env file."
            )
            sys.exit(1)

        # Read spreadsheet ID from environment (required)
        spreadsheet_id = os.getenv("IMPORT_SPREADSHEET_ID", "").strip()
        if not spreadsheet_id:
            logger.error(
                "IMPORT_SPREADSHEET_ID not set in .env file. "
                "Copy .env.sample to .env and set your import spreadsheet ID."
            )
            sys.exit(1)

        # Get worksheet names from environment (optional)
        category_worksheet = os.getenv("IMPORT_WORKSHEET_CATEGORIES") or DEFAULT_WORKSHEET_CATEGORIES
        experiences_worksheet = os.getenv("IMPORT_WORKSHEET_EXPERIENCES") or DEFAULT_WORKSHEET_EXPERIENCES
        manuals_worksheet = os.getenv("IMPORT_WORKSHEET_MANUALS") or DEFAULT_WORKSHEET_MANUALS

        # Confirm destructive operation
        if not args.yes:
            response = input(
                "This will DELETE all local experiences/manuals and replace them from Google Sheets.\n"
                "Continue? [y/N]: "
            ).strip().lower()
            if response not in {"y", "yes"}:
                print("Import aborted.")
                return

        # Read from Google Sheets
        logger.info("Reading data from Google Sheets...")
        sheets = SheetsClient(str(credentials_path))

        try:
            categories_rows = sheets.read_worksheet(spreadsheet_id, category_worksheet)
            experiences_rows = sheets.read_worksheet(spreadsheet_id, experiences_worksheet)
            manuals_rows = sheets.read_worksheet(spreadsheet_id, manuals_worksheet)
        except Exception as exc:
            logger.error(f"Failed to read from Google Sheets: {exc}")
            sys.exit(1)

        logger.info(
            "Fetched %s category rows, %s experience rows and %s manual rows from Google Sheets",
            len(categories_rows),
            len(experiences_rows),
            len(manuals_rows),
        )

        # Validate required columns exist
        if not categories_rows:
            logger.error("Categories worksheet is empty. Ensure the export includes category rows before importing.")
            sys.exit(1)

        # Send to API for processing
        payload = {
            "categories": categories_rows,
            "experiences": experiences_rows,
            "manuals": manuals_rows,
        }

        logger.info("Triggering import-sheets operation via API...")
        job = client.start_operation("import-sheets", payload=payload)
        job_id = job.get("job_id")
        if not job_id:
            raise APIOperationError("API did not return a job_id for import-sheets")

        logger.info("Import job queued with id=%s; waiting for completion...", job_id)

        # Poll for completion
        import time
        while True:
            status = client.get_operation_job(job_id)
            state = status.get("status")
            if state in {"succeeded", "failed", "cancelled"}:
                break
            logger.info("Job %s status=%s; waiting...", job_id, state)
            time.sleep(1.0)

        if state != "succeeded":
            logger.error("✗ Import job finished with status=%s error=%s", state, status.get("error"))
            sys.exit(1)

        result = status.get("result", {})
        if isinstance(result, str):
            import json
            try:
                result = json.loads(result)
            except Exception:
                pass

        counts = result.get("counts", {}) if isinstance(result, dict) else {}
        logger.info(
            "✓ Import completed successfully: %s categories, %s experiences, %s manuals",
            counts.get("categories", "?"),
            counts.get("experiences", "?"),
            counts.get("manuals", "?"),
        )

    except (APIOperationError, APIConnectionError) as exc:
        logger.error("✗ API operation failed: %s", exc)
        sys.exit(1)
    except Exception as exc:
        logger.error(f"✗ Import failed: {exc}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
