# Experience Curation Refinement - Phase 1 Implementation Plan

## Overview

**Goal:** Improve curation quality and stability with minimal code changes
**Effort:** 1 day (6-7 hours)
**Priority:** High (addresses core quality issues)
**Status:** Not started

---

## Background

Current curation issues identified:
1. **Vague atomicity guidance** - LLM has no concrete examples of what "atomic" means
2. **Stale neighbor graph** - Neighbors never rebuild during multi-round loops, missing new duplicates
3. **No quality metrics** - Can't measure if changes improve precision/recall

These three issues directly impact:
- Merge decision quality (over-merging or under-merging)
- Convergence speed (missed duplicates require more rounds)
- Ability to iterate (no feedback loop)

---

## Task 1: Enhance Atomicity Prompt

### Current State
File: `scripts/curation/agents/prompts/curation_prompt.yaml`

Current atomicity guidance (lines 13-17):
```yaml
Atomicity rule:
- Experiences should stay atomic (single issue + single fix).
- Merge only if the root cause AND key resolution steps are essentially identical.
- If steps differ, keep separate or manual_review.
- If merging would make the experience longer or more complex, prefer keep_separate.
```

**Problem:** Too vague. No concrete examples. "Essentially identical" is subjective.

### Proposed Changes

Add a concrete rubric with examples after line 17:

```yaml
Atomicity rule:
- Experiences should stay atomic (single issue + single fix).
- Merge only if the root cause AND key resolution steps are essentially identical.
- If steps differ, keep separate or manual_review.
- If merging would make the experience longer or more complex, prefer keep_separate.

Examples of ATOMIC experiences (good):
  ✓ "Fix Django timeout by increasing connection pool to 100"
    (Single issue: timeout. Single fix: pool size adjustment)

  ✓ "Debug race condition using thread-safe logger"
    (Single issue: race condition. Single fix: specific tool)

  ✓ "Resolve npm install failure by clearing cache"
    (Single issue: install fails. Single fix: cache clear)

Examples of NON-ATOMIC experiences (should NOT merge):
  ✗ "Fix timeout by increasing pool AND enabling caching AND adding monitoring"
    (Three distinct tactics bundled together)

  ✗ "Deploy to production, monitor error rates, then tune configuration"
    (Multi-step workflow, not a single fix)

  ✗ "Handle database errors: add retry logic for timeouts, connection pooling for scale, logging for debugging"
    (Three unrelated solutions for different sub-problems)

Complexity rule:
- If merging would make the combined entry >2x longer than either source, prefer keep_separate.
- If entries share a theme but solve different sub-problems, prefer keep_separate.

Note-taking:
- If an entry appears to bundle multiple independent tactics, note it in your response.
  This helps identify entries that may need future splitting.
```

### Implementation Steps

1. Open `scripts/curation/agents/prompts/curation_prompt.yaml`
2. Insert the enhanced rubric after line 17
3. Test with sample community to verify LLM can parse examples
4. Commit with message: "refine: add concrete atomicity examples to curation prompt"

### Success Criteria
- ✅ Prompt includes 3 atomic examples
- ✅ Prompt includes 3 non-atomic examples
- ✅ Complexity rule (2x length) explicitly stated
- ✅ LLM can successfully process updated prompt

### Expected Impact
- **Precision:** Increase by reducing false merges (over-merging)
- **Consistency:** LLM decisions should be more predictable
- **Time:** 30-45 minutes

---

## Task 2: Implement Neighbor Rebuild Cadence

### Current State

File: `scripts/curation/run_curation_loop.py`

Current behavior (line 779):
```python
# Reload neighbors (cached from initial build)
neighbor_records = load_neighbors_records(neighbors_path)
G = build_graph_from_neighbors(
    items=items,
    neighbor_records=neighbor_records,  # ← Always uses cached neighbors
    threshold=args.edge_threshold,
    per_category=per_category,
)
```

**Problem:** Neighbors are cached once and never refreshed. As entries merge, the graph becomes stale.

**Example:**
- Round 1: Entry A and B are dissimilar (score 0.60, below threshold 0.72)
- Round 1: Entry A merges with Entry C (similar content)
- Round 2: New merged A+C would be similar to B (score 0.78) but they were never neighbors
- Result: B is never considered for merging with A+C

### Proposed Changes

Add automatic neighbor rebuild logic with configurable cadence.

