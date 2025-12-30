#!/usr/bin/env python3
"""
Find potential duplicates in curation database using similarity search.

This script finds likely duplicates by computing similarity scores between
pending experiences and anchors (by default, synced entries), then buckets
them by similarity threshold. In solo mode, it compares pending vs pending.

Usage:
    # Find duplicates with table output (default)
    python scripts/curation/find_pending_dups.py --db-path data/curation/chl_curation.db
    
    # Find duplicates in solo mode (pending vs pending)
    python scripts/curation/find_pending_dups.py --db-path data/curation/chl_curation.db --compare-pending
    
    # Run interactive review session
    python scripts/curation/find_pending_dups.py --db-path data/curation/chl_curation.db --interactive --bucket high
    
    # Export to JSON
    python scripts/curation/find_pending_dups.py --db-path data/curation/chl_curation.db --format json
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Add project root to sys.path
project_root = Path(__file__).parent.parent  
sys.path.insert(0, str(project_root.parent))  

from sqlalchemy import create_engine, or_
from sqlalchemy.orm import sessionmaker
from src.common.storage.schema import Experience, CategoryManual, Embedding, FAISSMetadata
from src.common.storage.repository import EmbeddingRepository
from scripts._config_loader import load_scripts_config


def parse_args():
    # Load config to get defaults
    try:
        config, _ = load_scripts_config()
        curation_config = config.get("curation", {})
        default_db_path = curation_config.get("curation_db_path", "data/curation/chl_curation.db")
        default_state_file = curation_config.get("state_file", "data/curation/.curation_state.json")
    except Exception:
        # Fallback to hard-coded defaults if config loading fails
        default_db_path = "data/curation/chl_curation.db"
        default_state_file = "data/curation/.curation_state.json"

    parser = argparse.ArgumentParser(
        description="Find potential duplicates in curation database using similarity search"
    )
    parser.add_argument(
        "--db-path",
        default=default_db_path,
        help=f"Path to curation database (default: {default_db_path})",
    )
    parser.add_argument(
        "--format",
        choices=["table", "json", "csv"],
        default="table",
        help="Output format (default: table)",
    )
    parser.add_argument(
        "--bucket",
        choices=["high", "medium", "low", "all"],
        default="all",
        help="Similarity bucket to show (default: all)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Max number of top-K neighbors to search for each pending item (default: 50)",
    )
    parser.add_argument(
        "--compare-pending",
        action="store_true",
        help="Compare pending items against each other (for solo mode)",
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Run in interactive mode for duplicate review",
    )
    parser.add_argument(
        "--state-file",
        default=default_state_file,
        help=f"Path to resume state file (default: {default_state_file})",
    )
    parser.add_argument(
        "--reset-state",
        action="store_true",
        help="Reset resume state file",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Don't write any changes to database or state file",
    )

    # Bucket thresholds
    parser.add_argument(
        "--high-threshold",
        type=float,
        default=0.92,
        help="High similarity threshold (default: 0.92)",
    )
    parser.add_argument(
        "--medium-threshold",
        type=float,
        default=0.75,
        help="Medium similarity threshold (default: 0.75)",
    )
    parser.add_argument(
        "--low-threshold",
        type=float,
        default=0.55,
        help="Low similarity threshold for review queue (default: 0.55)",
    )

    return parser.parse_args()


def bucket_score(score: float, high_threshold: float, medium_threshold: float, low_threshold: float) -> str:
    """Classify similarity score into bucket."""
    if score >= high_threshold:
        return "high"
    elif score >= medium_threshold:
        return "medium"
    elif score >= low_threshold:
        return "low"
    else:
        return "none"


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


def save_state(state: Dict, state_file: Path, dry_run: bool = False):
    """Save resume state to JSON file."""
    if dry_run:
        print(f" (!) Dry run: would save state to: {state_file}")
        return
    
    state_file.parent.mkdir(parents=True, exist_ok=True)
    with open(state_file, 'w', encoding='utf-8') as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def find_duplicates(
    db_path: Path,
    compare_pending: bool,
    limit: int,
    high_threshold: float,
    medium_threshold: float,
    low_threshold: float,
    include_manuals: bool = False
) -> List[Dict]:
    """Find potential duplicates using FAISS similarity search."""
    # Import FAISS and embedding components
    from src.api.gpu.faiss_manager import FAISSIndexManager
    from src.common.config.config import get_config
    from src.api.gpu.embedding_client import EmbeddingClient
    
    print("Loading FAISS index and embeddings...")
    
    # Load configuration
    config = get_config()
    
    # Create database session
    engine = create_engine(f"sqlite:///{db_path}")
    Session = sessionmaker(bind=engine)
    session = Session()
    
    try:
        # Get pending entries to compare
        pending_experiences = session.query(Experience).filter(
            Experience.sync_status == 0  # PENDING
        ).all()
        
        pending_manuals = []
        if include_manuals:
            pending_manuals = session.query(CategoryManual).filter(
                CategoryManual.sync_status == 0  # PENDING
            ).all()
        
        all_pending = pending_experiences + pending_manuals
        
        if not all_pending:
            print("No pending entries found.")
            return []
        
        print(f"Found {len(pending_experiences)} pending experiences, {len(pending_manuals)} pending manuals")
        
        # Load FAISS index
        index_dir = db_path.parent / "faiss_index"
        if not index_dir.exists():
            print(f"❌ Error: FAISS index not found at {index_dir}")
            print("   Run build_curation_index.py first")
            sys.exit(1)
        
        faiss_manager = FAISSIndexManager(
            index_dir=str(index_dir),
            dimension=1024,  # Default dimension, will be auto-detected
            model_name=config.embedding_model,
            session=session,
        )
        
        # Load all embeddings to get mapping
        emb_repo = EmbeddingRepository(session)
        all_embeddings = emb_repo.get_all_by_model(config.embedding_model, entity_type=None)
        
        # Create mapping from entity_id to embedding_id and type
        entity_to_internal_id = {}
        for emb in all_embeddings:
            entity_to_internal_id[emb.entity_id] = emb.id
        
        # Get all pending entity IDs
        pending_ids = [e.id for e in all_pending]
        
        # Prepare results
        results = []
        
        # For each pending item, search for similar items
        for pending_item in all_pending:
            print(f"Searching for duplicates for {pending_item.__class__.__name__} {pending_item.id}...")
            
            # Get embedding for this pending item
            pending_emb = session.query(Embedding).filter(
                Embedding.entity_id == pending_item.id,
                Embedding.model_version == config.embedding_model
            ).first()
            
            if not pending_emb:
                print(f"  ⚠️  No embedding found for {pending_item.id}, skipping")
                continue
            
            # Convert embedding vector
            import numpy as np
            pending_vector = emb_repo.to_numpy(pending_emb)
            
            # Search in FAISS
            # If compare_pending is True, only compare to other pending items
            # Otherwise compare to all non-pending items (synced/rejected)
            if compare_pending:
                # For pending vs pending comparison, search with full index but filter results later
                distances, indices = faiss_manager.search(pending_vector.reshape(1, -1), limit * 2)  # Search more to have pending items
            else:
                # Search all and then filter based on status
                distances, indices = faiss_manager.search(pending_vector.reshape(1, -1), limit * 2)  # Search more to filter later
            
            # Get the results and filter based on sync_status if not comparing pending
            for i, (dist, idx) in enumerate(zip(distances[0], indices[0])):
                if i >= limit:  # Only take top limit results
                    break
                    
                # Get the entity_id from the FAISS metadata
                faiss_meta = session.query(FAISSMetadata).filter(FAISSMetadata.internal_id == idx).first()
                if not faiss_meta or faiss_meta.deleted:
                    continue
                
                anchor_id = faiss_meta.entity_id
                anchor_type = faiss_meta.entity_type
                
                # Skip if it's the same item
                if anchor_id == pending_item.id:
                    continue
                
                # Calculate similarity score (distance to similarity)
                # FAISS returns distances, convert to similarity (0-1 scale, higher is more similar)
                similarity_score = max(0.0, min(1.0, 1.0 - dist))  # Convert distance to similarity
                
                # If not comparing pending, filter out pending items
                if not compare_pending:
                    if anchor_type == "experience":
                        anchor_entity = session.query(Experience).filter(Experience.id == anchor_id).first()
                    else:
                        anchor_entity = session.query(CategoryManual).filter(CategoryManual.id == anchor_id).first()
                    
                    if not anchor_entity or anchor_entity.sync_status == 0:  # PENDING
                        continue
                
                # Classify into bucket
                bucket = bucket_score(similarity_score, high_threshold, medium_threshold, low_threshold)
                
                # Check for section mismatch if both are experiences
                section_mismatch = False
                if isinstance(pending_item, Experience) and anchor_type == "experience":
                    anchor_entity = session.query(Experience).filter(Experience.id == anchor_id).first()
                    if anchor_entity and anchor_entity.section != pending_item.section:
                        section_mismatch = True
                
                # Create result entry
                result = {
                    "pending_id": pending_item.id,
                    "pending_type": pending_item.__class__.__name__.lower(),
                    "anchor_id": anchor_id,
                    "anchor_type": anchor_type,
                    "score": similarity_score,
                    "bucket": bucket,
                    "category": pending_item.category_code,
                    "pending_title": pending_item.title[:50] + "..." if len(pending_item.title) > 50 else pending_item.title,
                    "anchor_title": anchor_entity.title[:50] + "..." if anchor_entity and len(anchor_entity.title) > 50 else (anchor_id if anchor_entity is None else anchor_entity.title),
                    "section_mismatch": section_mismatch,
                    "id_collision_flag": pending_item.id == anchor_id,  # This would be rare
                }
                
                results.append(result)
        
        print(f"Found {len(results)} potential duplicates")
        
        # Filter by bucket if needed
        if hasattr(args, 'bucket') and args.bucket != 'all':
            results = [r for r in results if r['bucket'] == args.bucket]
        
        return results
    
    except Exception as e:
        print(f"❌ Error during similarity search: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        session.close()


def format_results(results: List[Dict], format_type: str) -> str:
    """Format results in specified format."""
    if format_type == "table":
        # Table format
        if not results:
            return "No results found."
        
        header = f"{'Pending ID':<20} {'Anchor ID':<20} {'Score':<6} {'Bucket':<8} {'Category':<15} {'Section Mismatch':<8}"
        separator = "-" * len(header)
        
        table_rows = [header, separator]
        for result in results:
            row = f"{result['pending_id'][:19]:<20} {result['anchor_id'][:19]:<20} {result['score']:<6.3f} {result['bucket']:<8} {result['category']:<15} {str(result['section_mismatch']):<8}"
            table_rows.append(row)
        
        return "\n".join(table_rows)
    
    elif format_type == "json":
        return json.dumps(results, indent=2, ensure_ascii=False)
    
    elif format_type == "csv":
        if not results:
            return ""
        
        import io
        output = io.StringIO()
        import csv
        
        fieldnames = [
            'pending_id', 'pending_type', 'anchor_id', 'anchor_type', 
            'score', 'bucket', 'category', 'pending_title', 
            'anchor_title', 'section_mismatch', 'id_collision_flag'
        ]
        
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        for result in results:
            writer.writerow(result)
        
        return output.getvalue()


def interactive_review(db_path: Path, results: List[Dict], state_file: Path, dry_run: bool):
    """Run interactive review session."""
    import getpass

    print("\n=== Interactive Duplicate Review ===")
    print("Commands:")
    print("  merge <pending_id> <anchor_id> - mark pending as duplicate of anchor")
    print("  keep <pending_id> [note] - keep separate with optional note")
    print("  reject <pending_id> <reason> - reject entry with reason")
    print("  update <pending_id> - edit title/playbook/context")
    print("  split <pending_id> - duplicate entry for separate decisions")
    print("  diff <pending_id> <anchor_id> - show differences")
    print("  list - show all pending items with their top matches")
    print("  skip - save progress and exit")
    print("  quit - exit without saving")
    print()

    # Load existing state
    state = load_state(state_file)
    user = getpass.getuser() if hasattr(getpass, 'getuser') else 'unknown'
    if state is None:
        state = {
            "run_id": f"dup-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}",
            "db_path": str(db_path),
            "last_bucket": getattr(args, 'bucket', 'all'),
            "last_offset": 0,
            "decisions": [],
            "input_checksum": "unknown",  # Could add checksum calculation later
            "user": user,
            "version": "1.0",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    else:
        state['user'] = user  # Update user if changed

    # Create database session
    engine = create_engine(f"sqlite:///{db_path}")
    Session = sessionmaker(bind=engine)
    session = Session()

    try:
        # Group results by pending_id for easier navigation
        from collections import defaultdict
        results_by_pending = defaultdict(list)
        for result in results:
            results_by_pending[result['pending_id']].append(result)

        pending_ids = list(results_by_pending.keys())

        print(f"Total pending items to review: {len(pending_ids)}")

        # Show initial list command for all pending items
        print("\nTop matches for each pending item:")
        for i, pending_id in enumerate(pending_ids):
            print(f"  {i+1:2d}. {pending_id}")
            related_results = sorted(results_by_pending[pending_id], key=lambda x: x['score'], reverse=True)
            for j, result in enumerate(related_results[:3]):  # Show top 3 matches
                print(f"      {result['score']:.3f} | {result['bucket']} | {result['anchor_id']}")
        print()

        # Start review loop
        current_idx = state['last_offset']

        while current_idx < len(pending_ids):
            pending_id = pending_ids[current_idx]
            print(f"\n[{current_idx+1}/{len(pending_ids)}] Reviewing: {pending_id}")

            # Show all related results for this pending item
            related_results = sorted(results_by_pending[pending_id], key=lambda x: x['score'], reverse=True)
            for result in related_results:
                status_marker = " (!)" if dry_run else ""
                print(f"{status_marker}  Score: {result['score']:.3f} | Bucket: {result['bucket']} | Anchor: {result['anchor_id']}")

            # Get user command
            while True:
                cmd_input = input(f"\nCommand for {pending_id} (or 'help'): ").strip()

                if cmd_input == '':
                    continue
                elif cmd_input == 'help':
                    print("Available commands: merge, keep, reject, update, split, diff, list, skip, quit")
                    continue
                elif cmd_input == 'quit':
                    print("Exiting without saving...")
                    return
                elif cmd_input == 'skip':
                    state['last_offset'] = current_idx
                    save_state(state, state_file, dry_run)
                    print("Progress saved. Exiting...")
                    return

                parts = cmd_input.split()
                cmd = parts[0].lower() if parts else ''

                if cmd == 'merge' and len(parts) >= 3:
                    anchor_id = parts[1]
                    # Find the specific result for this pairing
                    target_result = None
                    for r in related_results:
                        if r['anchor_id'] == anchor_id:
                            target_result = r
                            break

                    if target_result:
                        print(f"Merging {pending_id} with {anchor_id}")
                        # Log the decision
                        decision = {
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "user": user,
                            "entry_id": pending_id,
                            "action": "merge",
                            "target_id": anchor_id,
                            "was_correct": None,  # Will be filled by human reviewer later
                            "notes": f"Merged with {anchor_id}",
                        }
                        state['decisions'].append(decision)
                        if not dry_run:
                            # Update the database: mark pending item as REJECTED (2) and link to anchor
                            pending_exp = session.query(Experience).filter(Experience.id == pending_id).first()
                            if pending_exp:
                                pending_exp.sync_status = 2  # REJECTED
                                if not hasattr(pending_exp, 'merge_with'):
                                    # If there's no merge_with column, we'd need to add it or use a different approach
                                    # For now, just add a note to curation_notes if it exists
                                    pass
                                session.commit()
                                print(f"  ✓ Database updated: {pending_id} marked as rejected")
                            else:
                                # Check if it's a manual instead
                                pending_manual = session.query(CategoryManual).filter(CategoryManual.id == pending_id).first()
                                if pending_manual:
                                    pending_manual.sync_status = 2  # REJECTED
                                    session.commit()
                                    print(f"  ✓ Database updated: {pending_id} marked as rejected")
                        else:
                            print(f"  (!) Would merge {pending_id} with {anchor_id} in database")
                        break
                    else:
                        print(f"Anchor ID {anchor_id} not found for this pending item")

                elif cmd == 'keep' and len(parts) >= 2:
                    note = ' '.join(parts[1:]) if len(parts) > 1 else "Kept separate manually"
                    print(f"Keeping {pending_id} separate: {note}")
                    decision = {
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "user": user,
                        "entry_id": pending_id,
                        "action": "keep",
                        "target_id": None,
                        "was_correct": None,
                        "notes": note,
                    }
                    state['decisions'].append(decision)
                    if not dry_run:
                        # In this case we would just make a note or leave as pending
                        print(f"  (!) Would mark {pending_id} to be kept separate in database")
                    else:
                        print(f"  (!) Would keep {pending_id} separate")
                    break

                elif cmd == 'reject' and len(parts) >= 3:
                    reason = ' '.join(parts[1:])
                    print(f"Rejecting {pending_id}: {reason}")
                    decision = {
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "user": user,
                        "entry_id": pending_id,
                        "action": "reject",
                        "target_id": None,
                        "was_correct": None,
                        "notes": reason,
                    }
                    state['decisions'].append(decision)
                    if not dry_run:
                        # Update database to mark as rejected
                        pending_exp = session.query(Experience).filter(Experience.id == pending_id).first()
                        if pending_exp:
                            pending_exp.sync_status = 2  # REJECTED
                            session.commit()
                            print(f"  ✓ Database updated: {pending_id} marked as rejected")
                        else:
                            pending_manual = session.query(CategoryManual).filter(CategoryManual.id == pending_id).first()
                            if pending_manual:
                                pending_manual.sync_status = 2  # REJECTED
                                session.commit()
                                print(f"  ✓ Database updated: {pending_id} marked as rejected")
                    else:
                        print(f"  (!) Would reject {pending_id} in database")
                    break

                elif cmd == 'update' and len(parts) >= 2:
                    print(f"Opening update editor for {pending_id}...")
                    # In real implementation, would open editor for title/playbook/context
                    # For now, just log the action
                    new_title = input(f"New title (current: {related_results[0].get('pending_title', 'Unknown')}) : ") or None
                    new_playbook = input("New playbook (optional): ") or None
                    new_context = input("New context (optional): ") or None

                    update_notes = []
                    if new_title:
                        update_notes.append(f"Title updated: {new_title}")
                    if new_playbook:
                        update_notes.append("Playbook updated")
                    if new_context:
                        update_notes.append("Context updated")

                    note = '; '.join(update_notes) if update_notes else "Entry updated"

                    decision = {
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "user": user,
                        "entry_id": pending_id,
                        "action": "update",
                        "target_id": None,
                        "was_correct": None,
                        "notes": note,
                    }
                    state['decisions'].append(decision)

                    if not dry_run:
                        # Update the database record
                        pending_exp = session.query(Experience).filter(Experience.id == pending_id).first()
                        if pending_exp:
                            if new_title:
                                pending_exp.title = new_title
                            if new_playbook:
                                pending_exp.playbook = new_playbook
                            if new_context:
                                pending_exp.context = new_context
                            # Reset embedding status to pending for re-embedding
                            pending_exp.embedding_status = 'pending'
                            session.commit()
                            print(f"  ✓ Database updated: {pending_id}")
                        else:
                            pending_manual = session.query(CategoryManual).filter(CategoryManual.id == pending_id).first()
                            if pending_manual:
                                if new_title:
                                    pending_manual.title = new_title
                                if new_playbook:  # For manuals, this would update content
                                    pending_manual.content = new_playbook
                                if new_context:
                                    pending_manual.summary = new_context
                                pending_manual.embedding_status = 'pending'
                                session.commit()
                                print(f"  ✓ Database updated: {pending_id}")
                    else:
                        print(f"  (!) Would update {pending_id} in database")
                    break

                elif cmd == 'split' and len(parts) >= 2:
                    # Generate a new ID with timestamp suffix
                    import time
                    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
                    new_id = f"{pending_id}_split_{timestamp}"
                    print(f"Splitting {pending_id} into new entry: {new_id}")

                    decision = {
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "user": user,
                        "entry_id": pending_id,
                        "action": "split",
                        "target_id": new_id,
                        "was_correct": None,
                        "notes": f"Split into {new_id}",
                    }
                    state['decisions'].append(decision)

                    if not dry_run:
                        # Create a new entry based on the existing one
                        pending_exp = session.query(Experience).filter(Experience.id == pending_id).first()
                        if pending_exp:
                            # Create new experience object with new ID
                            new_exp = Experience(
                                id=new_id,
                                category_code=pending_exp.category_code,
                                section=pending_exp.section,
                                title=pending_exp.title,
                                playbook=pending_exp.playbook,
                                context=pending_exp.context,
                                source=pending_exp.source,
                                sync_status=0,  # PENDING
                                author=pending_exp.author,
                                embedding_status='pending',
                                created_at=datetime.now(timezone.utc),
                                updated_at=datetime.now(timezone.utc),
                                synced_at=pending_exp.synced_at,
                                exported_at=pending_exp.exported_at,
                            )
                            session.add(new_exp)
                            session.commit()
                            print(f"  ✓ Database updated: {new_id} created as split from {pending_id}")
                        else:
                            pending_manual = session.query(CategoryManual).filter(CategoryManual.id == pending_id).first()
                            if pending_manual:
                                new_manual = CategoryManual(
                                    id=new_id,
                                    category_code=pending_manual.category_code,
                                    title=pending_manual.title,
                                    content=pending_manual.content,
                                    summary=pending_manual.summary,
                                    source=pending_manual.source,
                                    sync_status=0,  # PENDING
                                    author=pending_manual.author,
                                    embedding_status='pending',
                                    created_at=datetime.now(timezone.utc),
                                    updated_at=datetime.now(timezone.utc),
                                    synced_at=pending_manual.synced_at,
                                    exported_at=pending_manual.exported_at,
                                )
                                session.add(new_manual)
                                session.commit()
                                print(f"  ✓ Database updated: {new_id} created as split from {pending_id}")
                    else:
                        print(f"  (!) Would split {pending_id} to {new_id} in database")
                    break

                elif cmd == 'diff' and len(parts) >= 3:
                    anchor_id = parts[1]
                    # Find the specific result for this pairing
                    target_result = None
                    for r in related_results:
                        if r['anchor_id'] == anchor_id:
                            target_result = r
                            break

                    if target_result:
                        print(f"\nShowing differences between {pending_id} and {anchor_id}")

                        # Get both records from database
                        pending_exp = session.query(Experience).filter(Experience.id == pending_id).first()
                        anchor_exp = session.query(Experience).filter(Experience.id == anchor_id).first()

                        if not pending_exp:
                            pending_manual = session.query(CategoryManual).filter(CategoryManual.id == pending_id).first()
                            if pending_manual:
                                pending_exp = pending_manual

                        if not anchor_exp:
                            anchor_manual = session.query(CategoryManual).filter(CategoryManual.id == anchor_id).first()
                            if anchor_manual:
                                anchor_exp = anchor_manual

                        if pending_exp and anchor_exp:
                            print("\nTitle comparison:")
                            print(f"  PENDING:  {pending_exp.title}")
                            print(f"  ANCHOR:   {anchor_exp.title}")

                            # Use a simple text comparison for content
                            content_attr = 'playbook' if hasattr(pending_exp, 'playbook') else 'content'
                            print(f"\n{content_attr.title()} comparison:")
                            pending_content = getattr(pending_exp, content_attr, "")[:200] + ("..." if len(getattr(pending_exp, content_attr, "")) > 200 else "")
                            anchor_content = getattr(anchor_exp, content_attr, "")[:200] + ("..." if len(getattr(anchor_exp, content_attr, "")) > 200 else "")
                            print(f"  PENDING:  {pending_content}")
                            print(f"  ANCHOR:   {anchor_content}")

                            if hasattr(pending_exp, 'section') and hasattr(anchor_exp, 'section'):
                                print(f"\nSection: PENDING={pending_exp.section}, ANCHOR={anchor_exp.section}")
                        else:
                            print(f"One or both entries not found in database")
                    else:
                        print(f"Anchor ID {anchor_id} not found for this pending item")

                elif cmd == 'list':
                    print("\nAll pending items with top matches:")
                    for i, pid in enumerate(pending_ids):
                        print(f"  {i+1:2d}. {pid}")
                        related_results = sorted(results_by_pending[pid], key=lambda x: x['score'], reverse=True)
                        for j, result in enumerate(related_results[:3]):  # Show top 3 matches
                            print(f"      {result['score']:.3f} | {result['bucket']} | {result['anchor_id']}")

                else:
                    print("Invalid command or insufficient arguments. Type 'help' for available commands.")

            current_idx += 1

    finally:
        session.close()
        # Save state when done with current session
        state['last_offset'] = current_idx
        save_state(state, state_file, dry_run)


def write_evaluation_log(decisions: List[Dict], output_path: Path, dry_run: bool = False):
    """Write evaluation decisions to CSV log file."""
    import csv
    import os

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


def main():
    global args  # Make args globally accessible for the bucket check
    args = parse_args()

    # Validate database exists
    db_path = Path(args.db_path)
    if not db_path.exists():
        print(f"❌ Error: Database does not exist: {db_path}", file=sys.stderr)
        sys.exit(1)

    # Check if reset state is requested
    if args.reset_state:
        state_file = Path(args.state_file)
        if state_file.exists():
            state_file.unlink()
            print(f"✓ State file {state_file} removed")
        else:
            print(f"✓ State file {state_file} does not exist, nothing to reset")

    # Find potential duplicates
    results = find_duplicates(
        db_path,
        args.compare_pending,
        args.limit,
        args.high_threshold,
        args.medium_threshold,
        args.low_threshold
    )

    if args.interactive:
        # Load any existing state to get decisions that were previously made
        initial_state = load_state(Path(args.state_file))
        initial_decisions = initial_state.get('decisions', []) if initial_state else []

        # Run interactive review which will update the state
        interactive_review(db_path, results, Path(args.state_file), args.dry_run)

        # Now read the updated state to get all decisions
        updated_state = load_state(Path(args.state_file))
        all_decisions = updated_state.get('decisions', []) if updated_state else []

        # Write evaluation log
        evaluation_log_path = db_path.parent / "evaluation_log.csv"
        write_evaluation_log(all_decisions, evaluation_log_path, args.dry_run)

        print(f"\n✅ Interactive review complete! {len(all_decisions)} decisions logged to evaluation_log.csv")
    else:
        # Format and output results
        formatted_results = format_results(results, args.format)
        print(formatted_results)

    print(f"\n✅ Found {len(results)} potential duplicates")


if __name__ == "__main__":
    main()
