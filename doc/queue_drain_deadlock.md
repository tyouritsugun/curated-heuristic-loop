# Queue Drain Deadlock Bug

**Updated:** 2025-11-14  
**Status:** 🔴 Open – fix not yet merged

---

## Summary

`scripts/import.py` pauses background workers *before* requesting the queue drain. Once the worker is paused, no job can leave the queue, so the drain call spins for the entire timeout (300 s) and the import workflow hangs. The same pause-first ordering also appears in `src/services/operations_service.py:360-420` when running sync jobs.

---

## Current Behavior (2025-11-14)

- Code path: `scripts/import.py:300-323`.
- Order of operations today:
  1. `api_client.pause_workers()` → background worker stops accepting jobs.
  2. `api_client.drain_queue(timeout=300)` → waits for queue to reach zero, but nothing processes.
- User impact:
  - Embedding queue remains stuck at whatever count it had when we paused (e.g., 14/10 in the original report).
  - CLI emits “Waiting for embedding queue to drain…” every few seconds until timeout, then warns and proceeds.
  - Queue polling endpoint (`/ui/operations/queue`) is spammed, creating noisy logs.

The doc previously implied the fix landed, but the repository still ships the buggy ordering.

---

## Correct Coordination Workflow

```
1. Check API health.
2. Drain queue while workers are still running.
3. Pause workers once the queue is empty (or user agrees to proceed despite residual jobs).
4. Perform import (clear DB + FAISS).
5. Resume workers in a finally block.
```

This ensures no jobs are lost (queue drained before destructive operations), addresses race conditions, and guarantees workers resume even if the script crashes mid-import.

---

## Implementation Plan

1. **Reorder coordination block in `scripts/import.py`:**
   - Call `drain_queue(timeout=300)` first. If queue is already empty, skip waiting entirely.
   - After a successful drain, call `pause_workers()` and set `api_coordinated=True`.
2. **Handle drain timeouts explicitly:**
   - When `--yes` is set, continue automatically with a warning.
   - Without `--yes`, prompt `Queue still has N pending items. Continue anyway? [y/N]`.
   - Always log the reason (timeout vs. API error) and whether we attempted to pause anyway.
3. **Race condition mitigation:**
   - Option A (short-term): loop `drain_queue()` → check depth via `/ui/operations/queue` until two consecutive readings are zero, or max retries reached.
   - Option B (long-term): add server endpoint `/admin/queue/quiesce` that atomically blocks new submissions and drains existing work.
4. **State recovery / finally block:**
   - Adopt the pattern:
     ```python
     api_coordinated = False
     try:
         drained = api_client.drain_queue(timeout=timeout)
         # prompt user on failure
         if api_client.pause_workers():
             api_coordinated = True
         perform_import()
     finally:
         if api_coordinated:
             api_client.resume_workers()
     ```
   - Note: `scripts/import.py:455-459` already resumes workers, but only if `api_coordinated` is `True`. Update the flag logic so it flips to `True` **only after** pause succeeds.
5. **Failed-job handling:**
   - Confirm `/admin/queue/drain` ignores jobs in a terminal `failed` state. If not, update `src/services/background_worker.py` to exclude failures so drain doesn’t block forever.
6. **Audit siblings:**
   - `src/services/operations_service.py:360-420` (sync handler) currently pauses workers first; mirror the new pattern.
   - Search other scripts for `pause_workers()` usage and document decisions.
7. **Telemetry:**
   - Emit a structured log (`queue_coordination`) with fields: `{"drained": true/false, "remaining": N, "paused": true/false, "elapsed": seconds}` to speed up debugging.

### Critical: State Recovery Pattern

```python
# scripts/import.py (pseudo-code)
api_coordinated = False
try:
    drained = api_client.drain_queue(timeout=timeout)
    if not drained:
        handle_timeout(queue_depth=api_client.queue_depth(), assume_yes=args.yes)

    if api_client.pause_workers():
        api_coordinated = True

    perform_import()
finally:
    if api_coordinated:
        api_client.resume_workers()
```

This matches the existing resume logic in `scripts/import.py:455-459` but makes the ordering explicit and guarantees we resume only after a successful pause.

---

## Timeout, Prompts, and `--yes`

| Situation | Default behavior | `--yes` behavior |
|-----------|------------------|------------------|
| Drain succeeds | Continue, pause, import. | Same. |
| Drain times out | Prompt user (y/N). Abort on "no". | Log warning, continue, optionally pause anyway. |
| Drain API error | Prompt unless `--skip-api-coordination`. | Log + continue. |
| Queue already empty | Skip waiting, still pause and resume. | Same. |

Document these outcomes in `doc/manual.md` so operators know what to expect during maintenance windows.

---

## Race Conditions & Large Queues

