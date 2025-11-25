# Semi-Auto Curation (Phase 1–2) – Dup & Conflict Detection

Scope constraints for the first cut:
- Only **experiences** (manual volume is small).
- Only entries with `sync_status=pending` (local-only). Treat `sync_status!=pending` as the canonical/anchor set.

Code touchpoints already in repo:
- `src/common/storage/schema.py` — `Experience.sync_status` (default 1, used as the pending flag in docs).
- `src/api/services/search_service.py#L178` — `find_duplicates(...)` orchestrator.
- `src/api/cpu/search_provider.py#L150` and `src/api/gpu/search_provider.py` — duplicate search implementations (text and vector).
- `src/common/storage/repository.py` — session helpers for CRUD.

Minimal plan (easiest implementation path)
1) **Candidate selection**  
   - Query pending experiences: `session.query(Experience).filter(Experience.sync_status == 1)` (add constant for readability).  
   - Anchor pool: all experiences with `sync_status != 1` (or explicitly `= synced` once we enumerate statuses).

2) **Reuse existing duplicate hooks with a filter**  
   - Extend `SearchService.find_duplicates` (and provider methods) to accept `sync_status_filter` or a `where_clause` callable.  
   - In SQLite provider (`_find_experience_duplicates`), add `.filter(Experience.sync_status != 1)` so we only compare pending items against the anchor pool.  
   - In FAISS provider, load vectors only from the anchor pool when building the index or querying. This avoids re-adding new locals to the index until they are approved.

3) **Duplication pass (Agent 1 equivalent)**  
   - For each pending experience: call `find_duplicates(title, playbook, entity_type="experience", category_code=...)` with a threshold of 0.60 (current default).  
   - Emit: `[ {pending_id, candidate_id, score, provider, reason, title, summary} ]`.  
   - Bucket: `score >=0.90 → high-confidence dup`, `0.70–0.90 → needs-review`, `<0.70 → ignore`.

4) **Conflict/high-drift detection (Agent 2 equivalent, lightweight)**  
   - Only run on candidate pairs from step 3.  
   - Checks (no LLM required initially):  
     - Section mismatch (`useful` vs `contextual`).  
     - Title similarity high but playbook diff ratio low (e.g., `SequenceMatcher` or token Jaccard < 0.5).  
     - `updated_at` newer on pending but playbook shorter by >30% (possible regression).  
   - Output per pair: `{pending_id, candidate_id, conflict_types[], notes}` with a short diff snippet (first differing paragraph).

5) **Output & review loop**  
   - Write a CLI under `scripts/find_pending_dups.py` that dumps a JSON/CSV report and prints a tiny table summary (counts per bucket, top 10 pairs).  
   - Accept flags: `--category`, `--limit`, `--threshold`, `--format json|csv|table`.
   - Keep it read-only for now (no auto-merge).

6) **Iteration hooks**  
   - Add a test fixture with a few synthetic pending vs canonical experiences to lock thresholds.  
   - If vector search is enabled, plug in the GPU provider to improve recall; else text provider still works with title substring.
   - Later: replace conflict heuristics with an LLM comparator once basic pipeline is stable.

Atomization-first (manual checklist):
- Before duplicate/conflict detection, skim pending experiences to ensure each is atomic (one outcome, ≤8 bullets, ≤500 words, no unrelated headings).
- Flag non-atomic items for split/merge: note their IDs and briefly describe the extra topics they contain.

Quick evaluation steps after implementing:
- Apply the atomization checklist above to a sample of pending items (e.g., newest 50).
- Then run `python scripts/find_pending_dups.py --limit 200 --format table` on the atomic subset and confirm buckets look sane.
- Spot check high-confidence pairs manually to measure precision; adjust thresholds accordingly.

Open questions to settle when coding:
- Enumerate `sync_status` values into an Enum (pending/synced/remote) to avoid magic `1`.
- Decide whether pending items should be compared against each other (likely yes, but behind a flag to avoid quadratic blowup).
- Where to surface the report in UI: start with CLI; later feed the JSON into the MCP evaluator or a Streamlit pane.
