#!/usr/bin/env python3
"""Migrate category_manuals table to category_skills.

This script performs the Phase 2a database migration:
1. Renames the category_manuals table to category_skills
2. Clears the FAISS index (will be rebuilt on import)

IMPORTANT: Run this AFTER updating all code but BEFORE restarting the API server.
After running this script:
1. Restart API server
2. Import data from Google Sheets via UI
3. Verify skills appear correctly
"""
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.common.storage.database import get_session
from sqlalchemy import text
import shutil


def migrate():
    """Perform the manual → skill database migration."""
    print("=== Phase 2a: Manual → Skill Migration ===\n")

    # Step 1: Rename table
    print("Step 1: Renaming database table...")
    try:
        with get_session() as session:
            session.execute(text("ALTER TABLE category_manuals RENAME TO category_skills"))
            session.commit()
            print("✓ Renamed table: category_manuals → category_skills")
    except Exception as e:
        print(f"✗ Failed to rename table: {e}")
        print("\nRollback: The table may already be renamed, or the migration already ran.")
        print("Check the database schema and verify table name.")
        return 1

    # Step 2: Clear FAISS index
    print("\nStep 2: Clearing FAISS index...")
    faiss_dir = Path("data/faiss_index")
    if faiss_dir.exists():
        try:
            shutil.rmtree(faiss_dir)
            print("✓ Cleared FAISS index (will rebuild on import)")
        except Exception as e:
            print(f"✗ Failed to clear FAISS index: {e}")
            print("  You may need to manually delete data/faiss_index/")
            return 1
    else:
        print("  FAISS index directory not found (may be CPU mode or already cleared)")

    # Success
    print("\n" + "="*50)
    print("✓ Migration complete!")
    print("="*50)
    print("\nNext steps:")
    print("1. Restart API server")
    print("2. Import data from Google Sheets via UI (Operations → Import)")
    print("3. Verify skills appear correctly in the UI")
    print("4. Check embedding queue processes new skills")
    print("\nNote: MNL- ID prefix is preserved (legacy compatibility)")

    return 0


if __name__ == "__main__":
    sys.exit(migrate())
