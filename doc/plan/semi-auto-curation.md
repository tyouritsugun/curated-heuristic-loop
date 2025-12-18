# Semi‑Auto Curation (Phased Plan) – High‑Level Requirements

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
- **Reuse-first**: export APIs, similarity search, and reranking already exist in the codebase (API server). Developers should scan and reuse those modules instead of re-implementing them; extend only where gaps remain.

---

## Sync Status Enum (canonical)
- Stored as integer in DB (default=1 in schema). Adopt mapping in ADR and code comments: `0=PENDING`, `1=SYNCED`, `2=REJECTED` (verify/adjust if existing data differs).
- CLI and sheets columns must carry the integer, not string labels.
- Source of truth: `src/common/storage/schema.py` (Experience.sync_status, CategoryManual.sync_status). Document mapping in any script help text.
- Action item: run a quick DB check before implementation to confirm the actual default value in the current dataset; update mapping/tests if it differs.

---

## Preconditions
- **GPU mode required**: Semi-auto curation requires GPU-accelerated embeddings and reranking (Metal/CUDA). CPU-only mode is not supported for this workflow due to dependency on semantic similarity scoring.
- **Virtual environment**: Use the appropriate virtual environment for your hardware:
  - **Apple Silicon**: `.venv-apple` created with `requirements_apple.txt`
  - **NVIDIA GPU**: `.venv-nvidia` created with `requirements_nvidia.txt`
  - Activate with `source .venv-apple/bin/activate` or `source .venv-nvidia/bin/activate`
- Google Sheet ID + service account creds available.
- Local DB consistent with latest import (or export before wiping).
- CSV schema version matches tooling; reject/bail if columns missing.

---

## Quickstart (team mode)

### Export Phase (Each Team Member - Web UI)
```
1. Each team member opens CHL web UI → Operations page
2. Click "Export CSV" button (below "Export to Google Sheet")
3. Browser downloads {username}.export.zip containing:
   {username}/
     ├── categories.csv
     ├── experiences.csv
     └── manuals.csv
4. Send zip file to team curator (email/Slack/shared folder)
```

### Curation Phase (Team Curator - Command Line)
```bash
# Before running scripts, activate the appropriate virtual environment:
# For Apple Silicon: source .venv-apple/bin/activate
# For NVIDIA GPU: source .venv-nvidia/bin/activate

# 1) Collect exports
# Place all member zip files in data/curation/members/
# Unzip: alice.export.zip, bob.export.zip, etc.
unzip alice.export.zip -d data/curation/members/
unzip bob.export.zip -d data/curation/members/

# 2) Merge all member exports
python scripts/curation/merge_exports.py \
  --inputs data/curation/members/alice data/curation/members/bob \
  --output data/curation/merged

# 3) Initialize curation database (same schema as main chl.db)
python scripts/curation/init_curation_db.py --db-path data/curation/chl_curation.db

# 4) Import merged data to curation database
python scripts/curation/import_to_curation_db.py \
  --input data/curation/merged \
  --db-path data/curation/chl_curation.db

# 5) Build embeddings and FAISS index on curation DB
# Note: This step requires GPU mode (Metal/CUDA)
python scripts/curation/build_curation_index.py --db-path data/curation/chl_curation.db

# 6) Run duplicate detection (using curation DB embeddings)
# Note: Each duplicate pair appears twice by default (A→B and B→A)
# Add --deduplicate flag to show unique pairs only once
python scripts/curation/find_pending_dups.py \
  --db-path data/curation/chl_curation.db \
  --compare-pending \
  --format table

# 7) Interactive review (start with high similarity bucket)
python scripts/curation/find_pending_dups.py \
  --db-path data/curation/chl_curation.db \
  --bucket high \
  --interactive

# 8) Export approved data from curation DB
python scripts/curation/export_curated.py \
  --db-path data/curation/chl_curation.db \
  --output data/curation/approved

# 9) Publish to canonical Google Sheet
python scripts/curation/publish_to_canonical.py \
  --input data/curation/approved \
  --sheet-id <PUBLISHED_SHEET_ID>
```

