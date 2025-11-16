#!/usr/bin/env python3
"""Export the entire local SQLite dataset to Google Sheets via API.

This script:
1. Fetches all data from /api/v1/entries/export via HTTP
2. Writes to Google Sheets

The actual data fetching happens server-side.
"""
import sys
import argparse
import logging
import os
from pathlib import Path
from typing import List

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

# Column definitions (order matters for round-tripping via Sheets)
EXPERIENCE_COLUMNS = [
    "id", "category_code", "section", "title", "playbook", "context",
    "source", "sync_status", "author", "embedding_status",
    "created_at", "updated_at", "synced_at", "exported_at",
]

MANUAL_COLUMNS = [
    "id", "category_code", "title", "content", "summary",
    "source", "sync_status", "author", "embedding_status",
    "created_at", "updated_at", "synced_at", "exported_at",
]

CATEGORY_COLUMNS = [
    "code", "name", "description", "created_at",
]

# Default worksheet names
DEFAULT_WORKSHEET_EXPERIENCES = "Experiences"
DEFAULT_WORKSHEET_MANUALS = "Manuals"
DEFAULT_WORKSHEET_CATEGORIES = "Categories"


def _row(values: List[str]) -> List[str]:
    """Convert values to strings, replacing None with empty string."""
    return ["" if v is None else str(v) for v in values]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export data from database to Google Sheets via API",
    )
    parser.add_argument(
        "--api-url",
        default="http://localhost:8000",
        help="API server URL (default: http://localhost:8000)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print data without writing to Sheets",
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

        # Fetch all data from API
        logger.info("Fetching data from API...")
        try:
            data = client.export_entries()
        except Exception as exc:
            logger.error(f"Failed to fetch data from API: {exc}")
            sys.exit(1)

        experiences = data.get("experiences", [])
        manuals = data.get("manuals", [])
        categories = data.get("categories", [])

        logger.info(
            "Fetched %s experiences, %s manuals, %s categories from API",
            len(experiences),
            len(manuals),
            len(categories),
        )

        if args.dry_run:
            logger.info("Dry run mode - not writing to sheets")
            logger.info("Categories: %s rows", len(categories))
            logger.info("Experiences: %s rows", len(experiences))
            logger.info("Manuals: %s rows", len(manuals))
            return

        # Read Google Sheets configuration from environment
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
        spreadsheet_id = os.getenv("EXPORT_SPREADSHEET_ID", "").strip()
        if not spreadsheet_id:
            logger.error(
                "EXPORT_SPREADSHEET_ID not set in .env file. "
                "Copy .env.sample to .env and set your export spreadsheet ID."
            )
            sys.exit(1)

        # Get worksheet names from environment (optional)
        category_worksheet = os.getenv("EXPORT_WORKSHEET_CATEGORIES") or DEFAULT_WORKSHEET_CATEGORIES
        experiences_worksheet = os.getenv("EXPORT_WORKSHEET_EXPERIENCES") or DEFAULT_WORKSHEET_EXPERIENCES
        manuals_worksheet = os.getenv("EXPORT_WORKSHEET_MANUALS") or DEFAULT_WORKSHEET_MANUALS

        # Write to Google Sheets
        logger.info("Writing to Google Sheets...")
        sheets = SheetsClient(str(credentials_path))

        # Categories
        cat_rows = [CATEGORY_COLUMNS]
        for cat in categories:
            cat_rows.append(_row([cat.get(col) for col in CATEGORY_COLUMNS]))
        sheets.write_worksheet(spreadsheet_id, category_worksheet, cat_rows)
        logger.info("✓ Wrote %s categories to %s", len(categories), category_worksheet)

        # Experiences
        exp_rows = [EXPERIENCE_COLUMNS]
        for exp in experiences:
            exp_rows.append(_row([exp.get(col) for col in EXPERIENCE_COLUMNS]))
        sheets.write_worksheet(spreadsheet_id, experiences_worksheet, exp_rows)
        logger.info("✓ Wrote %s experiences to %s", len(experiences), experiences_worksheet)

        # Manuals
        man_rows = [MANUAL_COLUMNS]
        for man in manuals:
            man_rows.append(_row([man.get(col) for col in MANUAL_COLUMNS]))
        sheets.write_worksheet(spreadsheet_id, manuals_worksheet, man_rows)
        logger.info("✓ Wrote %s manuals to %s", len(manuals), manuals_worksheet)

        logger.info("✓ Export completed successfully!")

    except (APIOperationError, APIConnectionError) as exc:
        logger.error("✗ API operation failed: %s", exc)
        sys.exit(1)
    except Exception as exc:
        logger.error(f"✗ Export failed: {exc}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