#### Option A: Rebuild every N rounds (simpler)
```python
# Add to argument parser
parser.add_argument(
    "--rebuild-neighbors-cadence",
    type=int,
    default=0,
    help="Rebuild neighbors every N rounds (0=never, 1=every round, 2=every other round)"
)

# Add rebuild logic in main loop (after applying merges, before community detection)
if args.rebuild_neighbors_cadence > 0 and round_index % args.rebuild_neighbors_cadence == 0:
    logger.info(f"Round {round_index}: Rebuilding neighbors (cadence={args.rebuild_neighbors_cadence})")

    # Rebuild FAISS index from current items
    from scripts.curation.common.neighbor_builder import rebuild_neighbors
    neighbor_records = rebuild_neighbors(
        items=items,
        faiss_index_path=args.faiss_index,
        top_k=args.top_k,
        threshold=args.neighbor_threshold,
        output_path=neighbors_path
    )
else:
    logger.info(f"Round {round_index}: Using cached neighbors")
    neighbor_records = load_neighbors_records(neighbors_path)
```

#### Option B: Rebuild based on merge rate threshold (smarter)
```python
# Add to argument parser
parser.add_argument(
    "--rebuild-neighbors-threshold",
    type=float,
    default=0.05,
    help="Rebuild neighbors if merge rate exceeds this threshold (e.g., 0.05 = 5%)"
)

# Track merge rate from previous round
prev_round_merge_rate = 0.0

# Add rebuild logic
if prev_round_merge_rate > args.rebuild_neighbors_threshold:
    logger.info(
        f"Round {round_index}: Rebuilding neighbors "
        f"(merge_rate={prev_round_merge_rate:.2%} > threshold={args.rebuild_neighbors_threshold:.2%})"
    )
    neighbor_records = rebuild_neighbors(...)
else:
    logger.info(
        f"Round {round_index}: Using cached neighbors "
        f"(merge_rate={prev_round_merge_rate:.2%} ≤ threshold={args.rebuild_neighbors_threshold:.2%})"
    )
    neighbor_records = load_neighbors_records(neighbors_path)

# Update merge rate after round completes
prev_round_merge_rate = num_merges / len(items) if len(items) > 0 else 0.0
```

### Implementation Steps

1. **Create helper function** `rebuild_neighbors()` in `scripts/curation/common/neighbor_builder.py`:
   ```python
   def rebuild_neighbors(
       items: list[Experience],
       faiss_index_path: str,
       top_k: int,
       threshold: float,
       output_path: str,
       with_rerank: bool = False
   ) -> list[dict]:
       """
       Rebuild neighbor graph from current item set.

       Returns:
           List of neighbor records (same format as load_neighbors_records)
       """
       # Re-embed items if needed, rebuild FAISS index, compute neighbors
       # Save to output_path and return records
       pass
   ```

2. **Add CLI arguments** to `run_curation_loop.py`
   - `--rebuild-neighbors-cadence N` (Option A)
   - OR `--rebuild-neighbors-threshold X` (Option B)
   - Recommend starting with Option A (simpler)

3. **Add rebuild logic** in main loop (around line 779)

4. **Log graph changes** for analysis:
   ```python
   if rebuilding_neighbors:
       old_edge_count = len(neighbor_records_before)
       new_edge_count = len(neighbor_records_after)
       logger.info(f"  Old neighbor edges: {old_edge_count}")
       logger.info(f"  New neighbor edges: {new_edge_count}")
       logger.info(f"  Delta: {new_edge_count - old_edge_count:+d}")
   ```

5. **Test** with small dataset:
   ```bash
   python scripts/curation/run_curation_loop.py \
       --rebuild-neighbors-cadence 2 \
       --max-rounds 5 \
       --dry-run
   ```

6. **Commit** with message: "feat: add neighbor rebuild cadence to curation loop"

### Success Criteria
- ✅ `--rebuild-neighbors-cadence N` flag works
- ✅ Neighbors rebuild every N rounds
- ✅ Log shows before/after edge counts
- ✅ No crashes or data corruption

### Expected Impact
- **Recall:** Increase by catching newly similar entries post-merge
- **Convergence:** Potentially faster (fewer "missed duplicate" rounds)
- **Cost:** Higher (re-embedding + FAISS rebuild each N rounds)
- **Time:** 2-3 hours

---

## Task 3: Add Merge Quality Tracking

### Current State

File: `scripts/curation/common/decision_logging.py`

Evaluation log schema (line 20):
```python
timestamp,user,entry_id,action,target_id,was_correct,notes
```

**Problem:** `was_correct` field exists but is never populated. No precision/recall tracking.

### Proposed Changes

Add merge quality metrics to morning report and evaluation log.

#### Metrics to Track

1. **Precision** (per round):
   - Definition: % of merges that are correct
   - Calculation: `correct_merges / total_merges`
   - Data source: Manual validation (sample-based)

2. **Recall** (per round):
   - Definition: % of true duplicates that were merged
   - Calculation: Harder to measure (requires ground truth)
   - Proxy: Track "new merge pairs per round" (if converging, should decrease)