### Sync Phase (All Team Members - Web UI or CLI)
```bash
# Option A: Web UI
# Navigate to Operations → Import from Google Sheet → Click "Import"

# Option B: Command line
python scripts/import_from_sheets.py --sheet-id <PUBLISHED_SHEET_ID>
python scripts/ops/rebuild_index.py
```

**Solo mode** (local-only or single export): export → import to curation DB → run with `--compare-pending` so pending items can dedup against each other → approve → import to main DB.

Notes:
- **Export format**: Web UI "Export CSV" button creates `{username}.export.zip` where username comes from `getpass.getuser()` (OS username). Zip contains a folder `{username}/` with 3 CSVs (categories.csv, experiences.csv, manuals.csv).
- **Curation database**: `chl_curation.db` is a temporary database with identical schema to `chl.db`, used for similarity computation and dedup workflow. It isolates the curation work from team members' local databases.
- **Merge behavior**: The merge script reads all member directories, merges each entity type separately (categories usually identical, experiences/manuals with ID collision handling), and outputs to `data/curation/merged/` with the same 3-file structure.
- **Embeddings required**: Curation workflow requires GPU mode for embedding and similarity computation (reuses existing embedding service, FAISS manager, and reranker).
- The scripts above are planned CLI wrappers around existing API capabilities (`get_author()`, `ImportService`, GPU embedding/rerank, `SheetsClient`). They need to be created.

---

## Required Behaviors (dev-facing)
- Treat `sync_status=PENDING` as review candidates; everything else is anchor set.
- ID collisions in pending entries: retain original ID, append author suffix, and log to `merge_audit.csv`.
- Keep author, timestamps, and source file for every pending entry in `merged.csv`.
- `find_pending_dups` must:
  - Scope anchors to non-pending by default; add `--compare-pending` flag for team mode.
  - Bucket matches iteratively: process `high >=0.92` first, then `medium 0.75–0.92`, with recomputation after each phase to account for merges.
  - Emit conflicts (section mismatch, title-same/content-diff, regression, extension).
  - Output structure: self-matches (diagonal) are automatically filtered out; symmetric pairs (A→B and B→A) appear twice by default; add `--deduplicate` flag to show each unique pair only once (recommended for reporting, not for interactive review).
  - FAISS integration: use IndexFlatIP similarity scores directly (already in [0,1] range); convert numpy.int64 indices to Python int for database queries.
  - Support resume: write state to `.curation_state.json` in the working dir (schema: {run_id, input_path, last_bucket, last_index, decisions[], version, timestamp, user, input_checksum}; discard with `--reset-state` if checksum/input mismatches).
  - Interactive mode commands: `merge` (pick canonical, mark others `merge_with`, log), `update` (edit title/playbook/context inline), `keep` (mark `keep_separate` + note), `reject` (set sync_status=2 with reason), `split` (duplicate entry into parent+suffix for separate decisions; suffix format `{original_id}_split_{YYYYMMDDHHMMSS}`), `diff` (unified diff of titles/playbooks), `quit` (save state and exit).
  - Support iterative workflow: after completing high-similarity merges, rebuild index and recompute similarities to identify new high-similarity pairs that may emerge from medium merges.
  - **Important**: When duplicates are confirmed via `merge` command, the duplicate entries are marked as `sync_status=2` (REJECTED) and will be excluded from the final export, effectively "removing" them from the canonical knowledge base.
- `score_atomicity`: deferred to Phase 2+; leave placeholder flag but no-op until spec is defined (definition TBD: measures whether an experience is single, minimal, non-compound).
- Non-interactive outputs: `table|json|csv|spreadsheet`.
- Dry-run flag on any command that mutates files or sheets; dry-run writes only sidecar files (suffix `.dryrun`) and prints planned changes.

---

## Data Safety & Audit
- Preflight before merge: check required columns, count pending vs synced, fail loud on schema mismatch or BOM/encoding issues. Required columns: `id`, `category_code`, `section`, `title`, `playbook`, `context`, `source`, `author`, `sync_status`, `created_at`, `updated_at` (plus `expected_action` when present in labeled sets).
- Save `merge_audit.csv` with columns: `run_id`, `timestamp`, `user`, `input_files`, `output_file`, `pending_count`, `synced_count`, `collisions_appended_ids`, `schema_warnings`, `notes`.
- Interactive decisions append to `evaluation_log.csv` with columns: `timestamp`, `user`, `entry_id`, `action` (merge/update/keep/reject/split), `target_id` (for merges), `was_correct` (nullable), `notes`.
- `input_checksum` in `.curation_state.json`: SHA256 of normalized `merged.csv` contents (sorted rows, trimmed whitespace) to catch order/whitespace drift.
- Import step must warn that local DB will be wiped; recommend taking an export backup first.

---

## Outputs & Exit Criteria
- `merged.csv` updated with statuses (`SYNCED`, `REJECTED`, `PENDING` remaining).
- `evaluation_log.csv` and `tuning_report.txt` (optional) exist.
  - `tuning_report.txt` (if emitted) should summarize: threshold distributions, embed vs LLM disagreement counts, cluster size histogram, borderline bucket volume, and drift triads surfaced.
- Publish dry-run shows zero unexpected duplicates vs canonical.
- Session “done” when no pending items remain or all remaining are intentionally left PENDING with notes.

---

## Success Metrics
- Pending queue reduced to 0 (or targeted % per session) with decisions logged in `evaluation_log.csv`.
- No duplicate IDs or high-sim collisions in publish dry-run.
- All mutations run with dry-run first in CI/preflight; zero schema mismatches.
- Precision/recall on labeled dup set tracked per release (add small gold set).

---

## Rollback & Recovery
- Keep `merge_audit.csv` and source exports; allow `merge_exports.py --restore <audit>` to rebuild merged.csv.
- Failed import/publish: re-import last good canonical sheet; keep `EXPORT_SPREADSHEET_ID` snapshot IDs in log.
- Corrupted `.curation_state.json`: auto-backup previous state file; allow `--reset-state`.
- Unpublish guidance: re-import previous canonical version and rerun rebuild_index; log rollback in `audit_log`.

---

## Scaling & Performance Guards
- For large teams: support `--recent-days`, `--group-size`, and category scoping to bound pending-vs-pending checks.
- Scale expectations: target <=5 min for 1k pending items on GPU; provide `--max-neighbors` to cap graph size (default=50, aligned with sparse-graph top-k). Define "large" as >5k pending or >10 exports in a batch.
- GPU memory management: monitor VRAM usage during batch similarity scoring; allow chunking for large categories.
- Noise handling definition: treat "noise" as low-similarity outliers or low-quality entries with sparse edges; Phase 4 should formalize thresholds and handling (drop, quarantine, or down-rank).

---

## Modes (keep flows distinct)
- **Team curation**: multi-export → merge → dedup (anchors = non-pending) → publish → team re-imports baseline.
- **Solo curation**: import baseline first, or run with `--compare-pending` so pending can dedup internally; publish optional.
- Provide separate quickstart snippets and defaults per mode in CLI help.

---

## Roadmap / Phases
- **Phase 0 – Test Data & Dual-Sheet Harness**: curate a reusable test set (dev-tooling category), two parallel spreadsheets to simulate two teammates; 10 manuals + 20 seed experiences inflated to ~100–150 variants; ground-truth labels + `expected_action`; build `POST /api/v1/similarity/batch` (pairs → scores) and define `merge_audit.csv` + `.curation_state.json` schemas.
- **Phase 1 – Plumbing & Safety**: schema checks, merge/export/import scripts, resume state, dry-run flags, bucketed duplicate finder (existing thresholds), integrate pairwise batch API into CLI. ✅ **Complete**
- **Phase 2 – Sparse Graph & Community Detection**: build sparse similarity graph from FAISS index, detect non-overlapping communities using graph clustering (`python-louvain` or `leidenalg`), rank communities by priority (similarity, density, size), and export structured community data for LLM processing. See [phase2-spec.md](./phase2-spec.md) for full specification.
- **Phase 3 – LLM-Powered Community Iteration**: fully automated overnight workflow that iterates on Phase 2 communities with auto-deduplication (≥0.98 similarity), LLM-powered community resolution, iterative refinement with convergence guarantees (max 10 iterations, 5% improvement threshold), and morning report generation. Target: unsupervised overnight runtime, ≥30% item reduction, minimal cost with local LLM option or Claude Code MCP.
- **Phase 4 – Polishing & Scale**: blocking before LLM for cost, richer UI for triage, metrics (precision/recall on labeled dup set), noise handling for huge categories.

---

## Phase 2 → 3 Contract & Thresholds
- Graphs/communities are **per-category only**; no cross-category edges.
- Similarity scores use a blended signal (default 0.7 embed / 0.3 rerank). Rerank scores are cached when available; pipeline falls back to embed-only if cache is empty.
- Canonical file names: `data/curation/neighbors.jsonl` (top-K cache), `data/curation/similarity_graph.pkl`, and `data/curation/communities.json` (Phase 3 expects these paths and keys).
- Thresholds live in `scripts/scripts_config.yaml` under `curation.thresholds`; CLI flags may override, but config is the audit source of truth.
- Manuals are out of scope for Phase 2 communities; curate manuals manually.

### Default threshold table (user-tunable)
- `edge_keep` = 0.72 (also default `community_detect`)
- `community_detect` = 0.72 (Louvain/Leiden min edge weight)
- `auto_dedup` = 0.98 (Phase 3 merge-without-review)
- `high_bucket` = 0.92 (interactive high bucket)
- `medium_bucket` = 0.75 (interactive medium bucket)
- `low_bucket` = 0.55 (informational/preview)

Example override (config-first):
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

---

## Phase 0 Test Data & Dual-Sheet Setup
- Category choice: **Developer Tooling – Common Errors & Fixes** (Git/npm/pip/Docker/VS Code/HTTP errors). Rich public examples, easy paraphrase, embedding-friendly.
- Manuals: ~10 short SOP/policy docs (branching, SSH keys, node version policy, Docker build hygiene, lint/format standards, secrets handling, release checklist, incident triage). Include 2 near-duplicates to force a manual decision.
- Experiences: collect ~20 seed atomic issue→fix items; inflate to ~100–150 by paraphrasing, swapping package names/versions/paths, varying OS/tool versions, and crafting drift triads/borderline pairs.
- Two-team harness: split the set into two similar-but-not-identical sheets (e.g., A/B variants), export to two Google Sheets to mimic two teammate exports; use them in merge/dedup/similarity pipelines.
- Fields: `category_code`, `section`, `title`, `playbook`, `context`, `source`, `author`, `sync_status` (int), `expected_action` (ground truth labels for scoring). Keep IDs stable per variant family to test collision handling.
- Licensing: paraphrase/summarize public sources; avoid long verbatim stack traces; keep per-item text concise (<300 words).

---

## Workflow Alignment & Open Decisions (resolve in Phase 0 ADR)
- **Source of truth**: today Google Sheets is canonical via import_service (destructive import). Decide if CSV merge becomes a new bidirectional flow or remains a pre-publish staging step.
- **Exports**: existing `operations_service.export_to_sheets` writes Sheets; JSON export endpoint exists. Define CSV export format (schema version) and how it feeds merge.
- **Pairwise similarity API**: embedding search and rerank clients exist, but batch pairwise scoring does not. **Deliverable in Phase 0**: add `POST /api/v1/similarity/batch` (pairs → scores) and expose a CLI helper for test harness runs.
- **Manual items**: CategoryManual rows share sync_status; either include them in duplicate detection or explicitly mark them out-of-scope for Phase 1 (current plan: exclude manuals from auto similarity; curate manually).
- **Canonical sheet IDs**: clarify whether `<PUBLISHED_SHEET_ID>` equals existing `IMPORT_SPREADSHEET_ID` or a separate review sheet; document env vars in `.env.example`.

---

