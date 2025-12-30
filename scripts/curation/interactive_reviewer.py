#!/usr/bin/env python3
"""
Interactive review functionality for curation sessions.
"""

import getpass
from typing import Dict, List
from pathlib import Path
from collections import defaultdict
from datetime import datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from src.common.storage.schema import CategorySkill, CurationDecision, Experience


class InteractiveReviewer:
    def __init__(self, db_path: Path, state_file: Path, dry_run: bool = False):
        self.db_path = db_path
        self.state_file = state_file
        self.dry_run = dry_run
        self.engine = create_engine(f"sqlite:///{db_path}")
        self.Session = sessionmaker(bind=self.engine)

    def _ensure_decisions_table(self) -> None:
        CurationDecision.__table__.create(bind=self.engine, checkfirst=True)

    def _log_decision(
        self,
        session,
        entry_id: str,
        action: str,
        target_id: str | None,
        notes: str | None,
        user: str,
    ) -> None:
        decision = CurationDecision(
            entry_id=entry_id,
            action=action,
            target_id=target_id,
            notes=notes,
            user=user,
        )
        session.add(decision)

    def run_interactive_review(self, results: List[Dict]) -> List[Dict]:
        """Run interactive review session."""
        import getpass

        print("\n=== Interactive Duplicate Review ===")
        print("Commands:")
        print("  merge <anchor_id> [or merge <pending_id> <anchor_id>] - mark pending as duplicate of anchor")
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
        from scripts.curation.common.state_manager import StateManager
        state = StateManager.load_state(self.state_file)
        user = getpass.getuser() if hasattr(getpass, 'getuser') else 'unknown'
        if state is None:
            state = StateManager.create_initial_state(self.db_path, 'all', user)
        else:
            state['user'] = user  # Update user if changed

        session = self.Session()
        save_state = True
        if not self.dry_run:
            self._ensure_decisions_table()

        try:
            # Group results by pending_id for easier navigation
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
                    status_marker = " (!)" if self.dry_run else ""
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
                        save_state = False
                        return state['decisions']
                    elif cmd_input == 'skip':
                        state['last_offset'] = current_idx
                        from scripts.curation.common.state_manager import StateManager
                        StateManager.save_state(state, self.state_file, self.dry_run)
                        save_state = False
                        print("Progress saved. Exiting...")
                        return state['decisions']

                    parts = cmd_input.split()
                    cmd = parts[0].lower() if parts else ''

                    if cmd == 'merge' and len(parts) >= 2:
                        if len(parts) >= 3 and parts[1] == pending_id:
                            anchor_id = parts[2]
                        else:
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
                            if not self.dry_run:
                                # Update the database: mark pending item as REJECTED (2) and link to anchor
                                pending_exp = session.query(Experience).filter(Experience.id == pending_id).first()
                                if pending_exp:
                                    pending_exp.sync_status = 2  # REJECTED
                                    if not hasattr(pending_exp, 'merge_with'):
                                        # If there's no merge_with column, we'd need to add it or use a different approach
                                        # For now, just add a note to curation_notes if it exists
                                        pass
                                    self._log_decision(
                                        session,
                                        entry_id=pending_id,
                                        action="merge",
                                        target_id=anchor_id,
                                        notes=f"Merged with {anchor_id}",
                                        user=user,
                                    )
                                    session.commit()
                                    print(f"  ✓ Database updated: {pending_id} marked as rejected")
                                else:
                                    # Check if it's a manual instead
                                    pending_manual = session.query(CategorySkill).filter(CategorySkill.id == pending_id).first()
                                    if pending_manual:
                                        pending_manual.sync_status = 2  # REJECTED
                                        self._log_decision(
                                            session,
                                            entry_id=pending_id,
                                            action="merge",
                                            target_id=anchor_id,
                                            notes=f"Merged with {anchor_id}",
                                            user=user,
                                        )
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
                        if not self.dry_run:
                            # In this case we would just make a note or leave as pending
                            self._log_decision(
                                session,
                                entry_id=pending_id,
                                action="keep",
                                target_id=None,
                                notes=note,
                                user=user,
                            )
                            session.commit()
                            print(f"  ✓ Decision saved for {pending_id} (kept separate)")
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
                        if not self.dry_run:
                            # Update database to mark as rejected
                            pending_exp = session.query(Experience).filter(Experience.id == pending_id).first()
                            if pending_exp:
                                pending_exp.sync_status = 2  # REJECTED
                                self._log_decision(
                                    session,
                                    entry_id=pending_id,
                                    action="reject",
                                    target_id=None,
                                    notes=reason,
                                    user=user,
                                )
                                session.commit()
                                print(f"  ✓ Database updated: {pending_id} marked as rejected")
                            else:
                                pending_manual = session.query(CategorySkill).filter(CategorySkill.id == pending_id).first()
                                if pending_manual:
                                    pending_manual.sync_status = 2  # REJECTED
                                    self._log_decision(
                                        session,
                                        entry_id=pending_id,
                                        action="reject",
                                        target_id=None,
                                        notes=reason,
                                        user=user,
                                    )
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

                        if not self.dry_run:
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
                                self._log_decision(
                                    session,
                                    entry_id=pending_id,
                                    action="update",
                                    target_id=None,
                                    notes=note,
                                    user=user,
                                )
                                session.commit()
                                print(f"  ✓ Database updated: {pending_id}")
                            else:
                                pending_manual = session.query(CategorySkill).filter(CategorySkill.id == pending_id).first()
                                if pending_manual:
                                    if new_title:
                                        pending_manual.title = new_title
                                    if new_playbook:  # For manuals, this would update content
                                        pending_manual.content = new_playbook
                                    if new_context:
                                        pending_manual.summary = new_context
                                    pending_manual.embedding_status = 'pending'
                                    self._log_decision(
                                        session,
                                        entry_id=pending_id,
                                        action="update",
                                        target_id=None,
                                        notes=note,
                                        user=user,
                                    )
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

                        if not self.dry_run:
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
                                self._log_decision(
                                    session,
                                    entry_id=pending_id,
                                    action="split",
                                    target_id=new_id,
                                    notes=f"Split into {new_id}",
                                    user=user,
                                )
                                session.commit()
                                print(f"  ✓ Database updated: {new_id} created as split from {pending_id}")
                            else:
                                pending_manual = session.query(CategorySkill).filter(CategorySkill.id == pending_id).first()
                                if pending_manual:
                                    new_manual = CategorySkill(
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
                                    self._log_decision(
                                        session,
                                        entry_id=pending_id,
                                        action="split",
                                        target_id=new_id,
                                        notes=f"Split into {new_id}",
                                        user=user,
                                    )
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
                                pending_manual = session.query(CategorySkill).filter(CategorySkill.id == pending_id).first()
                                if pending_manual:
                                    pending_exp = pending_manual

                            if not anchor_exp:
                                anchor_manual = session.query(CategorySkill).filter(CategorySkill.id == anchor_id).first()
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
            from scripts.curation.common.state_manager import StateManager
            if save_state:
                StateManager.save_state(state, self.state_file, self.dry_run)

        return state['decisions']
