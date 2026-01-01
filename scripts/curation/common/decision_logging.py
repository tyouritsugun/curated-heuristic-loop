#!/usr/bin/env python3
"""
Decision logging functionality for curation sessions.
"""

import csv
import os
from typing import Dict, List
from pathlib import Path
from datetime import datetime, timezone


def write_evaluation_log(decisions: List[Dict], output_path: Path, dry_run: bool = False):
    """Write evaluation decisions to CSV log file."""
    if dry_run:
        print(f" (!) Dry run: would write {len(decisions)} decisions to evaluation log at: {output_path}")
        return

    # Ensure directory exists
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Fieldnames based on the legacy duplicate-detection doc
    fieldnames = [
        "timestamp", "user", "entry_id", "action",
        "target_id", "was_correct", "notes"
    ]

    # Check if file exists to determine if we need to write headers
    write_header = not output_path.exists()

    with open(output_path, 'a', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)

        if write_header:
            writer.writeheader()

        for decision in decisions:
            # Ensure all required fields are present
            row = {field: decision.get(field, "") for field in fieldnames}
            writer.writerow(row)
