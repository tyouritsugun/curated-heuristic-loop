# Semi-Auto Curation (Phase 1–2) – Dup & Conflict Detection

## Overview

This plan supports both **team** and **individual** curation workflows through a unified pipeline. The key insight: whether you have 1 developer or 10 developers, the workflow is the same after merging exports.

**Scope constraints for the first cut:**
- Only **experiences** (manual volume is small).
- Only entries with `sync_status=PENDING` (local-only). Treat `sync_status!=PENDING` as the canonical/anchor set.

**Code touchpoints already in repo:**
- `src/common/storage/schema.py` — `Experience.sync_status` (default 1, used as the pending flag in docs).
- `src/api/services/search_service.py#L178` — `find_duplicates(...)` orchestrator.
- `src/api/cpu/search_provider.py#L150` and `src/api/gpu/search_provider.py` — duplicate search implementations (text and vector).
- `src/common/storage/repository.py` — session helpers for CRUD.

---

## Pipeline Architecture

```
Individual/Team Exports → [Merge] → Unified CSV → [Curation Pipeline] → [Publish] → Canonical Sheet
```

The pipeline consists of three phases:
1. **Pre-processing**: Merge exports (handles 1-N developers)
2. **Core curation**: Detect duplicates, score atomicity, resolve conflicts
3. **Post-processing**: Publish approved entries to canonical sheet

---

## Pre-Processing: Export Merge

**Script**: `scripts/merge_exports.py`

**Purpose**: Aggregate exports from 1-N team members into a single unified CSV for curation.

**Input**:
- 1-N export CSV files from team members (exported via existing `export_to_sheets.py`)
- Each export contains: synced baseline (from last import) + pending entries (new local work)

**Process**:
1. Load all input exports
2. Deduplicate synced baseline across exports (by ID)
   - All teammates share same baseline from last import
   - Keep only one copy of each synced entry
3. Collect all pending entries (keep all, with author provenance)
4. Handle ID collisions: If pending IDs collide, append author suffix (e.g., `EXP-123-alice`)
5. Concatenate: `deduplicated_baseline + all_pending → merged.csv`

**Output**: `merged.csv` with structure:
```
id, category_code, section, title, playbook, context,
sync_status, author, created_at, updated_at, ...
```

**Edge cases handled**:
- **Different baselines**: If teammates imported at different times, keeps union of all synced entries
- **Pending ID collisions**: Renames with author suffix to avoid conflicts
- **Modified synced entries**: Warns user, keeps latest by `updated_at`

**Usage examples**:
```bash
# Team workflow (3 developers)
python scripts/merge_exports.py \
  --inputs alice_export.csv bob_export.csv carol_export.csv \
  --output merged.csv

# Individual workflow (1 developer)
python scripts/merge_exports.py \
  --inputs alice_export.csv \
  --output merged.csv
```

---

## Core Curation Pipeline (operates on merged.csv)

**Script**: `scripts/find_pending_dups.py`

**Input**: `merged.csv` (from merge_exports.py)

1) **Candidate selection + atomicity scoring**
   - Introduce `SyncStatus` `IntEnum` (`PENDING=1, SYNCED=2, REMOTE=3, REJECTED=4`) and swap all magic `1` filters to `SyncStatus.PENDING`.
   - Load merged.csv and filter:
     - **Pending pool** (candidates): `sync_status == SyncStatus.PENDING`
     - **Anchor pool** (baseline): `sync_status != SyncStatus.PENDING`
   - Add `score_atomicity(experience)` heuristic (bullets>8, words>500, multiple headings) returning `score, flags, suggestion`. Store in the report; allow `--min-atomicity` / `--atomicity-below` to focus review.

2) **Duplicate search with scoped pools**
   - For each pending entry, call `find_duplicates()` against anchor pool to find similar synced entries
   - **Important for team workflow**: Use `--compare-pending` flag to also check pending-vs-pending
     - This catches cases where multiple teammates wrote similar entries independently
     - Scoped by category and recent window (e.g., last 30 days, group size ≤50) to avoid quadratic blowup
   - SQLite provider filters anchors to non-pending; FAISS builds/queries index from anchors only

