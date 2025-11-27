# Semi‑Auto Curation (Phase 1–2) – High‑Level Requirements

Lean guide for developers to implement and run the duplicate/conflict detection pipeline. Keeps humans in the loop, minimizes surprises for team workflows.

---

## Purpose (what “success” looks like)
- Pending experiences are reviewed, deduped, and either published or rejected with provenance.
- Team exports merge without losing ownership or canonical truth.
- Curators can finish a session in one sitting or safely resume.

---

## Roles & Ownership
- **Developers**: export their local DB.
- **Curator**: runs merge, duplicate review, publish; owns decisions.
- **Fallback**: if curator unavailable, next available teammate with sheet access steps in.

---

## Preconditions
- Google Sheet ID + service account creds available.
- Local DB consistent with latest import (or export before wiping).
- CSV schema version matches tooling; reject/bail if columns missing.

---

## Quickstart (team of N)
```
# 1) Exports (each dev)
python scripts/export_to_sheets.py --output alice_export.csv

# 2) Merge
python scripts/merge_exports.py --inputs team_exports/*.csv --output merged.csv

# 3) Duplicate pass (pending vs synced + cross-team)
python scripts/find_pending_dups.py --input merged.csv --compare-pending --format table

# 4) Interactive decisions (start with high bucket)
python scripts/find_pending_dups.py --input merged.csv --bucket high --interactive

# 5) Publish approved
python scripts/publish_to_canonical.py --input merged.csv --sheet-id <PUBLISHED_SHEET_ID>

# 6) Team imports new baseline (after publish)
python scripts/import_from_sheets.py --sheet-id <PUBLISHED_SHEET_ID>
python scripts/rebuild_index.py
```

Solo developer: same steps, omit `--compare-pending`.

---

## Required Behaviors (dev-facing)
- Treat `sync_status=PENDING` as review candidates; everything else is anchor set.
- ID collisions in pending entries: retain original ID, append author suffix, and log to `merge_audit.csv`.
- Keep author, timestamps, and source file for every pending entry in `merged.csv`.
- `find_pending_dups` must:
  - Scope anchors to non-pending by default; add `--compare-pending` flag for team mode.
  - Bucket matches (`high >=0.92`, `medium 0.75–0.92`, `low <0.75` defaults).
  - Emit conflicts (section mismatch, title-same/content-diff, regression, extension).
  - Support resume: write state to `.curation_state.json` in the working dir.
- `score_atomicity`: store score + flags; allow `--atomicity-below <t>` filter.
- Non-interactive outputs: `table|json|csv`; interactive mode must support merge/update/reject/keep/split/diff/quit.
- Dry-run flag on any command that mutates files or sheets.

---

## Data Safety & Audit
- Preflight before merge: check required columns, count pending vs synced, fail loud on schema mismatch or BOM/encoding issues.
- Save `merge_audit.csv` summarizing: files merged, baseline dedupes, pending collisions, schema warnings.
- Interactive decisions append to `evaluation_log.csv` with timestamp, user, action, and was_correct (for later precision tracking).
- Import step must warn that local DB will be wiped; recommend taking an export backup first.

---

## Outputs & Exit Criteria
- `merged.csv` updated with statuses (`SYNCED`, `REJECTED`, `PENDING` remaining).
- `evaluation_log.csv` and `tuning_report.txt` (optional) exist.
- Publish dry-run shows zero unexpected duplicates vs canonical.
- Session “done” when no pending items remain or all remaining are intentionally left PENDING with notes.

---

## Scaling & Performance Guards
- For large teams: support `--recent-days`, `--group-size`, and category scoping to bound pending-vs-pending checks.
- Default to text provider; GPU/vector is optional but should work with same flags.

---

## Glossary
- **Pending**: local-only entries awaiting review.
- **Synced/Canonical**: approved entries; anchor set for dup detection.
- **Merged.csv**: unified file (baseline + all pending) that all curation steps operate on.

---

## Nice-to-Have (not blocking Phase 1)
- Printable curator checklist.
- Conflict-resolution tips (when to keep both vs update).
- Notification template to tell teammates when a new baseline is ready.