3. **Merge rate** (per round):
   - Definition: % of items merged
   - Calculation: `num_merges / total_items`

4. **Community stability** (future Phase 2):
   - Definition: Jaccard similarity of community membership between rounds
   - Deferred to Phase 2

#### Implementation Approach

**Step 1: Add merge rate to morning report**

File: `scripts/curation/reporting/morning_report.py`

Add after line ~45 (where progress percentage is calculated):
```python
# Calculate merge statistics
total_items = len(load_all_experiences())
merges_this_round = count_merges_in_latest_round()  # from decision log
merge_rate = merges_this_round / total_items if total_items > 0 else 0.0

report_lines.append(f"Merge rate this round: {merge_rate:.1%} ({merges_this_round}/{total_items})")
```

**Step 2: Add precision tracking placeholder**

Since precision requires manual validation, add infrastructure to support it:

```python
# In decision_logging.py, add helper function
def get_precision_from_validation_sample(round_index: int) -> Optional[float]:
    """
    Calculate precision from manually validated merges in evaluation log.

    Returns:
        Precision (0.0-1.0) if validation data exists, else None
    """
    df = pd.read_csv(EVALUATION_LOG_PATH)

    # Filter to this round, merge actions, with was_correct populated
    round_merges = df[
        (df['round_index'] == round_index) &
        (df['action'] == 'merge') &
        (df['was_correct'].notna())
    ]

    if len(round_merges) == 0:
        return None

    correct = (round_merges['was_correct'] == True).sum()
    return correct / len(round_merges)
```

**Step 3: Update morning report to include precision (if available)**

```python
precision = get_precision_from_validation_sample(latest_round)
if precision is not None:
    report_lines.append(f"Merge precision (validated): {precision:.1%}")
else:
    report_lines.append("Merge precision: No validation data yet")
```

**Step 4: Add validation workflow documentation**

Create `doc/curation-validation.md` with instructions:
```markdown
# Merge Validation Workflow

To track precision, periodically validate merge decisions:

1. After each round, sample 20-30 merge decisions
2. For each merge, manually check if it was correct
3. Update evaluation log with was_correct=True/False
4. Next morning report will include precision metric

Example SQL update (if using SQLite):
```sql
UPDATE evaluation_log
SET was_correct = TRUE
WHERE entry_id = 'EXP-123' AND target_id = 'EXP-456';
```

Or via CSV edit:
1. Open data/curation/evaluation_log.csv
2. Find merge rows from latest round
3. Set was_correct column to True/False
4. Save and rerun morning report
```

### Implementation Steps

1. **Update `morning_report.py`** to calculate and display merge rate
2. **Add precision calculation** to `decision_logging.py`
3. **Update morning report** to show precision if validation data exists
4. **Create validation docs** in `doc/curation-validation.md`
5. **Test** with mock evaluation log data
6. **Commit** with message: "feat: add merge quality metrics to morning report"

### Success Criteria
- ✅ Morning report shows merge rate per round
- ✅ Morning report shows precision if validation data exists
- ✅ Validation workflow documented
- ✅ Helper functions tested with mock data

### Expected Impact
- **Visibility:** Can now measure if changes improve quality
- **Iteration:** Data-driven refinement becomes possible
- **Time:** 2-3 hours

---

## Testing Plan

### Pre-Implementation Tests
1. **Baseline run** (before Phase 1 changes):
   - Run 3-round curation on sample dataset
   - Record: merge rate, convergence speed, manual assessment of 20 merges
   - Save logs for comparison

### Post-Implementation Tests
1. **Task 1 (Prompt)**: Run single round with enhanced prompt
   - Compare merge decisions vs. baseline
   - Check if LLM follows new examples

2. **Task 2 (Rebuild)**: Run 3-round loop with `--rebuild-neighbors-cadence 2`
   - Verify neighbors rebuild on round 2
   - Check if new merge pairs are discovered

3. **Task 3 (Metrics)**: Validate a sample of merges
   - Fill in `was_correct` column in evaluation log
   - Check morning report displays precision

### Success Metrics
- **Prompt enhancement**: Fewer vague merge decisions (subjective assessment)
- **Neighbor rebuild**: At least 1 new merge pair discovered post-rebuild
- **Metrics**: Morning report displays all intended metrics

---

## Rollout Plan

### Step 1: Implement in feature branch
```bash
git checkout -b feature/curation-phase1
```

### Step 2: Implement tasks in order
1. Task 1: Prompt enhancement (commit 1)
2. Task 2: Neighbor rebuild (commit 2)
3. Task 3: Quality metrics (commit 3)

