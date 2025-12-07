# Team Curation Walkthrough - Sample Scenario

A step-by-step guide showing how Alice, Bob, and their team leader Carlos use CHL's semi-automatic curation workflow to merge, deduplicate, and publish their collective knowledge.

---

## Cast of Characters

- **Alice** (`alice`) - Frontend developer, has been collecting UI/CSS debugging tips
- **Bob** (`bob`) - Backend developer, has experiences with Docker, Git, and database errors
- **Carlos** (`carlos`) - Team leader, curator responsible for merging and deduplicating team knowledge

---

## Scenario Overview

Alice and Bob have each been using CHL locally for 2 weeks. They've accumulated ~50 experiences each. Some overlap (both hit the same Git merge conflicts), some are unique. Carlos needs to:

1. Collect their exports
2. Merge them into a curation database
3. Find and resolve duplicates
4. Publish the approved knowledge base
5. Have the team sync to the new baseline

**Important Team Convention**: Category codes (3-letter abbreviations like "DVT", "PGS", "ADG") must be unique and agreed upon by the entire team. All team members should use the same category codes with identical names and descriptions. The merge script uses category codes as unique keys - if conflicts are detected (same code, different name/description), the merge will fail and the team must align on category definitions first.

---

## Phase 1: Export (Alice & Bob)

### Alice's Steps

```bash
# 1. Alice starts her CHL instance (GPU mode recommended for later phases)
# Navigate to the project root directory where curated-heuristic-loop exists
cd ~/Documents/program/curated-heuristic-loop
source .venv-nvidia/bin/activate
python -m src.api.server

# 2. Opens browser: http://localhost:8000
# 3. Navigates to: Operations page
# 4. Clicks: "Export CSV" button
# 5. Browser downloads: alice.export.zip (2.3 MB)
# 6. Sends file to Carlos via Slack
```

**What's in `alice.export.zip`?**
```
alice/
  ├── categories.csv       (1 row: DEV_TOOLING)
  ├── experiences.csv      (52 rows: Git, npm, CSS issues)
  └── manuals.csv          (3 rows: team SOPs)
```

### Bob's Steps

```bash
# Bob follows the same steps
# Downloads: bob.export.zip (1.8 MB)
# Sends to Carlos
```

**What's in `bob.export.zip`?**
```
bob/
  ├── categories.csv       (1 row: DEV_TOOLING)
  ├── experiences.csv      (48 rows: Docker, Git, Postgres issues)
  └── manuals.csv          (2 rows: Docker policies)
```

---

## Phase 2: Merge & Prepare (Carlos - Curator)

### Step 1: Collect Exports

```bash
# Carlos creates curation workspace
cd ~/Documents/program/curated-heuristic-loop
mkdir -p data/curation/members
cd data/curation/members

# Downloads alice.export.zip and bob.export.zip from Slack
# Unzips them
unzip alice.export.zip
unzip bob.export.zip

# Directory structure now:
# data/curation/members/
#   ├── alice/
#   │   ├── categories.csv
#   │   ├── experiences.csv
#   │   └── manuals.csv
#   └── bob/
#       ├── categories.csv
#       ├── experiences.csv
#       └── manuals.csv
```

### Step 2: Merge Exports

```bash
# Navigate to the project root directory where curated-heuristic-loop exists
cd ~/Documents/program/curated-heuristic-loop

# Merge all member exports into one dataset (uses defaults from scripts_config.yaml)
python scripts/curation/merge_exports.py

# Output:
# ✓ Merged 2 member exports (alice, bob)
# ✓ Categories: 1 unique (DVT - dev_tooling_common_errors)
#   - Validated: all members use same code/name/description
# ✓ Experiences: 100 total (52 from alice, 48 from bob)
#   - 3 ID collisions detected and resolved (suffix appended)
#   - Collision log: data/curation/merge_audit.csv
# ✓ Manuals: 5 total (3 from alice, 2 from bob)
# ✓ Output written to: data/curation/merged/
```

**ID Collision Example:**

Both Alice and Bob created an experience with ID `EXP-DVT-GIT-001` (Git merge conflict fix). The merge script detects this and renames:
- Alice's: `EXP-DVT-GIT-001` (kept, created earlier)
- Bob's: `EXP-DVT-GIT-001_bob` (suffix appended)

