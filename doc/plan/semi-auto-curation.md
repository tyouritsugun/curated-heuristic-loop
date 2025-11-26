# Semi-Auto Curation (Phase 1–2) – Dup & Conflict Detection

Scope constraints for the first cut:
- Only **experiences** (manual volume is small).
- Only entries with `sync_status=pending` (local-only). Treat `sync_status!=pending` as the canonical/anchor set.

Code touchpoints already in repo:
- `src/common/storage/schema.py` — `Experience.sync_status` (default 1, used as the pending flag in docs).
- `src/api/services/search_service.py#L178` — `find_duplicates(...)` orchestrator.
- `src/api/cpu/search_provider.py#L150` and `src/api/gpu/search_provider.py` — duplicate search implementations (text and vector).
- `src/common/storage/repository.py` — session helpers for CRUD.

1) **Candidate selection + atomicity scoring**  
   - Introduce `SyncStatus` `IntEnum` (`PENDING=1, SYNCED=2, REMOTE=3, REJECTED=4`) and swap all magic `1` filters to `SyncStatus.PENDING`.  
   - Query pending: `session.query(Experience).filter(Experience.sync_status == SyncStatus.PENDING)`. Anchor pool: `!= SyncStatus.PENDING`.  
   - Add `score_atomicity(experience)` heuristic (bullets>8, words>500, multiple headings) returning `score, flags, suggestion`. Store in the report; allow `--min-atomicity` / `--atomicity-below` to focus review.

2) **Duplicate search with scoped pools**  
   - `SearchService.find_duplicates` accepts `where_clause`/`sync_status_filter`. SQLite provider filters anchors to non-pending; FAISS builds/query index from anchors only.  
   - Optional `--compare-pending` does pending-vs-pending, scoped by category and recent window (e.g., last 30 days, group size ≤50) to avoid quadratic blowup.

3) **Duplication pass & bucketing**  
   - Call `find_duplicates(..., threshold=0.60)`.  
   - Buckets: `>=0.92 → high`, `0.75–0.92 → medium`, `<0.75 → low/ignore`.  
   - Each row carries: pending_id, candidate_id, score, provider, atomicity, conflicts, recommended_action.

4) **Conflict/high-drift detection (richer signals, still lightweight)**  
   - Checks: section mismatch; high title similarity + low playbook overlap; pending newer but shorter by >30% (regression); pending extends canonical (≥2 shared bullets and ≤2 unique bullets); canonical outdated (pending 20–100% longer and newer).  
   - Mark conflict types; feed into recommended action (e.g., `PENDING_EXTENDS_CANONICAL → update canonical`).

5) **Interactive review loop (actionable CLI)**  
   - `python scripts/find_pending_dups.py --bucket high --interactive [--dry-run]`.  
   - For each pair: show pending vs canonical side-by-side, similarity, conflicts, atomicity. Actions: merge (mark pending synced), update canonical with pending deltas, reject pending, split (flag), skip, show diff.  
   - Default non-interactive output still supports `--format table|json|csv`; print counts per bucket plus recommended actions. Colorize but also emit plain tags for logs/CI.

6) **Metrics & tuning loop**  
   - `--report-metrics` aggregates manual spot-checks (log to `evaluation_log.csv`): precision by bucket, false positives, conflict distribution.  
   - Emit threshold suggestions (e.g., raise high bucket to 0.92 if precision <90%).

7) **Iteration hooks & tests**  
   - Add synthetic fixtures covering: high-similar dup, pending-extends-canonical, non-atomic entry, section mismatch.  
   - Keep GPU provider optional; text provider remains default.

Workflow (user-facing)
1. Run detection with atomicity scoring: `python scripts/find_pending_dups.py --category PGS --format table`.  
2. Triage non-atomic first: `--atomicity-below 0.6 --suggest-splits`.  
3. Process high bucket interactively: `--bucket high --interactive --dry-run` (remove `--dry-run` to apply).  
4. Generate metrics: `--report-metrics > tuning_report.txt` and adjust thresholds.