3) **Duplication pass & bucketing**
   - Call `find_duplicates(..., threshold=0.60)` for all pending entries
   - Buckets: `>=0.92 → high`, `0.75–0.92 → medium`, `<0.75 → low/ignore`
   - Each match row carries: `pending_id, candidate_id, score, provider, atomicity, conflicts, recommended_action`
   - Group matches into duplicate clusters (1 pending + N similar entries from anchor/pending pools)

4) **Conflict/high-drift detection (richer signals, still lightweight)**
   - For each duplicate match, detect conflicts:
     - **Section mismatch**: pending.section != candidate.section
     - **High title similarity + low playbook overlap**: Similar titles but different content
     - **Regression**: pending newer but shorter by >30% (possible information loss)
     - **Pending extends canonical**: ≥2 shared bullets and ≤50% unique (pending adds to existing)
     - **Canonical outdated**: pending 20–100% longer and newer (evolved version)
   - Mark conflict types; feed into recommended action (e.g., `PENDING_EXTENDS_CANONICAL → update canonical`)

5) **Interactive review loop (actionable CLI)**
   - **Command**: `python scripts/find_pending_dups.py --input merged.csv --bucket high --interactive [--dry-run]`
   - For each duplicate cluster:
     - Show pending entry vs. all matches (canonical or other pending) side-by-side
     - Display: similarity score, conflicts, atomicity score, author (for team workflow)
     - **Actions available**:
       - `[m]` **Merge**: Mark pending as SYNCED (accept as duplicate, no new entry needed)
       - `[u]` **Update**: Update canonical with pending improvements, mark pending SYNCED
       - `[r]` **Reject**: Mark pending as REJECTED (true duplicate, discard)
       - `[k]` **Keep both**: Mark pending as SYNCED (related but distinct entries)
       - `[s]` **Split**: Flag pending as non-atomic, append to `needs_split.txt`, keep status PENDING
       - `[d]` **Diff**: Show bullet-level diff using difflib, return to action menu
       - `[q]` **Quit**: Save progress, exit (resume with `--resume` flag)
   - **Dry-run mode** (`--dry-run`): Simulate all actions without DB writes, print summary at end
   - **Non-interactive mode** (default): Output `--format table|json|csv` with counts per bucket and recommended actions
   - **Colorization**: Respect `NO_COLOR` env and `--no-color` flag; emit `[TAG]` prefixes in plain mode
   - **Progress indicators**: Use tqdm for atomicity scoring and duplicate search phases

6) **Metrics & tuning loop**
   - **Command**: `python scripts/find_pending_dups.py --input merged.csv --report-metrics > tuning_report.txt`
   - Aggregates manual spot-checks from `evaluation_log.csv`:
     - Schema: `timestamp, pending_id, candidate_id, score, bucket, user_action, was_correct, notes`
     - Tracks: precision by bucket, false positive rate, conflict distribution
   - Emits threshold suggestions (e.g., "raise high bucket to ≥0.94 if precision <90%")
   - Provides actionable insights on where to invest in better detection logic

7) **Iteration hooks & tests**
   - Add synthetic fixtures covering:
     - High-similar duplicate (score ≥0.92, same category)
     - Pending extends canonical (≥2 shared bullets)
     - Non-atomic entry (>8 bullets, >500 words)
     - Section mismatch (useful vs. harmful)
     - False positive (high title similarity, different intent)
     - Canonical subsumes pending (pending is subset)
   - Keep GPU provider optional; text provider remains default
   - Test both team (N>1) and individual (N=1) merge scenarios

---

## Post-Processing: Publish to Canonical

**Script**: `scripts/publish_to_canonical.py`

**Purpose**: Upload approved/synced entries from merged.csv to the canonical Published Google Sheet.

**Input**: `merged.csv` (after curation, with updated sync_status)