**Merge Audit Log** (`data/curation/merge_audit.csv`):
```csv
run_id,timestamp,user,input_files,output_file,pending_count,synced_count,collisions_appended_ids,schema_warnings,notes
mer-20250203-143022,2025-02-03T14:30:22Z,carlos,"alice,bob",merged,100,0,"EXP-DVT-GIT-001_bob,EXP-DVT-NPM-003_bob,EXP-DVT-CSS-012_alice",,
```

### Step 3: Initialize Curation Database

```bash
# Create a fresh curation database (uses defaults from scripts_config.yaml)
python scripts/curation/init_curation_db.py

# Output:
# ✓ Database initialized at: data/curation/chl_curation.db
# ✓ Schema version: 1.0
# ✓ Tables created: categories, experiences, category_manuals, embeddings, faiss_metadata
```

### Step 4: Import Merged Data

```bash
# Import merged CSVs into curation database (uses defaults from scripts_config.yaml)
python scripts/curation/import_to_curation_db.py

# Output:
# ✓ Imported 1 categories
# ✓ Imported 100 experiences
# ✓ Imported 5 manuals
# ✓ All entries marked as embedding_status='pending'
```

### Step 5: Build Embeddings & Index

```bash
# Build embeddings and FAISS index on curation database (uses defaults from scripts_config.yaml)
# This requires GPU mode
python scripts/curation/build_curation_index.py

# Output:
# Loading embedding model...
# ✓ Model loaded: nomic-ai/nomic-embed-text-v1.5-GGUF
# Processing experiences: 100/100 [████████████████████] 100%
# ✓ Embeddings generated for 100 experiences
# ✓ FAISS index built (100 vectors, 768 dimensions)
# ✓ Index saved to: data/curation/faiss_index/
# Processing manuals: 5/5 [████████████████████████] 100%
# ✓ Embeddings generated for 5 manuals
# ✓ Total time: 2m 15s
```

---

## Phase 3: Duplicate Detection & Review (Carlos)

### Step 6: Scan for Duplicates

```bash
# Run duplicate detection in table format (non-interactive preview)
python scripts/curation/find_pending_dups.py \
  --db-path data/curation/chl_curation.db \
  --compare-pending \
  --format table

# Output:
# Analyzing 100 pending experiences...
# Computing pairwise similarities (GPU)...
# ✓ Similarity matrix computed (4,950 pairs)
# ✓ Sparse graph built (top-50 neighbors per item)
#
# === Duplicate Detection Summary ===
# High-similarity pairs (≥0.92): 8 pairs → suggest merge
# Medium-similarity pairs (0.75-0.92): 12 pairs → related, keep separate
# Borderline pairs (0.55-0.75): 6 pairs → needs review
# Drift triads detected: 2 triads (A≈B≈0.88, B≈C≈0.87, A≈C≈0.65)
#
# Top 8 High-Similarity Pairs:
# ┌─────────────────────────┬─────────────────────────┬───────┬──────────────┐
# │ ID A                    │ ID B                    │ Score │ Signal       │
# ├─────────────────────────┼─────────────────────────┼───────┼──────────────┤
# │ EXP-DVT-GIT-001         │ EXP-DVT-GIT-001_bob     │ 0.94  │ embed+LLM    │
# │ EXP-DVT-NPM-003         │ EXP-DVT-NPM-003_bob     │ 0.93  │ embed+LLM    │
# │ EXP-DVT-DOCKER-012      │ EXP-DVT-DOCKER-013      │ 0.92  │ LLM          │
# │ EXP-DVT-CSS-007         │ EXP-DVT-CSS-008         │ 0.95  │ embed+LLM    │
# │ ...                     │ ...                     │ ...   │ ...          │
# └─────────────────────────┴─────────────────────────┴───────┴──────────────┘
#
# Recommendation: Start interactive review with --bucket high
```

### Step 7: Interactive Review (High Similarity)

```bash
# Review high-similarity pairs interactively
python scripts/curation/find_pending_dups.py \
  --db-path data/curation/chl_curation.db \
  --bucket high \
  --interactive

# Carlos is dropped into interactive mode:
```

**Interactive Session:**

