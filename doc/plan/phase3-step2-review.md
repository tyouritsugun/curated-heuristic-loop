# Phase 3 Stage 2 Review (Refined)

Based on code + tests, Stage 2 is **complete** with a few documented gaps and clarifications.

---

## ✅ Stage 1 (Complete) — Single-Community Prompt Harness

- ✓ `prompt_harness.py` implemented with required flags
- ✓ Prompt building + validation working
- ✓ Tests passing (prompt_utils)
- ✓ No DB mutations (read-only)

---

## ✅ Stage 2 (Complete) — Round Loop + Convergence

### Core Loop Requirements ✓

- ✓ Round loop with community selection + priority ordering
- ✓ Filters out `sync_status=2` and oversized (unless `--process-oversized`)
- ✓ Prompt built per community
- ✓ LLM call with retries + validation
- ✓ Decisions applied (merge_all / merge_subset / keep_separate / manual_review)
- ✓ Graph rebuild using cached neighbors (no FAISS re-query)
- ✓ Progress metrics + convergence check

### LLM Response Contract ✓

- ✓ JSON schema validation
- ✓ Decision type enforcement
- ✓ Merge validation + downgrade rules
- ✓ Invalid IDs filtered with warnings

### Convergence & Safety ✓

- ✓ `--max-rounds` (default 3)
- ✓ Relative progress thresholds (items + communities)
- ✓ 2-round convergence rule
- ✓ Zero-progress early stop
- ✓ Optional max-runtime cap (estimated or explicit)

### Outputs ✓

- ✓ Evaluation log CSV
- ✓ Communities JSON export
- ✓ Morning report
- ✓ Dry-run sidecars (`.dryrun`)

### Flags & Config ✓

- ✓ Required flags present (`--dry-run`, `--batch-size`, `--max-rounds`, `--improvement-threshold`, `--two-pass`, `--process-oversized`)
- ✓ Config loaded from `scripts_config.yaml`
- ✓ LLM settings with env overrides

### Error Handling ✓

- ✓ LLM retry logic with backoff
- ✓ Failure → manual_review
- ✓ Graph rebuild failure handling
- ✓ Warnings collected + reported

### State Management ✓

- ✓ Phase‑3 state schema with progress history
- ✓ Saves after each community + round
- ✓ `--reset-state` support

---

## 🔍 Clarifications / Adjustments

- **`--two-pass`** only *switches to* `communities_rerank.json` if present. It does not run rerank itself (Stage 3 scope).
- **Dry‑run** writes `.dryrun` files for evaluation log and communities, but **does not write** the state file (it logs “would save”).
- **Config validation** relies on downstream exceptions (no explicit preflight in `run_phase3.py`).
- When using **`--db-copy`**, use a **separate state file** to avoid stale state blocking execution.

---

## 🔍 Known Gaps (Non‑blocking)

1. **No cost summary** (tokens/$) in morning report
2. **No tuning_report.txt** (optional report mentioned in plan)
3. **Dry‑run output examples** not documented

---

## ✅ Stage 2 Test Recommendations

1. Dry‑run smoke test:
   ```bash
   python scripts/curation/run_phase3.py --dry-run --batch-size 1 --max-rounds 1
   ```
2. Verify outputs:
   - `data/curation/morning_report.md.dryrun`
   - `data/curation/evaluation_log.csv.dryrun`
   - `data/curation/communities.json.dryrun`
3. Confirm state file is *not* written in dry‑run

---

## ✅ Ready for Stage 3?

**Yes**, with minor optional enhancements:

- Add cost tracking to morning report
- Add optional tuning report generation
- Add integration tests for end‑to‑end dry‑run

---

If you want this review kept current, add line references after finalizing Stage 2.
