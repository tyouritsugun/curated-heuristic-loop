#!/usr/bin/env python3
"""
Publish approved CSVs to canonical Google Sheet.

This script uploads the approved CSV files (categories.csv, experiences.csv,
manuals.csv) to the canonical Google Sheet. Performs schema validation and
duplicate checking as safety measures.

Usage:
    # Dry run (recommended first)
    python scripts/curation/publish_to_canonical.py \\
        --input data/curation/approved \\
        --sheet-id <YOUR_SHEET_ID> \\
        --dry-run

    # Actual publish
    python scripts/curation/publish_to_canonical.py \\
        --input data/curation/approved \\
        --sheet-id <YOUR_SHEET_ID>
"""

import argparse
import csv
import hashlib
import sys
from pathlib import Path
from typing import Dict, List, Set

# Add project root to sys.path
project_root = Path(__file__).parent.parent  
sys.path.insert(0, str(project_root.parent))  

from src.common.storage.sheets_client import SheetsClient
import os
from scripts._config_loader import load_scripts_config


def parse_args():
    # Load config to get defaults
    try:
        config, _ = load_scripts_config()
        curation_config = config.get("curation", {})
        default_input_dir = curation_config.get("approved_output_dir", "data/curation/approved")
    except Exception:
        # Fallback to hard-coded default if config loading fails
        default_input_dir = "data/curation/approved"

    parser = argparse.ArgumentParser(
        description="Publish approved CSVs to canonical Google Sheet"
    )
    parser.add_argument(
        "--input",
        default=default_input_dir,
        help=f"Input directory containing approved CSVs (default: {default_input_dir})",
    )
    parser.add_argument(
        "--sheet-id",
        help="Google Sheet ID for canonical sheet (required)",
        required=True,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Don't write to Google Sheet, just validate and show plan",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show detailed output",
    )
    return parser.parse_args()