### Step 3: Test with sample data
```bash
# Run baseline (current main branch)
git checkout main
python scripts/curation/run_curation_loop.py --max-rounds 3 --dry-run
# Save output to baseline.log

# Run with Phase 1 changes
git checkout feature/curation-phase1
python scripts/curation/run_curation_loop.py \
    --max-rounds 3 \
    --rebuild-neighbors-cadence 2 \
    --dry-run
# Save output to phase1.log

# Compare
diff baseline.log phase1.log
```

### Step 4: Manual validation
- Sample 20-30 merge decisions from each run
- Mark as correct/incorrect
- Calculate precision for both

### Step 5: Document findings
- Update `doc/plan/experience-curation-refine.md` with results
- Decide whether to proceed to Phase 2

### Step 6: Merge to main
```bash
git checkout main
git merge feature/curation-phase1
git push
```

---

## Risk Mitigation

### Risk 1: Neighbor rebuild is too slow
**Mitigation:**
- Start with cadence=2 (rebuild every other round, not every round)
- Profile rebuild time with sample dataset
- If too slow, consider Option B (rebuild only when merge_rate > threshold)

### Risk 2: Prompt examples cause unexpected behavior
**Mitigation:**
- Test prompt with diverse community samples before deploying
- Monitor decision distribution (% merge_all vs keep_separate)
- Rollback if decisions become too conservative or too aggressive

### Risk 3: Precision tracking requires too much manual work
**Mitigation:**
- Start with small sample size (10-15 merges per round)
- Automate sampling (randomly select from decision log)
- Consider active learning: prioritize validation of uncertain cases

---

## Acceptance Criteria

Phase 1 is complete when:
- ✅ All 3 tasks implemented and tested
- ✅ Baseline vs. Phase 1 comparison complete
- ✅ At least one full curation loop run successfully with all changes
- ✅ Morning report displays new metrics
- ✅ Findings documented in parent plan
- ✅ Code merged to main branch

---

## Next Steps After Phase 1

If Phase 1 shows improvement:
- **Proceed to Phase 2**: Orchestrated two-pass rerank workflow
- **Continue manual validation**: Build precision dataset for ongoing monitoring
- **Tune parameters**: Adjust rebuild cadence, edge threshold based on data

If Phase 1 shows no improvement or regressions:
- **Analyze why**: Were hypotheses wrong? Implementation issues?
- **Iterate**: Adjust prompt examples, rebuild logic, or pivot approach
- **Document lessons**: Update parent plan with findings

---

## Estimated Timeline

| Task | Effort | Dependencies |
|------|--------|--------------|
| Task 1: Prompt enhancement | 30-45 min | None |
| Task 2: Neighbor rebuild | 2-3 hours | None |
| Task 3: Quality metrics | 2-3 hours | None |
| Testing & validation | 1-2 hours | Tasks 1-3 complete |
| Documentation | 30 min | Testing complete |
| **Total** | **6-7 hours** | |

Recommended schedule:
- **Day 1 morning**: Tasks 1-2 (prompt + rebuild)
- **Day 1 afternoon**: Task 3 (metrics) + testing
- **Day 1 end**: Documentation + review

---

## Open Questions

1. **Rebuild cadence**: Should we use Option A (every N rounds) or Option B (threshold-based)?
   - Recommendation: Start with Option A (simpler), move to Option B if needed

2. **Validation sample size**: How many merges to validate per round?
   - Recommendation: 10-20 for small datasets, 30-50 for large datasets

3. **Precision threshold**: What precision is "good enough"?
   - Recommendation: Target >85% precision; investigate if <75%

4. **Recall proxy**: How to estimate recall without ground truth?
   - Recommendation: Use "new merges per round" as proxy; should decrease toward zero at convergence

---

## Files Modified

### Code Changes
- `scripts/curation/agents/prompts/curation_prompt.yaml` (Task 1)
- `scripts/curation/run_curation_loop.py` (Task 2)
- `scripts/curation/common/neighbor_builder.py` (Task 2, new function)
- `scripts/curation/common/decision_logging.py` (Task 3)
- `scripts/curation/reporting/morning_report.py` (Task 3)

### Documentation Changes
- `doc/plan/experience-curation-refine.md` (updated with results)
- `doc/curation-validation.md` (new, Task 3)

### Configuration Changes
None (all changes are via CLI flags)

---

## Success Checklist

Before marking Phase 1 complete:
- [ ] Prompt includes concrete atomic/non-atomic examples
- [ ] `--rebuild-neighbors-cadence` flag implemented
- [ ] Neighbors rebuild at configured intervals
- [ ] Morning report shows merge rate
- [ ] Morning report shows precision (when validation data exists)
- [ ] Validation workflow documented
- [ ] Baseline vs. Phase 1 comparison complete
- [ ] At least 20 merge decisions manually validated
- [ ] Findings documented in parent plan
- [ ] All code merged to main branch
- [ ] All tests passing
