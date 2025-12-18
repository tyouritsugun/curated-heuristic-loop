# Team Curation Walkthrough - Sample Scenario

A step-by-step guide showing how Alice, Bob, and their team leader Carlos use CHL's semi-automatic curation workflow to merge, deduplicate, and publish their collective knowledge.

### Virtual Environment Setup

The scrpits are using same envoriments as API server which requires a dedicated virtual environment based on your hardware platform:

<details>
<summary><b>Choose Your Platform:</b></summary>

- **CPU-Only Mode:** Use `.venv-cpu` virtual environment with `requirements_cpu.txt`
- **Apple Silicon (Metal):** Use `.venv-apple` virtual environment with `requirements_apple.txt`
- **NVIDIA GPU:** Use `.venv-nvidia` virtual environment with `requirements_nvidia.txt`

**Activate the appropriate virtual environment before running any curation scripts:**

For CPU mode:
```bash
source .venv-cpu/bin/activate  # Linux/macOS
# Windows: .venv-cpu\Scripts\activate
```

For Apple Silicon:
```bash
source .venv-apple/bin/activate  # Linux/macOS
# Windows: .venv-apple\Scripts\activate
```

For NVIDIA GPU:
```bash
source .venv-nvidia/bin/activate  # Linux/macOS
# Windows: .venv-nvidia\Scripts\activate
```

**Important:** The semi-auto curation pipeline (Phase 1-4) requires GPU mode (Apple Silicon or NVIDIA) for semantic similarity computation. CPU-only mode does not support the embedding and similarity search operations needed for duplicate detection, however, it is possible for the cpu-only user to export the local database to share with the team.
</details>


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

