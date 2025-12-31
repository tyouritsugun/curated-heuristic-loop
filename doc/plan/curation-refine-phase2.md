# Experience Curation Refinement - Phase 2 (Pipeline Reuse)

## Goal
Run Phase 2 using existing code paths: rebuild embeddings/FAISS, build communities, auto-dedup high similarity, and route high-bucket pairs to LLM decisions.

## What We Reuse (Existing Code)
- **Embeddings + FAISS rebuild**: `scripts/curation/merge/build_curation_index.py`
- **Communities build (with rerank option)**: `scripts/curation/merge/build_communities.py`
- **Auto-dedup + bucketization**: `scripts/curation/merge/find_pending_dups.py`
  - Uses thresholds from `scripts/scripts_config.yaml`:
    - `auto_dedup` (merge without review)
    - `high_bucket`, `medium_bucket`, `low_bucket` (interactive buckets)
- **LLM merge loop over communities**: `scripts/curation/overnight/run_curation_overnight.py`
  - Includes `auto_dedup` pass via `auto_dedup()` in `scripts/curation/overnight/run_curation_loop.py`

## What We Remove / Avoid (Now Redundant)
- **`scripts/curation/merge/run_merge_pipeline.py`**
  - This one-command wrapper overlaps with the new explicit steps (merge exports → import → prepass → rebuild index → build communities).
  - Keep file if useful for legacy workflows, but **do not reference it in docs**.

## Phase 2 Flow (Explicit Steps)
1. **Merge member exports**
   ```bash
   python scripts/curation/merge/merge_exports.py
   ```
2. **Import into curation DB** (resets DB and artifacts by default)
   ```bash
   python scripts/curation/merge/import_to_curation_db.py
   ```
3. **Atomicity pre-pass** (Phase 1 output)
   ```bash
   python scripts/curation/prepass/atomicity_split_prepass.py
   ```
4. **Rebuild embeddings + FAISS**
   ```bash
   python scripts/curation/merge/build_curation_index.py
   ```
5. **Auto-dedup + LLM bucket routing**
   - Auto-dedup uses `auto_dedup` threshold.
   - High/medium bucket pairs are routed for review/LLM decisions.
   ```bash
   python scripts/curation/merge/find_pending_dups.py
   ```
6. **Rebuild communities (optional rerank)**
   ```bash
   python scripts/curation/merge/build_communities.py --with-rerank
   ```
7. **Run overnight curation loop**
   ```bash
   python scripts/curation/overnight/run_curation_overnight.py
   ```

## Notes
- Phase 2 assumes Phase 1 (atomicity split) has already run and marked originals inactive.
- **LLM behavior in Phase 2**: NO SPLITTING (Phase 1 already did that). LLM only merges experiences.
  - **Merge criteria**: Same atomic action with different conditions/contexts (e.g., same technique applicable to different scenarios).
  - **Keep separate**: Different atomic actions, even if related (preserves atomicity).
  - The prompt explicitly forbids creating multi-step/non-atomic experiences during merge.
- The dedup buckets and thresholds are already implemented and should be reused (no new logic needed).
- If we later want cadence-based neighbor rebuilds or split suggestions, add those as Phase 3 enhancements.

## Success Criteria
- Embeddings and FAISS rebuild complete successfully.
- Auto-dedup merges apply to `>= auto_dedup` pairs only.
- High-bucket pairs are surfaced for LLM decisions via existing flow.
- Communities build without errors and the overnight loop runs end-to-end.
