
# FAISS Refinement Review (2025-11-11)

## Findings
1. **High – Automatic rebuild resurrects tombstoned vectors**  
   The new `needs_rebuild()` path in `initialize_faiss_with_recovery` rebuilds the index by replaying *all* rows from `embeddings` (`src/search/thread_safe_faiss.py:67-117`). It never consults `faiss_metadata.deleted`, even though `delete()` only tombstones rows (`src/search/faiss_index.py:459-478`) and embeddings are left intact. Once this branch fires (e.g., checksum mismatch), every previously deleted entity is silently re-added to FAISS, undoing the metadata consistency work.  
   **Fix:** Reuse the `_rebuild_index()` strategy—derive the rebuild set from `faiss_metadata` filtered by `deleted == False`, or remove embeddings when tombstoning so the recovery query can safely touch the `embeddings` table.

2. **High – Checksum validation never reflects the real database state**  
   `_compute_checksum()` now drives `_validate_consistency()` and `needs_rebuild()`, but it filters by `Embedding.model_name == self.model_name` (`src/search/faiss_index.py:780-792`). `self.model_name` is stored as `<repo>:<quant>` (e.g., `Qwen/...:Q4`), while embeddings are persisted with `model_repo` only (`src/embedding/service.py:142-149`, `src/storage/repository.py:329-341`). The query therefore always returns zero rows, the checksum becomes `md5("model:0")`, and both the stored and recomputed checksums remain equal no matter how many embeddings exist. The newly added validation never trips, so metadata drift goes undetected and the auto-rebuild in Finding #1 is rarely triggered for the right reasons.  
   **Fix:** Align the key used in embeddings with what FAISS stores—either persist `<repo>:<quant>` in `embeddings.model_name`, or have `_compute_checksum()` strip the quant suffix / use `model_repo`. Once the keys match, consider extending the checksum beyond a simple count.

3. **Medium – `_session_scope` leaves shared sessions wedged after failures**  
   The helper only rolls back when it created the session (`owns_session=True`), see `src/search/faiss_index.py:269-305`. Several CLIs still inject a long-lived session without `session_factory` (e.g., `scripts/tweak/read.py:67-105`). If any FAISS metadata write raises (lock, integrity error), `_session_scope` exits without rolling back, leaving that shared session stuck in "pending rollback" and breaking every subsequent DB call until the process is restarted. The previous implementation explicitly rolled back even for caller-owned sessions.  
   **Fix:** Always rollback on exception (and flush on success) when using the legacy shared-session path so existing tooling keeps working; alternatively, drop support for the shared session mode and make `session_factory` mandatory.

## Open Questions / Follow-ups
- Do we still need the legacy single-session mode? If not, we can eliminate the problematic branch in `_session_scope` entirely.
- Should tombstones live alongside embeddings (or vice versa) so disaster recovery never has to guess which table is the source of truth?
