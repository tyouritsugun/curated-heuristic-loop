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
python scripts/curation/build_curation_index.py --db-path data/curation/chl_curation.db

# 6) Run duplicate detection (using curation DB embeddings)
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
  - Bucket matches (`high >=0.92`, `medium 0.75–0.92`, `low <0.75` defaults).
  - Emit conflicts (section mismatch, title-same/content-diff, regression, extension).
  - Support resume: write state to `.curation_state.json` in the working dir (schema: {run_id, input_path, last_bucket, last_index, decisions[], version, timestamp, user, input_checksum}; discard with `--reset-state` if checksum/input mismatches).
  - Interactive mode commands: `merge` (pick canonical, mark others `merge_with`, log), `update` (edit title/playbook/context inline), `keep` (mark `keep_separate` + note), `reject` (set sync_status=2 with reason), `split` (duplicate entry into parent+suffix for separate decisions; suffix format `{original_id}_split_{YYYYMMDDHHMMSS}`), `diff` (unified diff of titles/playbooks), `quit` (save state and exit).
- `score_atomicity`: deferred to Phase 2+; leave placeholder flag but no-op until spec is defined (definition TBD: measures whether an experience is single, minimal, non-compound).
- Non-interactive outputs: `table|json|csv`.
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
- **Phase 1 – Plumbing & Safety**: schema checks, merge/export/import scripts, resume state, dry-run flags, bucketed duplicate finder (existing thresholds), integrate pairwise batch API into CLI.
- **Phase 2 – Sparse Similarity Graph**: compute/embed + LLM signals per category, blend, sparsify (top-k / tau_keep), dedup via tau_eq components, similarity clusters via Louvain/DBSCAN, borderline queue, drift guards.
- **Phase 3 – Agentic Loop (optional pilot)**: worker + reviewer agents generate and validate actions; produce `agent_suggestions.jsonl` and `agent_reviews.jsonl`; feed curated queue to humans.
- **Phase 4 – Polishing & Scale**: blocking before LLM for cost, richer UI for triage, metrics (precision/recall on labeled dup set), noise handling for huge categories.

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

## Agentic Curation Loop (Worker + Reviewer)
- Roles:
  - Worker agent: consumes manuals, drift triads, and similarity clusters; proposes concrete actions per item (merge targets, keep separate, retitle/scope, split facets, canonical choice).
  - Reviewer agent: tool-augmented checker that validates worker proposals (threshold sanity, consistency with provenance, MST/triangle drift checks), then produces a short rationale plus a pass/fail recommendation.
- Flow:
  1) Input packages: cluster bundle (nodes, edges, sims), drift list, and relevant manual snippets/checklists.
  2) Worker outputs for each cluster: (a) dedup sets with canonical pick, (b) related-but-keep sets with reasons, (c) proposed edits (titles/scope notes), (d) uncertainties.
  3) Reviewer runs validations via tools: recompute edge thresholds, confirm no A≈B≈C gaps were ignored, check provenance (embed vs LLM), ensure actions obey guardrails (e.g., don’t auto-merge below tau_eq). Reviewer emits accept/adjust/reject with minimal justification.
  4) Human curator receives: reviewer-approved suggestions + flagged disagreements/uncertainties ordered by risk (drift first, low-margin scores next).
- Guardrails:
  - Worker must cite which signal(s) support each suggested merge/split.
  - Reviewer fails any merge suggestion lacking an edge ≥ tau_eq or missing a direct edge in a proposed component.
  - Any cluster containing drift gaps auto-goes to human if worker and reviewer disagree.
- Outputs:
  - `agent_suggestions.jsonl`: worker proposals.
  - `agent_reviews.jsonl`: reviewer decisions + tool checks.
  - Final UI should highlight reviewer-approved actions and a separate queue for “needs human judgment.”

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