- **New jobs arriving mid-drain:** after `drain_queue()` returns success, query `/ui/operations/queue` once more. If new items appeared, rerun drain up to `MAX_STABLE_CHECKS` times (e.g., 3) before pausing, or switch to the proposed `/admin/queue/quiesce` endpoint.
- **Large queue threshold:** if depth > 1,000, warn that draining may exceed the default 300 s and recommend pausing ingestion sources first.

---

## Concurrent Coordination

- Define `WorkerPauseToken` inside `worker_control` so multiple clients can call pause/resume safely. Each pause request obtains a token; workers resume only when every outstanding token is released (reference-counted behavior).
- Make `pause_workers()` and `resume_workers()` idempotent—if another client already paused workers, the API should respond with the current state rather than failing.
- Document recommended workflow for MCP/import scripts so concurrent maintenance windows do not fight over worker state.

---

## Queue Endpoint Expectations

- `/ui/operations/queue` (see `src/api/routers/ui.py:1011-1038`) must expose counts for `pending`, `in_progress`, and `failed`. Drain logic should poll only the first two statuses.
- Update `partials/ops_queue_card.html` to highlight failed jobs separately, ensuring operators are not misled into thinking drain is stuck when only failed items remain.
- Add regression tests around `OperationsService.queue_status()` so future changes cannot accidentally lump failed jobs back into the blocking count.

---

## State Recovery & Crash Safety

- Keep the existing `finally` block (`scripts/import.py:455-459`) but ensure it always has the correct `api_client` reference and is resilient if `pause_workers()` never succeeded.
- Consider persisting coordination state (e.g., `data/queue_coordination.json`) so another process can resume workers if the original script dies completely.
- Add guidance for operators: if workers are paused unexpectedly, run `uv run python scripts/admin.py resume-workers` (new helper) to recover.

---

## Queue Semantics & Failed Jobs

- Verify the `/admin/queue/drain` implementation only waits on `pending` + `in_progress` jobs. Failed jobs should be surfaced separately and should not block drain.
- Add docs describing how to inspect failed jobs (`/ui/operations/queue?status=failed`) so operators can triage them outside the drain workflow.

---

## Test Plan

| Scenario | Steps | Expected |
|----------|-------|----------|
| Happy path | Start API + worker, enqueue >0 embeddings, run `scripts/import.py --yes`. | Queue drains to 0 while workers run, pause succeeds, import completes without timeout. |
| Drain timeout | Freeze worker so drain exceeds 300 s. | Script warns, prompts user (unless `--yes`), no infinite polling, resume still runs. |
| API offline | Stop server, run import. | Health check fails, script logs “skipping worker coordination,” proceeds safely. |
| CPU-only mode | Set `CHL_SEARCH_MODE=sqlite_only`. | Coordination skipped (current behavior retained). |
| Failed job present | Insert failed job in queue. | Drain ignores failed jobs, completes if no pending/in-progress jobs remain. |
| Race condition | Enqueue new job right after drain returns zero. | Second stability check catches it, drain re-runs before pause. |
| Crash simulation | Force exception after pause but before import completion. | Finally block resumes workers automatically. |

Success criteria: each test run leaves workers resumed (`/admin/queue/status` reports `running`) and queue depth matches expectation.

---

## Success Criteria

- `scripts/import.py --yes` drains + pauses + resumes without hanging, confirmed by structured log `queue_coordination`.
- `src/services/operations_service.sync_embeddings()` mirrors the same ordering and passes CI integration test that injects artificial queue load.
- New telemetry dashboards show `queue_depth`, `worker_state`, and `last_pause_caller` so ops can confirm no deadlock persisted longer than 5 minutes.

---

## Issue Tracking

- [tyouritsugun/curated-heuristic-loop#310](https://github.com/tyouritsugun/curated-heuristic-loop/issues/310) – drain ordering & timeout UX.
- [tyouritsugun/curated-heuristic-loop#311](https://github.com/tyouritsugun/curated-heuristic-loop/issues/311) – worker pause token & concurrent coordination.

---

## Open Questions

1. Should we lower the default drain timeout now that the deadlock is fixed (e.g., 120 s) to surface real slowdowns sooner?
2. Would a server-side `/admin/queue/quiesce` endpoint (atomic drain + pause) reduce the need for client coordination logic entirely?
3. Should pause/resume become reference-counted (so concurrent scripts don’t accidentally resume workers still needed by another job)?
4. Do we need smoke tests in CI that simulate a non-empty queue before running import/export scripts to catch regressions automatically?
5. Should we expose the queue depth and worker pause state via `/health` so dashboards can alert when workers stay paused >5 minutes?

Until the above code changes land, advise operators to run `scripts/import.py --skip-api-coordination` only if they fully understand the risk (potential job loss) or to manually drain the queue via API first.