def read_csv(file_path: Path) -> List[Dict]:
    """Read CSV file and return list of dicts."""
    if not file_path.exists():
        return []

    with open(file_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return list(reader)


def validate_schema(data: List[Dict], expected_columns: Set[str], entity_type: str) -> List[str]:
    """Validate CSV schema against expected columns."""
    if not data:
        return []

    actual_columns = set(data[0].keys())
    missing_columns = expected_columns - actual_columns
    extra_columns = actual_columns - expected_columns
    
    warnings = []
    
    if missing_columns:
        warnings.append(f"{entity_type}: Missing required columns: {missing_columns}")
    
    if extra_columns:
        warnings.append(f"{entity_type}: Extra columns found: {extra_columns}")
    
    return warnings


def check_duplicates(data: List[Dict], id_column: str, entity_type: str) -> List[str]:
    """Check for duplicate IDs in data."""
    id_counts = {}
    for row in data:
        id_val = row.get(id_column, "")
        if id_val:
            id_counts[id_val] = id_counts.get(id_val, 0) + 1
    
    duplicate_ids = [id_val for id_val, count in id_counts.items() if count > 1]
    
    if duplicate_ids:
        return [f"{entity_type}: Found duplicate IDs: {duplicate_ids[:5]}{'...' if len(duplicate_ids) > 5 else ''}"]
    return []


def calculate_checksum(data: List[Dict]) -> str:
    """Calculate checksum of data for change detection."""
    content = str(sorted([tuple(sorted(row.items())) for row in data]))
    return hashlib.sha256(content.encode()).hexdigest()


def main():
    args = parse_args()

    input_dir = Path(args.input)

    # Validate input directory exists
    if not input_dir.exists():
        print(f"❌ Error: Input directory does not exist: {input_dir}", file=sys.stderr)
        sys.exit(1)

    # Read CSV files
    print(f"Reading approved CSVs from: {input_dir}")
    print()

    categories_data = read_csv(input_dir / "categories.csv")
    experiences_data = read_csv(input_dir / "experiences.csv") 
    manuals_data = read_csv(input_dir / "manuals.csv")

    print(f"  Categories: {len(categories_data)} rows")
    print(f"  Experiences: {len(experiences_data)} rows")
    print(f"  Manuals: {len(manuals_data)} rows")
    print()

    # Schema validation
    print("Validating schema...")
    schema_warnings = []
    
    # Expected columns based on schema.py
    expected_categories = {"code", "name", "description", "created_at"}
    expected_experiences = {
        "id", "category_code", "section", "title", "playbook", "context",
        "source", "author", "sync_status", "embedding_status",
        "created_at", "updated_at", "synced_at", "exported_at"
    }
    expected_manuals = {
        "id", "category_code", "title", "content", "summary",
        "source", "author", "sync_status", "embedding_status",
        "created_at", "updated_at", "synced_at", "exported_at"
    }
    
    schema_warnings.extend(validate_schema(categories_data, expected_categories, "Categories"))
    schema_warnings.extend(validate_schema(experiences_data, expected_experiences, "Experiences"))
    schema_warnings.extend(validate_schema(manuals_data, expected_manuals, "Manuals"))
    
    if schema_warnings:
        print("❌ Schema validation failed:")
        for warning in schema_warnings:
            print(f"  - {warning}")
        print()
        print("Please fix schema issues before publishing.")
        sys.exit(1)
    
    print("✓ Schema validation passed")
    print()

    # Duplicate checking
    print("Checking for duplicates...")
    duplicate_warnings = []
    duplicate_warnings.extend(check_duplicates(categories_data, "code", "Categories"))
    duplicate_warnings.extend(check_duplicates(experiences_data, "id", "Experiences"))
    duplicate_warnings.extend(check_duplicates(manuals_data, "id", "Manuals"))
    
    if duplicate_warnings:
        print("❌ Duplicate detection failed:")
        for warning in duplicate_warnings:
            print(f"  - {warning}")
        print()
        print("Please resolve duplicate issues before publishing.")
        sys.exit(1)
    
    print("✓ No duplicates found")
    print()

    # Calculate checksums for change detection
    categories_checksum = calculate_checksum(categories_data)
    experiences_checksum = calculate_checksum(experiences_data)
    manuals_checksum = calculate_checksum(manuals_data)
    
    print(f"Categories checksum: {categories_checksum[:12]}...")
    print(f"Experiences checksum: {experiences_checksum[:12]}...")
    print(f"Manuals checksum: {manuals_checksum[:12]}...")
    print()

    # If dry run, just show the plan
    if args.dry_run:
        print(" (!) DRY RUN MODE - No changes will be made to Google Sheet")
        print()
        print("Publish plan:")
        print(f"  Sheet ID: {args.sheet_id}")
        print(f"  Categories: {len(categories_data)} rows (checksum: {categories_checksum[:12]}...)")
        print(f"  Experiences: {len(experiences_data)} rows (checksum: {experiences_checksum[:12]}...)")
        print(f"  Manuals: {len(manuals_data)} rows (checksum: {manuals_checksum[:12]}...)")
        print()
        print("✅ Dry run completed - no changes made to Google Sheet")
        return

    # Initialize Sheets client and publish
    try:
        # Get credentials path from environment or use default
        credentials_path = os.getenv("GOOGLE_CREDENTIALS_PATH", "credentials.json")

        print(f"Initializing Google Sheets client with credentials: {credentials_path}")
        client = SheetsClient(credentials_path)

        print("Publishing to Google Sheet...")

        # Publish each worksheet
        if categories_data:
            print(f"  Uploading Categories ({len(categories_data)} rows)...")
            headers = list(categories_data[0].keys()) if categories_data else []
            rows = [[row.get(h, "") for h in headers] for row in categories_data]
            client.write_worksheet(args.sheet_id, "Categories", headers, rows)

        if experiences_data:
            print(f"  Uploading Experiences ({len(experiences_data)} rows)...")
            headers = list(experiences_data[0].keys()) if experiences_data else []
            rows = [[row.get(h, "") for h in headers] for row in experiences_data]
            client.write_worksheet(args.sheet_id, "Experiences", headers, rows)

        if manuals_data:
            print(f"  Uploading Manuals ({len(manuals_data)} rows)...")
            headers = list(manuals_data[0].keys()) if manuals_data else []
            rows = [[row.get(h, "") for h in headers] for row in manuals_data]
            client.write_worksheet(args.sheet_id, "Manuals", headers, rows)
        
        print()
        print(f"✅ Successfully published to Google Sheet: {args.sheet_id}")
        print()
        print("Next steps:")
        print("  1. Team members should re-import the canonical baseline")
        print("  2. Run: python scripts/import_from_sheets.py --sheet-id", args.sheet_id)
        print("  3. Rebuild index: python scripts/ops/rebuild_index.py")
        
    except Exception as e:
        print(f"❌ Error during publish: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()