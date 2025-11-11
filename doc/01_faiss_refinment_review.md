# FAISS Refinement Review Update (2025-11-11)

Commits reviewed: `6232b43` (initial refinement) and `57df8b3` (bug fixes). Remote history could not be fetched because outbound DNS is blocked in this environment, so analysis is limited to local commits.

## What Got Fixed
- `_session_scope` now flushes caller-owned sessions on success and always rollbacks on failure, so legacy single-session CLIs no longer get stuck in "pending rollback" states (`src/search/faiss_index.py:269-309`).
- Embedding inserts now persist `embedding_client.model_name` (`repo:quant`) so FAISS consistency checks and rebuilds can key off the same value as `_compute_checksum()` (`src/embedding/service.py:143-474`).
- Automatic rebuild during `initialize_faiss_with_recovery` now respects `FAISSMetadata.deleted`, so deleted entities stay deleted even after checksum-triggered recovery (`src/search/thread_safe_faiss.py:68-134`).

## Remaining Issues
1. **High – Embedding lookups still use the old key**  
   Both places that check for an existing embedding row still call `self.emb_repo.get_by_entity(..., model_name=self.embedding_client.model_repo)` (`src/embedding/service.py:335-352` and `428-445`). Because inserts now store `<repo>:<quant>`, these lookups never match. Every embed is treated as new, leading to duplicate rows, more tombstones, and `FAISSIndexManager.update()` never being used.  
   **Fix:** Replace those lookups with `model_name=getattr(self.embedding_client, "model_name", "")` (or a helper that normalizes both strings). Consider a migration to collapse duplicate rows created since `57df8b3` landed.

2. **High – Rebuild utilities still query by `model_repo`**  
   `VectorFAISSProvider.rebuild_index()` fetches embeddings with `self.embedding_client.model_repo` (`src/search/vector_provider.py:300-333`). After the storage change, this method (and `scripts/rebuild_index.py`, which calls it) will rebuild an empty FAISS index because no rows match the old key. Automatic rebuild during startup is now fixed, but any manual rebuild or health tooling will drop all vectors.  
   **Fix:** Update every call that filters `Embedding.model_name` to use the same `<repo>:<quant>` value. A quick `rg "model_repo" src -n | grep model_name` surfaces the remaining hotspots.

3. **Medium – Legacy data invisible to checksum/rebuild**  
   `_compute_checksum()` now works for new rows, but embeddings written before `57df8b3` still carry `model_repo`. Unless we migrate those rows (or make `_compute_checksum()`/rebuild attempt both keys), FAISS will think older data doesn’t exist and may trigger unnecessary rebuilds or skip rows entirely.  
   **Fix:** Run a one-time SQL migration to rewrite `embeddings.model_name` to `<repo>:<quant>` where applicable, or add a fallback query that unions both formats until the migration is complete.

## Impact on Other Components
- Keeping `FAISSMetadata.deleted` as the source of truth is now consistent throughout the recovery code, but ensure any admin tooling that used `embeddings` to gauge deletions understands that rows stick around for disaster recovery.
- Scripts and CLIs (e.g., `scripts/search_health.py`, `scripts/tweak/read.py`) should be reviewed for lingering `model_repo` filters; otherwise telemetry and diagnostics will drift from actual FAISS contents.

## Suggested Next Actions
1. Normalize every place that reads/writes `Embedding.model_name` so it always uses the canonical `<repo>:<quant>` string (preferably via a shared helper). 
2. Backfill historical embedding rows to the new format to keep checksum/rebuild logic accurate. 
3. Add a regression test that runs `VectorFAISSProvider.rebuild_index()` end-to-end to ensure it produces the same vector count before and after model name changes.