**Process**:
1. Load merged.csv
2. Filter to entries where `sync_status == SyncStatus.SYNCED`
3. Safety check: Run `find_duplicates` between approved and current Published Sheet (should be none)
4. Handle ID collisions: If approved entry ID exists in Published Sheet, assign new ID
5. Update timestamps: Set `synced_at` to current time
6. Upload to Published Google Sheet (append to existing canonical entries)
7. Log summary: "Added X entries, updated Y entries, total now Z"

**Output**: Updated Published Google Sheet (canonical baseline for next import cycle)

**Usage**:
```bash
# Preview what will be published
python scripts/publish_to_canonical.py \
  --input merged.csv \
  --sheet-id <PUBLISHED_SHEET_ID> \
  --dry-run

# Actually publish
python scripts/publish_to_canonical.py \
  --input merged.csv \
  --sheet-id <PUBLISHED_SHEET_ID>
```

---

## Workflow Guide

### For Team Curation (Multiple Developers)

**Who performs each step**:
- **Developers** (individual work): Steps 1
- **Curator** (team lead/designated reviewer): Steps 2-7
- **All teammates**: Step 8

**Steps**:

1. **Export pending work** (each developer)
   ```bash
   # Each team member exports their local database
   python scripts/export_to_sheets.py --output alice_export.csv
   ```
   **What this does**: Exports all entries (synced baseline + pending) to CSV
   **When to do this**: End of sprint, before retro/curation session

2. **Collect exports** (curator)
   ```bash
   # Gather all teammate exports into a shared folder
   mkdir -p team_exports/sprint_2025_01
   # Copy alice_export.csv, bob_export.csv, carol_export.csv to this folder
   ```

3. **Merge exports** (curator)
   ```bash
   python scripts/merge_exports.py \
     --inputs team_exports/sprint_2025_01/*.csv \
     --output merged.csv
   ```
   **What this does**:
   - Deduplicates synced baseline (all teammates share same baseline)
   - Collects all pending entries from all teammates
   - Outputs unified merged.csv for curation

   **Output example**: `Merged 3 exports: Synced (100), Pending (16), Total (116)`

4. **Run duplicate detection** (curator)
   ```bash
   python scripts/find_pending_dups.py \
     --input merged.csv \
     --compare-pending \
     --format table
   ```
   **What this does**:
   - Scores atomicity for all pending entries
   - Finds duplicates: pending vs. synced AND pending vs. pending (cross-team)
   - Buckets matches: high (≥0.92), medium (0.75-0.92), low (<0.75)

   **Why `--compare-pending`**: Catches cases where multiple teammates wrote similar entries

5. **Triage non-atomic entries** (curator)
   ```bash
   python scripts/find_pending_dups.py \
     --input merged.csv \
     --atomicity-below 0.6 \
     --suggest-splits
   ```
   **What this does**: Lists pending entries that should be split into multiple atomic entries
   **Output**: Suggestions like "Split EXP-123 into 3 entries: hover states, loading states, error states"

6. **Review duplicates interactively** (curator)
   ```bash
   # Preview first (dry-run)
   python scripts/find_pending_dups.py \
     --input merged.csv \
     --bucket high \
     --interactive \
     --dry-run

   # Apply decisions
   python scripts/find_pending_dups.py \
     --input merged.csv \
     --bucket high \
     --interactive
   ```
   **What this does**:
   - Shows each duplicate cluster with pending + matches
   - Curator chooses action: merge, update, reject, keep both, split, skip
   - Updates sync_status in merged.csv based on decisions

   **Typical decisions**:
   - High similarity (0.95), no conflicts → **Merge** (mark as duplicate)
   - Pending extends canonical → **Update** canonical with improvements
   - Cross-team duplicates → **Pick best** or **Merge** into one

7. **Publish to canonical** (curator)
   ```bash
   # Preview changes
   python scripts/publish_to_canonical.py \
     --input merged.csv \
     --sheet-id <PUBLISHED_SHEET_ID> \
     --dry-run

   # Publish approved entries
   python scripts/publish_to_canonical.py \
     --input merged.csv \
     --sheet-id <PUBLISHED_SHEET_ID>
   ```
   **What this does**: Uploads all SYNCED entries to Published Google Sheet
   **Result**: Canonical sheet now has 112 entries (was 100, added 12 approved)

