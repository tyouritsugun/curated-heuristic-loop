#!/usr/bin/env python3
"""
State management functionality for curation sessions.
"""

import json
from typing import Dict, List, Optional
from pathlib import Path
from datetime import datetime, timezone


class StateManager:
    @staticmethod
    def load_state(state_file: Path) -> Optional[Dict]:
        """Load resume state from JSON file."""
        if not state_file.exists():
            return None

        try:
            with open(state_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"⚠️  Could not load state file {state_file}: {e}")
            return None

    @staticmethod
    def save_state(state: Dict, state_file: Path, dry_run: bool = False):
        """Save resume state to JSON file."""
        if dry_run:
            print(f" (!) Dry run: would save state to: {state_file}")
            return

        state_file.parent.mkdir(parents=True, exist_ok=True)
        with open(state_file, 'w', encoding='utf-8') as f:
            json.dump(state, f, indent=2, ensure_ascii=False)

    @staticmethod
    def create_initial_state(db_path: Path, bucket: str = 'all', user: str = 'unknown') -> Dict:
        """Create initial state for a new curation session."""
        return {
            "run_id": f"dup-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}",
            "db_path": str(db_path),
            "last_bucket": bucket,
            "last_offset": 0,
            "decisions": [],
            "input_checksum": "unknown",  # Could add checksum calculation later
            "user": user,
            "version": "1.0",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }