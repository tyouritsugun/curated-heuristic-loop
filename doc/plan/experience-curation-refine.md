# Experience Curation Refine (Idea Draft)

## Goal
Improve curation stability and quality by emphasizing **atomic experiences** and by reconsidering how neighbor rebuilding and reranking are used across rounds.

## Core hypothesis
- The biggest source of drift is **non-atomic experiences** (entries that bundle multiple tactics, steps, or topics).
- If experiences are atomic, communities become more stable and merge decisions converge faster.
- Reranking and neighbor rebuild cadence can help, but they are secondary to data quality.

## Clarify “atomic” (working definition)
An experience is atomic if it:
- Encodes **one actionable idea** or decision rule.
- Can be reused independently without extra context from other parts of the entry.
- Does not bundle multiple distinct tactics, steps, or outcomes.

Non-atomic signals:
- Multiple tactics in one entry (e.g., “Do X and Y and Z”).
- Mixed goals or unrelated subtopics in the same playbook.
- One entry describes a full workflow that could be split into independent steps.

## Risks we want to avoid
- Over-merge: collapsing many experiences into one because they share theme-level similarity.
- Drift: community composition changes too much between rounds, reducing convergence.
- Over-fragmentation: splitting too aggressively and losing context.

## Proposed refinement ideas
### 1) Prompting for atomicity
Add explicit guidance to the LLM prompt to **preserve atomicity**:
- Reject merges that would create multi-topic or multi-step entries.
- Prefer **split suggestions** when an entry appears non-atomic.
- Provide a short rubric + examples of atomic vs non-atomic.

Potential output extension:
- Add a `split_suggested` list with rationale (requires schema change if we choose to adopt it).

### 2) Neighbor rebuild cadence (no code yet)
Current behavior: communities are rebuilt each round using cached neighbors (no re-embedding).

Idea options:
- **Option A:** Rebuild neighbors every round (higher recall, higher cost, more churn).
- **Option B:** Rebuild neighbors every N rounds (e.g., every 2–3 rounds).
- **Option C:** Rebuild only if merge rate exceeds a threshold (e.g., >5% of items merged).

Clarifications for Phase 1:
- **Placement:** Rebuild after merges are applied and before re-detecting communities.
- **Threshold:** Use the same `--edge-threshold` as graph construction (no new threshold flag in Phase 1).
- **Cadence start point:** Start with a fixed cadence (e.g., every 2 rounds), then revisit a merge-rate trigger later.

### 3) Rerank placement
Idea: use rerank after an initial embed-only pass, to increase precision once broad clusters form.

Possible workflow:
1. Round 0: embed-only neighbors → LLM merge/keep.
2. Rebuild neighbors with rerank → LLM merge/keep.
3. Continue with rerank-based neighbors until convergence (or revert to embed-only if cost is too high).

## Evaluation plan (lightweight)
To decide if the changes help:
- Track merge precision via a small manual sample.
- Track merge yield via “new merges per round” (explicitly not recall).
- Track stability via number of communities and overlap between rounds.
- Track cost/time per round (rerank impact).

## Open questions
- Do we optimize for **precision** (fewer false merges) or **recall** (more duplicates caught)?
- Typical dataset size and GPU budget? (rerank costs scale quickly.)
- Should we introduce a “split suggestion” output path now, or first just add atomicity guidance?

## Implementation Status (as of 2025-12-30)

### What's Already Implemented
- ✅ Atomicity guidance in prompt (minimal, needs enhancement)
- ✅ Reranker client fully integrated with caching
- ✅ Two-pass capability (manual via `--two-pass` flag)
- ✅ Evaluation log with `was_correct` field (not analyzed)
- ✅ Community detection and priority scoring
- ✅ Early stopping based on improvement threshold

### What's Missing
- ❌ Concrete atomicity examples and rubric in prompt
- ❌ Automatic neighbor rebuild cadence (always cached)
- ❌ Orchestrated two-pass rerank workflow
- ❌ Split suggestion mechanism in LLM response
- ❌ Merge quality tracking (precision/recall)
- ❌ Community stability metrics

Notes for Phase 1 metrics:
- Add `round_index` to evaluation log to enable per-round precision.
- Use active item count (pending items) as the merge-rate denominator.

---

## Implementation Phases

### Phase 1: Quick Wins (high impact)
**Goal:** Test core hypotheses with minimal code changes

**Tasks:**
1. **Enhance atomicity prompt**
   - Add 2-3 concrete examples of atomic vs. non-atomic experiences
   - Add explicit complexity rule: "If merging makes entry >2x longer, prefer keep_separate"
   - Add guidance: "Note entries that bundle multiple independent tactics"
   - File: `scripts/curation/agents/prompts/curation_prompt.yaml`

2. **Implement neighbor rebuild cadence**
   - Add `--rebuild-neighbors-cadence N` flag to `run_curation_loop.py`
   - Rebuild neighbors every N rounds OR when merge_rate > threshold
   - Compare graph changes before/after to measure impact
   - Files: `scripts/curation/run_curation_loop.py`, `scripts/curation/common/neighbor_builder.py`

3. **Add merge quality tracking**
   - Track precision/recall metrics per round
   - Add to morning report: "Round N: precision X%, recall Y%"
   - Store in evaluation log for trend analysis
   - Files: `scripts/curation/common/decision_logging.py`, `scripts/curation/reporting/morning_report.py`

**Success criteria:**
- Atomicity examples added to prompt
- Neighbors rebuild at least once per multi-round loop
- Morning report shows merge quality metrics
- Can measure before/after impact on merge decisions

**Risks:**
- Neighbor rebuild may not improve recall if embedding quality is poor
- Tracking precision requires manual validation (sample-based)

### Phase 2: Orchestration (medium complexity)
**Goal:** Automate two-pass workflow and enable split suggestions

**Tasks:**
1. **Orchestrate two-pass rerank workflow**
   - Auto-detect when to switch to rerank (e.g., merge_rate > 10% in round 1)
   - Rebuild communities with rerank automatically
   - Compare cost vs. precision gain
   - Files: `run_curation_loop.py`, `build_communities.py`

2. **Add split_suggested to LLM response schema**
   - Extend response: `{"decision": "...", "merges": [], "split_suggested": [...]}`
   - Update validation in `prompt_utils.py`
   - Log split suggestions to evaluation log
   - Files: `scripts/curation/agents/prompts/curation_prompt.yaml`, `scripts/curation/agents/prompt_utils.py`

3. **Add community stability metrics**
   - Compute Jaccard similarity of community membership between rounds
   - Track number of communities and avg overlap per round
   - Use as convergence signal (e.g., "overlap > 95% for 2 rounds → converged")
   - Files: `scripts/curation/common/convergence.py` (new), `morning_report.py`

**Success criteria:**
- Two-pass workflow runs automatically based on merge rate
- LLM can flag entries for potential splits
- Community stability tracked in morning report

**Risks:**
- Rerank may be too expensive for large datasets
- Split suggestions may have high false positive rate

### Phase 3: Evaluation & Iteration (ongoing)
**Goal:** Validate hypotheses and refine approach

**Tasks:**
1. **Run A/B experiment**
   - Baseline: Current system (cached neighbors, no rerank)
   - Variant A: Phase 1 changes (rebuild cadence, enhanced prompt)
   - Variant B: Phase 1 + Phase 2 changes (orchestrated rerank)
   - Measure: precision, recall, convergence rounds, cost

2. **Manual validation** (ongoing)
   - Sample 20-30 merge decisions per round
   - Mark as correct/incorrect in evaluation log
   - Calculate precision per round
   - Identify patterns in false positives/negatives

3. **Document findings**
   - Which strategy works best for different dataset sizes?
   - Cost/benefit tradeoff of rerank
   - Optimal neighbor rebuild cadence
   - Update this plan with recommendations

**Success criteria:**
- Clear data on which refinements improve quality
- Cost/benefit analysis of rerank documented
- Recommendations for production deployment

---

## Next steps (proposal)
- **Immediate:** Implement Phase 1 (quick wins)
- **Short-term:** Run small experiment to validate improvements
- **Medium-term:** Implement Phase 2 if Phase 1 shows promise
- **Ongoing:** Manual validation and iteration