8. **Team import new baseline** (all teammates)
   ```bash
   # WARNING: This wipes local database and replaces with canonical
   python scripts/import_from_sheets.py --sheet-id <PUBLISHED_SHEET_ID>
   python scripts/rebuild_index.py
   ```
   **What this does**:
   - Downloads Published Sheet
   - Wipes local SQLite database
   - Imports canonical entries (all marked as SYNCED)
   - Rebuilds FAISS index for semantic search

   **Important**: Any pending work NOT approved in step 6-7 is lost. Export first if needed!

---

### For Individual Curation (Single Developer)

**Steps**:

1. **Export your work**
   ```bash
   python scripts/export_to_sheets.py --output my_export.csv
   ```

2. **Merge (single file)**
   ```bash
   python scripts/merge_exports.py \
     --inputs my_export.csv \
     --output merged.csv
   ```
   **Note**: Even for single developer, run merge to create unified format

3. **Run duplicate detection**
   ```bash
   python scripts/find_pending_dups.py \
     --input merged.csv \
     --format table
   ```
   **Note**: Skip `--compare-pending` (only one developer, no cross-team dups)

4. **Triage non-atomic entries**
   ```bash
   python scripts/find_pending_dups.py \
     --input merged.csv \
     --atomicity-below 0.6
   ```

5. **Review duplicates interactively**
   ```bash
   python scripts/find_pending_dups.py \
     --input merged.csv \
     --bucket high \
     --interactive
   ```

6. **Publish to canonical**
   ```bash
   python scripts/publish_to_canonical.py \
     --input merged.csv \
     --sheet-id <PUBLISHED_SHEET_ID>
   ```

7. **Import new baseline**
   ```bash
   python scripts/import_from_sheets.py --sheet-id <PUBLISHED_SHEET_ID>
   python scripts/rebuild_index.py
   ```

---

## Key User Concepts

### What is "Pending"?
- Entries with `sync_status=PENDING` are **local-only insights** not yet reviewed
- These are new experiences you wrote but haven't curated yet
- After curation, approved entries are marked `SYNCED` and published to canonical

### What is "Synced" (Canonical)?
- Entries with `sync_status=SYNCED` are **approved, published** entries
- All teammates share the same synced baseline after import
- These form the "anchor pool" for duplicate detection

### What is "Merged.csv"?
- Unified file containing: `synced baseline + all pending entries`
- For team: deduplicates baseline, collects pending from all teammates
- For individual: just your export reformatted to standard structure
- All curation tools operate on this file

### Why Run Merge Even for Individual?
- Creates consistent file format for curation pipeline
- Allows switching between individual/team workflows without code changes
- Simplifies testing and tool development

### What Happens to Rejected Entries?
- Marked `sync_status=REJECTED` in merged.csv
- Not published to canonical sheet
- Lost after next import (unless you backup merged.csv)
- Consider exporting rejected entries separately if you want to keep for reference

---

## Tuning & Optimization

### Threshold Tuning
After initial curation sessions, generate metrics:
```bash
python scripts/find_pending_dups.py \
  --input merged.csv \
  --report-metrics > tuning_report.txt
```

Review precision by bucket and adjust thresholds in code if needed:
- If high bucket has <85% precision: raise to ≥0.94
- If medium bucket is mostly false positives: narrow range or add conflict filters

### Atomicity Heuristics
If many false flags, tune scoring in `score_atomicity()`:
- Adjust bullet count threshold (currently >8)
- Adjust word count threshold (currently >500)
- Add domain-specific checks (e.g., code snippets exempt from bullet limit)

### Cross-Team Duplicate Scoping
If `--compare-pending` is too slow for large teams:
- Reduce time window (30 days → 14 days)
- Lower group size limit (50 → 25)
- Only compare within same category and section
