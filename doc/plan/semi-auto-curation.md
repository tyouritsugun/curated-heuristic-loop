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
python scripts/ops/rebuild_index.py
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

## Roadmap / Phases
- **Phase 0 – Plumbing & Safety**: schema checks, merge/export/import scripts, `merge_audit.csv`, resume state, dry-run flags, bucketed duplicate finder (existing thresholds).
- **Phase 1 – Sparse Similarity Graph**: compute/embed + LLM signals per category, blend, sparsify (top-k / tau_keep), dedup via tau_eq components, similarity clusters via Louvain/DBSCAN, borderline queue, drift guards.
- **Phase 2 – Agentic Loop (optional pilot)**: worker + reviewer agents generate and validate actions; produce `agent_suggestions.jsonl` and `agent_reviews.jsonl`; feed curated queue to humans.
- **Phase 3 – Polishing & Scale**: blocking before LLM for cost, richer UI for triage, metrics (precision/recall on labeled dup set), noise handling for huge categories.

---

## Duplicate Detection via Sparse Similarity Graph
- Per category: iterate its experience items and compute both similarity signals (embedding score + LLM rerank) for candidate pairs. Note that manual items are excluded, as it should be curated manually.
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

---

## Nice-to-Have (not blocking Phase 1)
- Printable curator checklist.
- Conflict-resolution tips (when to keep both vs update).
- Notification template to tell teammates when a new baseline is ready.