1. Follow the API server setup and startup instructions in [README.md Step 4](README.md#step-4-start-api-server) to start CHL instance (GPU mode recommended for later phases)
2. Navigate to the Operations page in the web UI
3. Click the "Export CSV" button
4. Browser downloads: alice.export.zip (2.3 MB)
5. Sends file to Carlos

Example commands from README.md:
```bash
# Navigate to the project root directory where curated-heuristic-loop exists
cd ~/your/project/curated-heuristic-loop
source .venv-nvidia/bin/activate #or .venv-apple ...
python -m src.api.server

# Then open browser: http://localhost:8000
```

**What's in `alice.export.zip`?**
```
alice/
  ├── categories.csv       (1 row: DEV_TOOLING)
  ├── experiences.csv      (52 rows: Git, npm, CSS issues)
  └── manuals.csv          (3 rows: team SOPs)
```

### Bob's Steps

Bob follows the same export process as Alice (see [README.md Step 4](README.md#step-4-start-api-server)):
1. Start his CHL instance
2. Navigate to the Operations page in the web UI
3. Click the "Export CSV" button
4. Downloads: bob.export.zip (1.8 MB)
5. Sends to Carlos

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
cd ~/your/project/curated-heuristic-loop
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
cd ~/your/project/curated-heuristic-loop

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
# Uses default curation database path from scripts_config.yaml
python scripts/curation/find_pending_dups.py \
  --compare-pending \
  --format table

# Alternative (explicit path):
# python scripts/curation/find_pending_dups.py \
#   --db-path data/curation/chl_curation.db \
#   --compare-pending \
#   --format table

# Output:
# Analyzing 100 pending experiences...
# Computing pairwise similarities (GPU)...
# ✓ Similarity matrix computed (4,950 pairs)
# ✓ Sparse graph built (top-50 neighbors per item)
#
# === Duplicate Detection Summary ===
# High-similarity pairs (≥0.92): 8 pairs → suggest merge
# Medium-similarity pairs (0.75-0.92): 12 pairs → related, keep separate
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

# Note on output structure:
# - Each duplicate pair appears TWICE by default (A→B and B→A for symmetric pairs)
# - Self-matches (diagonal) are automatically filtered out
# - Use --deduplicate flag to show each unique pair only once:
#   python scripts/curation/find_pending_dups.py \
#     --compare-pending \
#     --format table \
#     --deduplicate
```

### Step 7: Iterative Curation Process (High Similarity First)

The curation process follows an iterative approach where high-similarity items are processed first, followed by medium-similarity items, with recomputation after each phase.

**Step 7a: Process High Similarity Items**

```bash
# Review high-similarity pairs interactively until none remain
# Uses default curation database path from scripts_config.yaml
python scripts/curation/find_pending_dups.py \
  --bucket high \
  --interactive

# Alternative (explicit path):
# python scripts/curation/find_pending_dups.py \
#   --db-path data/curation/chl_curation.db \
#   --bucket high \
#   --interactive

# Carlos continues until no more high-similarity pairs exist
```

**Step 7b: Process Medium Similarity Items**

After completing all high-similarity merges, process medium-similarity items:

```bash
# Review medium-similarity pairs interactively
# Uses default curation database path from scripts_config.yaml
python scripts/curation/find_pending_dups.py \
  --bucket medium \
  --interactive

# Alternative (explicit path):
# python scripts/curation/find_pending_dups.py \
#   --db-path data/curation/chl_curation.db \
#   --bucket medium \
#   --interactive

# Carlos continues until no more medium-similarity pairs exist
```

**Step 7c: Recompute Similarities (Iterative Step)**

After completing both high and medium similarity reviews, the similarity landscape may have changed due to merges. Carlos recomputes the entire similarity matrix to see if any new high-similarity pairs emerged from previous medium merges:

```bash
# Re-run duplicate detection to check for new high similarities
# Uses default curation database path from scripts_config.yaml
python scripts/curation/find_pending_dups.py \
  --compare-pending \
  --format table

# Alternative (explicit path):
# python scripts/curation/find_pending_dups.py \
#   --db-path data/curation/chl_curation.db \
#   --compare-pending \
#   --format table

# If new high-similarity pairs exist, return to Step 7a
# Continue this cycle until no more high or medium similarities exist
```

**Iterative Loop Logic:**
1. Process ALL high-similarity items until none remain
2. Process ALL medium-similarity items until none remain
3. Recompute and check for any new high-similarity items
4. If new high similarities found, return to step 1
5. If no high similarities but medium found, return to step 2
6. If neither exist, curation convergence is reached

**Example Interactive Session (High Similarity):**

```
=== Interactive Duplicate Review ===
Session: rev-20250203-144530
Bucket: high
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

Carlos continues processing all high-similarity items:
- 6 pairs merged (chose canonical, marked duplicates as REJECTED)
- 2 pairs kept separate (different enough in context/solution)

**Post-Merge Validation:**
After completing high-similarity processing, Carlos rebuilds the embeddings and FAISS index to reflect the merged items:

```bash
# Rebuild index after merges to reflect new canonical items
# Uses default curation database path from scripts_config.yaml
python scripts/curation/build_curation_index.py

# Alternative (explicit path):
# python scripts/curation/build_curation_index.py --db-path data/curation/chl_curation.db
```

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

### Step 9: Complete Iterative Curation Loop

Continue the iterative process:

1. Complete all high-similarity items
2. Complete all medium-similarity items
3. Rebuild embeddings and recompute similarities
4. Check for new high-similarity items (may emerge from previous medium merges)
5. If found, return to step 1; otherwise, curation is complete

```
=== Iterative Curation Complete ===
No more high or medium similarity pairs found after recomputation.
Curation convergence reached after 3 cycles:
  - Cycle 1: 6 high merges, 4 medium merges
  - Cycle 2: 2 new high merges (from medium merges in Cycle 1), 1 medium merge
  - Cycle 3: 0 high merges, 0 medium merges (converged)

Save session? (y/n) > y
✓ Session saved to .curation_state.json
```

---

## Alternative: Overnight Auto-Curation (Phase 2 + Phase 3)

For larger datasets or when Carlos doesn't have time for manual review, Phase 2 and Phase 3 together provide a fully automated overnight workflow. Phase 2 builds the community structure (see [phase2-spec.md](./plan/phase2-spec.md)), and Phase 3 iterates on those communities with LLM decisions.

### When to Use Overnight Auto-Curation

**Use overnight mode when:**
- Dataset has >200 pending items (would take hours to review manually)
- Carlos has limited time and prefers morning review of results
- Team wants to maximize automatic deduplication before manual review

**Use manual mode (Phase 3 from previous section) when:**
- Dataset is small (<100 items)
- Need fine-grained control over every decision
- Learning the curation process for the first time

### Carlos's Overnight Workflow

**Step 1: Build Communities (Phase 2) - 2 minutes:**

```bash
# Navigate to project root and activate environment
cd ~/your/project/curated-heuristic-loop
source .venv-apple/bin/activate  # or .venv-nvidia

# Build sparse graph and detect communities
python scripts/curation/build_communities.py \
  --db-path data/curation/chl_curation.db \
  --output data/curation/communities.json

# Output:
# ✓ Building sparse similarity graph...
# ✓ Querying FAISS top-50 neighbors for 100 items...
# ✓ Filtering edges below threshold 0.72...
# ✓ Graph built: 847 edges
# ✓ Running Louvain community detection...
# ✓ Found 15 communities (sizes: 3-12 items)
# ✓ Community data exported to: data/curation/communities.json
# ✓ Graph saved to: data/curation/similarity_graph.pkl
```

**Step 2: LLM Iteration (Phase 3) - Before bed (3 minutes):**

```bash
# Start overnight LLM-powered curation
python scripts/curation/overnight_curation.py \
  --communities data/curation/communities.json \
  --db-path data/curation/chl_curation.db \
  --max-iterations 10

# Output:
# ✓ Starting overnight auto-curation...
# ✓ Configuration:
#   - Auto-dedup threshold: 0.98 (merge without review)
#   - Max iterations: 10
#   - LLM mode: Claude Code MCP (or local LLM if configured)
# ✓ Loaded 15 communities from Phase 2
# ✓ Initial state: 100 pending items
#
# Iteration 1: Auto-dedup phase...
# [Carlos goes to bed, script runs overnight]
```

**Next morning (review results):**

```bash
# Generate morning report
python scripts/curation/overnight_curation.py --report-only

# Output:
# ========================================
# Overnight Curation Report
# ========================================
#
# Summary:
# ✓ Status: Converged after 4 iterations
# ✓ Runtime: 4h 23m
# ✓ Items before: 100
# ✓ Items after: 68
# ✓ Reduction: 32% (32 duplicates removed)
#
# Iteration Breakdown:
#
# Iteration 1 (Auto-dedup):
#   - Found 12 pairs with similarity ≥0.98
#   - Merged automatically: 12 duplicates removed
#   - Remaining items: 88
#
# Iteration 2 (Community processing):
#   - Communities detected: 15
#   - Highest priority community (5 items, avg sim=0.89): MERGED
#   - LLM decision: merge_all
#   - Items removed: 4
#   - Remaining items: 84
#
# Iteration 3 (Community processing):
#   - Communities detected: 12
#   - Highest priority community (8 items, avg sim=0.85): MERGED
#   - LLM decision: merge_subset (5 items merged, 3 kept separate)
#   - Items removed: 4
#   - Remaining items: 80
#
# Iteration 4 (Convergence):
#   - Communities detected: 8
#   - Progress: 33% reduction (below 5% threshold)
#   - Remaining communities: low priority (avg sim <0.78)
#   - Status: Converged
#
# Manual Review Queue:
# ✓ 8 borderline communities flagged for review
# ✓ Total items in queue: 24
# ✓ Recommended: Review with --interactive mode
#
# Next Steps:
# 1. Review flagged communities:
#    python scripts/curation/find_pending_dups.py --bucket medium --interactive
# 2. Export approved data:
#    python scripts/curation/export_curated.py --output data/curation/approved
# 3. Publish to canonical sheet
#
# Cost: $0 (local LLM) or ~$0.50 (Claude API)
# ========================================
```

**Review borderline cases (optional):**

If overnight curation flagged borderline communities for manual review:

```bash
# Review remaining medium-similarity items interactively
python scripts/curation/find_pending_dups.py \
  --bucket medium \
  --interactive

# Carlos reviews ~24 items (much less than original 100)
# Takes 10-15 minutes instead of 45 minutes
```

### Overnight Benefits

**Time savings:**
- Original manual review: 45 minutes for 100 items
- Overnight (Phase 2 + 3): 5 min setup + 10 min morning review = 15 minutes total
- Savings: 30 minutes (67% reduction)

**Scalability:**
- Manual review time grows linearly: 200 items = 90 minutes, 500 items = 3+ hours
- Overnight mode handles 1000+ items in same overnight window
- Morning review time stays constant (~15-20 minutes for borderline cases)

**Quality:**
- Automatic high-confidence merges (≥0.98 similarity) are very safe
- LLM community decisions provide reasoning and consistency
- Human still reviews borderline cases where LLM is uncertain
- Convergence guarantees prevent infinite loops or stuck states

### Configuration

Configuration in `scripts/scripts_config.yaml`:

```yaml
curation:
  # Phase 2: Graph construction
  min_similarity_threshold: 0.72  # Ignore edges below this
  top_k_neighbors: 50             # Keep top-k neighbors per item
  algorithm: "louvain"            # Community detection algorithm

  # Phase 3: LLM iteration
  auto_dedup_threshold: 0.98    # Auto-merge without review
  max_iterations: 10
  min_improvement_rate: 0.05    # 5% community reduction per round

  # LLM configuration (choose one)
  # Option 1: Claude Code MCP (recommended - fixed monthly cost)
  # Option 2: Claude API (pay per use, ~$0.02 per community)
  # Option 3: Local LLM (free, requires 16GB+ VRAM, use qwen2.5:14b)
```

---

## Phase 4: Export & Publish (Carlos)

### Step 10: Export Approved Data

```bash
# Export approved entries from curation DB
# Uses default curation database path from scripts_config.yaml
# This excludes REJECTED entries, includes only SYNCED/PENDING approved items
python scripts/curation/export_curated.py \
  --output data/curation/approved

# Alternative (explicit path):
# python scripts/curation/export_curated.py \
#   --db-path data/curation/chl_curation.db \
#   --output data/curation/approved

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

---

## Configuration Settings

### Phase 2 → 3 Contract (what Phase 3 expects)

- Graphs and communities are **per-category**; no cross-category edges.
- Similarity scores use an embed + rerank blend (default 0.7 / 0.3). Rerank cache is optional; if empty, the pipeline falls back to embed-only.
- Output files are fixed: `data/curation/similarity_graph.pkl` and `data/curation/communities.json` with keys `communities[]` and `metadata`.
- Manuals stay out of communities; they are curated manually.

### Curation Thresholds (defaults in `scripts/scripts_config.yaml`)

- `edge_keep` / `community_detect`: 0.72 (edges kept in sparse graph and fed to Louvain/Leiden)
- `auto_dedup`: 0.98 (Phase 3 merge-without-review)
- `high_bucket`: 0.92 (interactive high bucket)
- `medium_bucket`: 0.75 (interactive medium bucket)
- `low_bucket`: 0.55 (preview only)

Override example:

```yaml
curation:
  thresholds:
    edge_keep: 0.70
    community_detect: 0.70
    auto_dedup: 0.985
    high_bucket: 0.93
    medium_bucket: 0.76
    low_bucket: 0.55
```

### Output to Spreadsheet

For team collaboration, the curation results can be exported to a spreadsheet format where:
- High-similarity pairs are highlighted in green with "MERGE RECOMMENDED" and assigned a unique group ID (e.g., "GRP-101") to group all items that should be merged together
- Medium-similarity pairs are highlighted in yellow with "REVIEW NEEDED" and assigned similarity scores for sorting
- Duplicates already resolved are marked in gray with "RESOLVED" and original group information
- This allows users to filter by group ID to quickly identify all items that belong to the same merge group, or filter by recommendation status to focus on specific types of decisions

The iterative curation workflow follows these steps:
1. Focus on high-similarity items first (green) - process all until none remain
2. Then process medium-similarity items (yellow) - review and decide
3. After completing each phase, rebuild the similarity index to reflect merged items
4. Return to step 1 to check for new high-similarity pairs that may have emerged
5. Repeat until no more high or medium similarities remain
