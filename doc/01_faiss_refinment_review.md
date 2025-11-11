# FAISS Refinement Review Update (2025-11-11)

Commits reviewed: `6232b43` (initial refinement), `57df8b3` (first bug fix pass), and `31367d5` (latest fixes + docs). Remote history is still unavailable because outbound DNS is blocked in this environment, so the review reflects the local repository state only.

## Improvements Since Last Review
- **Consistent keying inside services.** `EmbeddingService` stores a canonical `<repo>:<quant>` string and also uses the same key for `get_by_entity` checks, eliminating the duplicate-row issue noted earlier (`src/embedding/service.py:38-205`, `335-474`).
- **Vector rebuild respects canonical keys.** `VectorFAISSProvider.rebuild_index()` now pulls embeddings via the same canonical model name, preventing deleted rows from returning during manual rebuilds (`src/search/vector_provider.py:34-119`, `297-319`).
- **Background worker wiring.** `BackgroundEmbeddingWorker` and the FastAPI startup path now thread `config.embedding_model` through to both the embedding service and the vector provider so long-running workers stay consistent (`src/api_server.py:194-235`, `src/services/background_worker.py:37-250`).

## Remaining Issues
1. **High – Scripts still call the old signatures (runtime failure).**  
   `VectorFAISSProvider.__init__` now requires `model_name` and no longer accepts a `session` kwarg, yet `scripts/rebuild_index.py:83-94`, `scripts/tweak/read.py:86-108`, and `scripts/tweak/write.py:86-108` still call it with the old arguments. These CLIs will raise `TypeError` before doing any work.  
   **Fix:** Pass `model_name=config.embedding_model` and drop the obsolete `session=` parameter in every call site.

2. **High – EmbeddingService constructor not updated everywhere.**  
   `EmbeddingService.__init__` now expects `model_name`, but `scripts/sync_embeddings.py:122-146` still instantiate it with only `(session, embedding_client, faiss_index_manager)`. Running `python scripts/sync_embeddings.py` will crash immediately.  
   **Fix:** Thread `config.embedding_model` into that script (and any other ad-hoc tooling) to match the new signature.

3. **Medium – Existing embeddings still use the legacy key.**  
   The code path now writes `<repo>:<quant>`, but rows created before `57df8b3` keep the old `model_repo` string. Neither `_compute_checksum()` nor rebuild routines attempt to read both formats, so older data remains invisible to the new consistency checks until a migration runs.  
   **Fix:** Run a one-time migration (or add a dual-format fallback) so checksum validation and rebuilds produce accurate results without requiring a full re-embed.

## Impact on Other Components
- Any diagnostics or admin tooling that instantiates `VectorFAISSProvider` or `EmbeddingService` will now fail fast unless updated; coordinate with dev-ops/docs so users know to pull the latest scripts once the fixes land.
- Tombstone policy (`FAISSMetadata.deleted` as source of truth, embeddings retained for DR) now flows through both automatic and manual rebuild paths; just ensure future migrations preserve that invariant.

## Suggested Next Actions
1. Update every script/CLI instantiation to pass the canonical `model_name` and remove deprecated parameters; add a smoke test that imports each CLI module to catch signature drift.
2. Backfill or dual-read legacy embedding rows so checksum validation monitors the real corpus.
3. Once the above land, re-run manual rebuild + sync flows to verify they behave identically to the production server path.
