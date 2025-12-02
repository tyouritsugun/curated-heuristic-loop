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

---

## Preconditions
- Google Sheet ID + service account creds available.
- Local DB consistent with latest import (or export before wiping).
- CSV schema version matches tooling; reject/bail if columns missing.

---

## Quickstart (team mode; scripts to be built as CLI wrappers over existing services)
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
python scripts/ops/rebuild_index.py
```

**Solo mode** (local-only or single export): same steps, but either import a baseline first or pass `--compare-pending` so pending items can dedup against each other when no synced anchors exist.

Notes:
- The scripts above are planned CLI wrappers around existing API capabilities (`OperationsService` export/import, GPU embedding/rerank). They need to be created or wired before use.
- Existing endpoints today: `POST /api/v1/entries/export` (JSON), import service wipes DB then ingests sheets. Decide if CSV export/merge is additive or replaces the sheets-as-source-of-truth pattern (see ADR todo).

---

## Required Behaviors (dev-facing)
- Treat `sync_status=PENDING` as review candidates; everything else is anchor set.
- ID collisions in pending entries: retain original ID, append author suffix, and log to `merge_audit.csv`.
- Keep author, timestamps, and source file for every pending entry in `merged.csv`.
- `find_pending_dups` must:
  - Scope anchors to non-pending by default; add `--compare-pending` flag for team mode.
  - Bucket matches (`high >=0.92`, `medium 0.75–0.92`, `low <0.75` defaults).
  - Emit conflicts (section mismatch, title-same/content-diff, regression, extension).
  - Support resume: write state to `.curation_state.json` in the working dir (schema: {run_id, input_path, last_bucket, last_index, decisions[], version}; discard with `--reset-state` if checksum/input mismatches).
- `score_atomicity`: deferred to Phase 2+; leave placeholder flag but no-op until spec is defined (definition TBD: measures whether an experience is single, minimal, non-compound).
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
- Default to text provider; GPU/vector is optional but should work with same flags.
- Scale expectations: target <=5 min for 1k pending items on GPU; provide CPU fallback warning and `--max-neighbors` to cap graph size. Define “large” as >5k pending or >10 exports in a batch.

---

## Modes (keep flows distinct)
- **Team curation**: multi-export → merge → dedup (anchors = non-pending) → publish → team re-imports baseline.
- **Solo curation**: import baseline first, or run with `--compare-pending` so pending can dedup internally; publish optional.
- Provide separate quickstart snippets and defaults per mode in CLI help.

---

## Roadmap / Phases
- **Phase 0 – Test Data & Dual-Sheet Harness**: curate a reusable test set (dev-tooling category), two parallel spreadsheets to simulate two teammates; 10 manuals + 20 seed experiences inflated to ~100 variants; export/import glue to load these sheets.
- **Phase 1 – Plumbing & Safety**: schema checks, merge/export/import scripts, `merge_audit.csv`, resume state, dry-run flags, bucketed duplicate finder (existing thresholds), pairwise similarity batch endpoint.
- **Phase 2 – Sparse Similarity Graph**: compute/embed + LLM signals per category, blend, sparsify (top-k / tau_keep), dedup via tau_eq components, similarity clusters via Louvain/DBSCAN, borderline queue, drift guards.
- **Phase 3 – Agentic Loop (optional pilot)**: worker + reviewer agents generate and validate actions; produce `agent_suggestions.jsonl` and `agent_reviews.jsonl`; feed curated queue to humans.
- **Phase 4 – Polishing & Scale**: blocking before LLM for cost, richer UI for triage, metrics (precision/recall on labeled dup set), noise handling for huge categories.

---

## Phase 0 Test Data & Dual-Sheet Setup
- Category choice: **Developer Tooling – Common Errors & Fixes** (Git/npm/pip/Docker/VS Code/HTTP errors). Rich public examples, easy paraphrase, embedding-friendly.
- Manuals: ~10 short SOP/policy docs (branching, SSH keys, node version policy, Docker build hygiene, lint/format standards, secrets handling, release checklist, incident triage).
- Experiences: collect ~20 atomic issue→fix items; inflate to ~100 by paraphrasing, swapping package names/versions/paths, varying OS/tool versions to create controlled near-duplicates.
- Two-team harness: split the set into two similar-but-not-identical sheets (e.g., A/B variants), export to two Google Sheets to mimic two teammate exports; use them in merge/dedup/similarity pipelines.
- Fields: `category_code`, `section`, `title`, `playbook`, `context`, `source`, `author`, `sync_status` (int). Keep IDs stable per variant family to test collision handling.
- Licensing: paraphrase/summarize public sources; avoid long verbatim stack traces; keep per-item text concise (<300 words).

---

## Workflow Alignment & Open Decisions (resolve in Phase 0 ADR)
- **Source of truth**: today Google Sheets is canonical via import_service (destructive import). Decide if CSV merge becomes a new bidirectional flow or remains a pre-publish staging step.
- **Exports**: existing `operations_service.export_to_sheets` writes Sheets; JSON export endpoint exists. Define CSV export format (schema version) and how it feeds merge.
- **Pairwise similarity API**: embedding search and rerank clients exist, but batch pairwise scoring does not. Add `POST /api/v1/similarity/batch` (pairs → scores) before Phase 1.
- **Manual items**: CategoryManual rows share sync_status; either include them in duplicate detection or explicitly mark them out-of-scope for Phase 1 (current plan: exclude manuals from auto similarity; curate manually).
- **Canonical sheet IDs**: clarify whether `<PUBLISHED_SHEET_ID>` equals existing `IMPORT_SPREADSHEET_ID` or a separate review sheet; document env vars in `.env.example`.
- **GPU/CPU fallback**: embeddings require GPU path; define CPU fallback (text overlap or smaller model) and flag behavior when embeddings unavailable.

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