## Duplicate Detection via Sparse Similarity Graph
- Per category: iterate its experience items and compute both similarity signals (embedding score + LLM rerank) for candidate pairs. Manual items are excluded in Phase 1 (curated manually) unless ADR reverses this.
- Inputs: two similarity signals per pair (embedding, LLM rerank). Normalize to [0,1], set diagonal to 1.
- Blend: `S = w_embed * S_embed + w_llm * S_llm` (start 0.7/0.3). Option: `max(S_embed, S_llm)` if either signal is decisive.
- Sparsify (blocking after the fact): for each item keep top-k neighbors (k≈50 or 1 percent of n) or scores >= tau_keep (~0.70); set the rest to 0; symmetrize with max or mean.
- Dedup (strict): keep edges with score >= tau_eq (~0.90); connected components on this subgraph are near-duplicate sets. Choose a canonical representative per component and mark the others as duplicates.
- Similarity groups (looser): use the sparse graph with tau_cluster < tau_eq (start 0.75). Run Louvain/Leiden (community detection on weighted graphs) or DBSCAN/HDBSCAN with distance = 1 - score. Output = related-item clusters for curator review.
- Borderline queue: collect edges with score in [tau_review_low, tau_review_high] (e.g., 0.55–0.70) to surface uncertain cases first.
- Drift guard (A≈B, B≈C, A≠C):
  - Build a maximum spanning tree (MST) inside each cluster using edge weight = similarity.
  - Flag edges in the MST that fall below the relevant threshold (tau_eq for dedup, tau_cluster for grouping); cutting them prevents long weak chains.
  - Triangle test: if A–B and B–C are high but A–C is lower by a gap (e.g., ≥0.15), add the trio to a “drift review” list.
  - Review flow: surface drift triads first; suggest merge A+B, keep C separate, or split common atoms into one canonical experience plus variants.
- Provenance: keep which signal (embed, LLM, both) produced each edge; store sparse edge list as (i, j, score) for reuse.
- Future runs to save LLM cost: block before scoring (ANN/LSH or cheap hashes) to get candidate pairs; run LLM only inside blocks; build the sparse graph directly.

---

## LLM-Powered Community Iteration (Phase 3)

**Purpose:** Automated overnight curation using Phase 2 community data

**Workflow:**
1. **Input:** Load communities.json from Phase 2
2. **Auto-Dedup Phase:** Find and merge all pairs with similarity ≥0.98 (no LLM needed)
3. **Community Processing:** Iterate on communities by priority order:
   - LLM receives: community members, pairwise scores, item details (title, playbook, context)
   - LLM decides: `merge_all`, `merge_subset`, `keep_separate`, or `manual_review`
   - Execute decision and rebuild graph for next iteration
4. **Convergence Check:** Stop when progress <5% or max iterations reached
5. **Morning Report:** Summary of actions, remaining borderline cases for human review

**LLM Options:**
- **Claude API** via `anthropic` library (pay per use)
- **Local LLM** via `ollama` (qwen2.5:14b for 16GB+ VRAM, free)
- **Claude Code MCP** (recommended for fixed monthly pricing)

**Decision Types:**
- `merge_all`: All items are duplicates, choose canonical
- `merge_subset`: Some subgroups duplicate, others distinct
- `keep_separate`: Related but distinct items
- `manual_review`: Too ambiguous for LLM

**Convergence Guarantees:**
- Max iterations: 10 rounds hard limit
- Progress threshold: Must reduce communities by ≥5% per round
- Stuck detection: After 2 slow rounds, escalate remainder to manual review
- Monotonic reduction: Item count must decrease or stay same

**Outputs:**
- Morning report with iteration summary
- Manual review queue (borderline cases)
- Updated curation database with merge decisions applied

---

## Glossary
- **Pending**: local-only entries awaiting review.
- **Synced/Canonical**: approved entries; anchor set for dup detection.
- **Merged.csv**: unified file (baseline + all pending) that all curation steps operate on.
- **State transitions**: PENDING(0) → SYNCED(1) on approve/publish; PENDING(0) → REJECTED(2) on reject. SYNCED edits should create a new PENDING draft rather than mutating in place (enforce in CLI/API).

---

## Nice-to-Have (not blocking Phase 1)
- Printable curator checklist.
- Conflict-resolution tips (when to keep both vs update).
- Notification template to tell teammates when a new baseline is ready.
- Mermaid diagrams for data flow and state machine.
