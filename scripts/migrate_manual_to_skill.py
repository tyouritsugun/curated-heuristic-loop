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

from src.common.config.config import Config
from src.common.storage.database import Database
from sqlalchemy import text
import shutil


def migrate():
    """Perform the manual → skill database migration."""
    print("=== Phase 2a: Manual → Skill Migration ===\n")

    # Initialize database
    print("Initializing database...")
    config = Config()
    db = Database(config.database_path, echo=False)
    db.init_database()
    print(f"✓ Database initialized at: {config.database_path}\n")

    # Step 1: Check if migration is needed
    print("Step 1: Checking database tables...")
    try:
        with db.session_scope() as session:
            # Check which tables exist
            tables = session.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()
            table_names = [t[0] for t in tables]

            has_manuals = 'category_manuals' in table_names
            has_skills = 'category_skills' in table_names

            print(f"  category_manuals exists: {has_manuals}")
            print(f"  category_skills exists: {has_skills}")

            if not has_manuals and has_skills:
                print("\n✓ Migration already complete - only category_skills table exists")
                db.close()
                return 0

            if has_manuals and has_skills:
                print("\n  Both tables exist - copying data and dropping old table...")
                # Check row counts
                manuals_count = session.execute(text("SELECT COUNT(*) FROM category_manuals")).scalar()
                skills_count = session.execute(text("SELECT COUNT(*) FROM category_skills")).scalar()
                print(f"  category_manuals has {manuals_count} rows")
                print(f"  category_skills has {skills_count} rows")

                if skills_count > 0:
                    print("\n⚠ Warning: category_skills already has data!")
                    print("  This suggests the migration may have partially run.")
                    print("  Please manually verify the data before proceeding.")
                    db.close()
                    return 1

                # Copy data from old table to new table
                print("\n  Copying data from category_manuals to category_skills...")
                session.execute(text("""
                    INSERT INTO category_skills
                    SELECT * FROM category_manuals
                """))
                session.commit()
                print(f"  ✓ Copied {manuals_count} rows")

                # Drop old table
                print("  Dropping old category_manuals table...")
                session.execute(text("DROP TABLE category_manuals"))
                session.commit()
                print("  ✓ Dropped category_manuals table")

            elif has_manuals and not has_skills:
                print("\n  Renaming table...")
                session.execute(text("ALTER TABLE category_manuals RENAME TO category_skills"))
                session.commit()
                print("  ✓ Renamed table: category_manuals → category_skills")

            else:
                print("\n✗ Neither table exists - cannot proceed")
                db.close()
                return 1

    except Exception as e:
        print(f"\n✗ Failed to migrate table: {e}")
        db.close()
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

    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(migrate())
