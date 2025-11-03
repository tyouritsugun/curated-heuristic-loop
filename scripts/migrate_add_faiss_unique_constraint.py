#!/usr/bin/env python3
"""Migration script to add unique constraint to faiss_metadata table.

This migration adds a unique constraint on (entity_id, entity_type) to prevent
duplicate FAISS metadata entries for the same entity.

Usage:
    python scripts/migrate_add_faiss_unique_constraint.py [--dry-run]

Options:
    --dry-run    Show what would be done without making changes
"""
import sys
import argparse
import logging
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import text
from src.storage.database import get_database, init_database
from src.config import get_config

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def find_duplicate_faiss_metadata(session):
    """Find duplicate entries in faiss_metadata table.

    Returns:
        List of (entity_id, entity_type, count) tuples for duplicates
    """
    from sqlalchemy import func
    from src.storage.schema import FAISSMetadata

    duplicates = (
        session.query(
            FAISSMetadata.entity_id,
            FAISSMetadata.entity_type,
            func.count().label('count')
        )
        .group_by(FAISSMetadata.entity_id, FAISSMetadata.entity_type)
        .having(func.count() > 1)
        .all()
    )

    return duplicates


def clean_duplicate_faiss_metadata(session, dry_run=False):
    """Remove duplicate faiss_metadata entries, keeping the most recent one.

    Args:
        session: SQLAlchemy session
        dry_run: If True, only report what would be done

    Returns:
        Number of duplicate entries removed
    """
    from src.storage.schema import FAISSMetadata

    duplicates = find_duplicate_faiss_metadata(session)

    if not duplicates:
        logger.info("No duplicate faiss_metadata entries found")
        return 0

    logger.warning(f"Found {len(duplicates)} entities with duplicate faiss_metadata entries")

    total_removed = 0
    for entity_id, entity_type, count in duplicates:
        logger.info(f"  {entity_type} {entity_id}: {count} entries")

        # Get all entries for this entity, ordered by id (most recent last)
        entries = (
            session.query(FAISSMetadata)
            .filter(
                FAISSMetadata.entity_id == entity_id,
                FAISSMetadata.entity_type == entity_type
            )
            .order_by(FAISSMetadata.id.asc())
            .all()
        )

        # Keep the last one (most recent), delete the rest
        to_delete = entries[:-1]
        to_keep = entries[-1]

        if dry_run:
            logger.info(
                f"    Would delete {len(to_delete)} entries, keep ID {to_keep.id} "
                f"(faiss_internal_id={to_keep.faiss_internal_id})"
            )
        else:
            for entry in to_delete:
                logger.info(
                    f"    Deleting entry ID {entry.id} "
                    f"(faiss_internal_id={entry.faiss_internal_id})"
                )
                session.delete(entry)

            logger.info(
                f"    Keeping entry ID {to_keep.id} "
                f"(faiss_internal_id={to_keep.faiss_internal_id})"
            )

        total_removed += len(to_delete)

    if not dry_run:
        session.flush()
        logger.info(f"Removed {total_removed} duplicate entries")
    else:
        logger.info(f"Would remove {total_removed} duplicate entries")

    return total_removed


def add_unique_constraint(session, dry_run=False):
    """Add unique constraint to faiss_metadata table by recreating it.

    SQLite doesn't support adding constraints to existing tables, so we need to:
    1. Create a temporary table with the new schema
    2. Copy data from old table
    3. Drop old table
    4. Rename temp table

    Args:
        session: SQLAlchemy session
        dry_run: If True, only report what would be done

    Returns:
        True if constraint was added or already exists
    """
    if dry_run:
        logger.info("Would recreate faiss_metadata table with unique constraint")
        logger.info("Steps:")
        logger.info("  1. Create temp table with new schema")
        logger.info("  2. Copy existing data")
        logger.info("  3. Drop old table")
        logger.info("  4. Rename temp table")
        return True

    logger.info("Recreating faiss_metadata table with unique constraint...")

    try:
        # Execute raw SQL to recreate the table
        session.execute(text("""
            CREATE TABLE faiss_metadata_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_id VARCHAR NOT NULL,
                entity_type VARCHAR NOT NULL,
                faiss_internal_id INTEGER NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                deleted BOOLEAN NOT NULL DEFAULT 0,
                CONSTRAINT ck_faiss_entity_type CHECK (entity_type IN ('experience', 'manual')),
                CONSTRAINT uq_faiss_entity UNIQUE (entity_id, entity_type)
            )
        """))
        logger.info("  ✓ Created new table with constraint")

        # Copy data from old table
        session.execute(text("""
            INSERT INTO faiss_metadata_new
                (id, entity_id, entity_type, faiss_internal_id, created_at, deleted)
            SELECT id, entity_id, entity_type, faiss_internal_id, created_at, deleted
            FROM faiss_metadata
        """))
        logger.info("  ✓ Copied data from old table")

        # Drop old table
        session.execute(text("DROP TABLE faiss_metadata"))
        logger.info("  ✓ Dropped old table")

        # Rename new table
        session.execute(text("ALTER TABLE faiss_metadata_new RENAME TO faiss_metadata"))
        logger.info("  ✓ Renamed new table")

        session.flush()
        logger.info("✓ Successfully added unique constraint to faiss_metadata table")

        return True

    except Exception as e:
        logger.error(f"Failed to add constraint: {e}")
        raise


def main():
    parser = argparse.ArgumentParser(
        description="Migrate database to add unique constraint to faiss_metadata"
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Show what would be done without making changes'
    )
    args = parser.parse_args()

    try:
        # Load configuration
        config = get_config()
        logger.info(f"Using database: {config.database_path}")

        # Initialize database
        db = init_database(config.database_path, echo=False)

        with db.session_scope() as session:
            logger.info("Starting migration...")

            # Step 1: Find and clean duplicates
            logger.info("\n=== Step 1: Checking for duplicate faiss_metadata entries ===")
            removed = clean_duplicate_faiss_metadata(session, dry_run=args.dry_run)

            # Step 2: Add constraint (informational for SQLite)
            logger.info("\n=== Step 2: Adding unique constraint ===")
            add_unique_constraint(session, dry_run=args.dry_run)

            if args.dry_run:
                logger.info("\n=== DRY RUN COMPLETE ===")
                logger.info("No changes were made to the database")
                logger.info("Run without --dry-run to apply changes")
            else:
                logger.info("\n=== MIGRATION COMPLETE ===")
                logger.info(f"Cleaned up {removed} duplicate entries")
                logger.info("Note: For SQLite, the unique constraint will be enforced from now on")
                logger.info("Consider running: python scripts/rebuild_index.py")

    except Exception as e:
        logger.error(f"Migration failed: {e}", exc_info=True)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