```
=== Interactive Duplicate Review ===
Session: rev-20250203-144530
State file: .curation_state.json
Progress: 0/8 high-similarity pairs reviewed

Commands: merge | update | keep | reject | diff | split | quit

─────────────────────────────────────────────────────────────────
Pair 1/8 | Score: 0.94 (embed: 0.92, LLM: 0.96)

[A] EXP-DVT-GIT-001 (author: alice)
    Title: Git merge conflict in package.json
    Context: Git 2.39, macOS
    Playbook: When merging feature branches, package.json conflicts...

[B] EXP-DVT-GIT-001_bob (author: bob)
    Title: Resolving package.json merge conflicts
    Context: Git 2.40, Ubuntu 22.04
    Playbook: During git merge, package.json often has conflicts...

─────────────────────────────────────────────────────────────────
>
```

Carlos types `diff` to see detailed differences:

```
> diff

=== Unified Diff ===
Title:
- [A] Git merge conflict in package.json
+ [B] Resolving package.json merge conflicts

Playbook:
- [A] When merging feature branches, package.json conflicts arise.
-     Accept both changes and run `npm install` to verify.
+ [B] During git merge, package.json often has conflicts in dependencies.
+     Use `git checkout --ours package.json && npm install` to resolve.

Context:
- [A] Git 2.39, macOS
+ [B] Git 2.40, Ubuntu 22.04

Analysis: Core problem is identical (package.json merge conflicts).
Different solutions (manual merge vs --ours strategy).
Recommendation: MERGE - combine both strategies into one canonical entry.
```

Carlos decides to merge, choosing A as canonical:

```
> merge A

Merged: EXP-DVT-GIT-001 (canonical) ← EXP-DVT-GIT-001_bob
Action: EXP-DVT-GIT-001_bob marked as rejected, merge_with=EXP-DVT-GIT-001

Would you like to edit the canonical entry's playbook to include Bob's strategy? (y/n)
> y

[Editor opens with combined playbook]
# Carlos merges both approaches into one comprehensive playbook

✓ Canonical entry updated
✓ Decision logged to evaluation_log.csv

Progress: 1/8 pairs reviewed
─────────────────────────────────────────────────────────────────
Pair 2/8 | Score: 0.93 (embed: 0.91, LLM: 0.95)
...
```

Carlos continues reviewing all 8 high-similarity pairs:
- 6 pairs merged (chose canonical, marked duplicates as REJECTED)
- 2 pairs kept separate (different enough in context/solution)

### Step 8: Review Drift Triads

```
=== Drift Triad Review ===
Found 2 triads where A≈B, B≈C, but A≉C (potential drift)

Triad 1:
[A] EXP-DVT-DOCKER-020: Docker build fails with network timeout
[B] EXP-DVT-DOCKER-021: Dockerfile build slow on corporate network
[C] EXP-DVT-DOCKER-025: Docker push fails to private registry

Similarities:
  A-B: 0.88 (both about Docker build networking)
  B-C: 0.87 (both about Docker + network/registry)
  A-C: 0.65 (different problems: build timeout vs push auth)

Recommendation: Merge A+B (similar root cause), keep C separate (different phase)

> merge_ab

✓ A+B merged into EXP-DVT-DOCKER-020
✓ C kept separate
```

After reviewing all high-similarity and drift cases:

```
=== Review Session Complete ===
High bucket: 8 pairs reviewed
  - 6 merged
  - 2 kept separate

Drift triads: 2 reviewed
  - 2 resolved (merged pairs, kept outliers separate)

Next steps:
  - Review medium bucket (optional, 12 pairs)
  - Review borderline queue (6 pairs needing human judgment)
  - Export approved data

Save session? (y/n) > y
✓ Session saved to .curation_state.json
```

### Step 9: Review Borderline Cases (Optional)

```bash
# Carlos can optionally review borderline pairs (0.55-0.75 similarity)
python scripts/curation/find_pending_dups.py \
  --db-path data/curation/chl_curation.db \
  --bucket borderline \
  --interactive

# These are uncertain cases where human judgment is critical
# Carlos reviews and decides: keep separate or merge
```

---

## Phase 4: Export & Publish (Carlos)

### Step 10: Export Approved Data

```bash
# Export approved entries from curation DB
# This excludes REJECTED entries, includes only SYNCED/PENDING approved items
python scripts/curation/export_curated.py \
  --db-path data/curation/chl_curation.db \
  --output data/curation/approved

# Output:
# ✓ Exporting approved entries...
# ✓ Categories: 1
# ✓ Experiences: 94 (6 rejected duplicates excluded)
# ✓ Manuals: 5
# ✓ Exported to: data/curation/approved/
#   - categories.csv
#   - experiences.csv
#   - manuals.csv
```

### Step 11: Publish to Canonical Sheet

```bash
# Publish approved data to team's canonical Google Sheet
python scripts/curation/publish_to_canonical.py \
  --input data/curation/approved \
  --sheet-id 1sYfvvsN3AgoKQbfURa0ysgv_h_93u2_zEshCOMzPj2c

# Output:
# Connecting to Google Sheets...
# ✓ Sheet access confirmed
# Clearing existing worksheets...
# Writing Categories worksheet (1 rows)...
# Writing Experiences worksheet (94 rows)...
# Writing Manuals worksheet (5 rows)...
# ✓ Published successfully
# ✓ Sheet URL: https://docs.google.com/spreadsheets/d/1sYfvvsN3AgoKQbfURa0ysgv_h_93u2_zEshCOMzPj2c
#
# Team notification:
# "New canonical knowledge base published! 94 experiences, 5 manuals.
#  Please import via CHL Operations page to sync."
```

Carlos posts to team Slack:
```
📚 New CHL baseline published!
✓ Merged Alice + Bob's knowledge
✓ 94 experiences (6 duplicates removed)
✓ 5 team manuals
→ Everyone: please import from canonical sheet to sync
→ Sheet: https://docs.google.com/spreadsheets/d/1sYfvv...
```

---

## Phase 5: Team Sync (Alice, Bob, and everyone)

### Alice's Import Steps

```bash
# Alice opens CHL web UI
# Navigates to: Operations page
# Clicks: "Import from Google Sheet" button
# (Sheet ID is pre-configured in .env as IMPORT_SPREADSHEET_ID)

# UI shows progress:
# - Clearing local database...
# - Importing 1 categories...
# - Importing 94 experiences...
# - Importing 5 manuals...
# ✓ Import complete

# Rebuild index
# (Can be done via UI or CLI)
```

**CLI Alternative:**
```bash
python scripts/import_from_sheets.py --sheet-id 1sYfvvsN3AgoKQbfURa0ysgv_h_93u2_zEshCOMzPj2c
python scripts/ops/rebuild_index.py
```

### Bob's Import Steps

Bob follows the same steps as Alice.

### Result

- Alice, Bob, and Carlos now all have the **same** knowledge base
- Everyone has 94 experiences (duplicates removed)
- New experiences they create locally start with default status until curation process
- Next curation cycle: they export again → Carlos merges new PENDING items → repeat

---

## Summary Stats

**Before Curation:**
- Alice: 52 experiences
- Bob: 48 experiences
- Total: 100 experiences (with overlap)

**After Curation:**
- Team canonical: 94 experiences
- Duplicates removed: 6
- Quality: High (drift triads resolved, conflicts merged)

**Time Investment:**
- Alice export: 30 seconds
- Bob export: 30 seconds
- Carlos merge + review: ~45 minutes (first time, includes learning)
- Team import: 2 minutes each

---

## Key Files Generated

```
data/curation/
├── members/
│   ├── alice/
│   │   ├── categories.csv
│   │   ├── experiences.csv
│   │   └── manuals.csv
│   └── bob/
│       ├── categories.csv
│       ├── experiences.csv
│       └── manuals.csv
├── merged/
│   ├── categories.csv
│   ├── experiences.csv
│   └── manuals.csv
├── approved/
│   ├── categories.csv
│   ├── experiences.csv
│   └── manuals.csv
├── chl_curation.db           # Temporary curation database
├── faiss_index/              # Embeddings for similarity
├── merge_audit.csv           # ID collision log
├── evaluation_log.csv        # Carlos's review decisions
└── .curation_state.json      # Resume state for interactive review
```

---

## Next Cycle

Two weeks later, Alice and Bob have each added 20 new experiences. They export again, Carlos merges only the new pending items against the canonical baseline (now established), and the cycle repeats.

This workflow ensures:
- ✅ No duplicate knowledge
- ✅ Team stays in sync
- ✅ Quality maintained through human-in-the-loop review
- ✅ Minimal disruption to individual workflows
