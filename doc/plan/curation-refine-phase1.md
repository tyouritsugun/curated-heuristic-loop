# Experience Curation Refinement - Phase 1 (Concise)

## Goal
Improve curation quality and stability with minimal code changes by tightening atomicity guidance, refreshing neighbors during multi-round loops, and adding lightweight quality metrics.

## Scope
- Prompt improvements for atomicity (examples + clarity)
- Neighbor rebuild cadence during multi-round curation
- Merge quality metrics in reporting

## Task 1: Atomicity Prompt
**Why:** Current guidance is too vague.
**Change:** Add 2–3 atomic and non-atomic examples plus an explicit complexity rule.
**File:** `scripts/curation/agents/prompts/curation_prompt.yaml`
**Done when:** Examples included; complexity rule is explicit.

## Task 2: Neighbor Rebuild Cadence
**Why:** Cached neighbors go stale after merges.
**Change:** Add a rebuild cadence flag and refresh neighbors at configured intervals.
**Files:** `scripts/curation/overnight/run_curation_loop.py`, `scripts/curation/common/neighbor_builder.py`
**Done when:** Flag works; rebuilds occur on schedule; logs show old/new edge counts.

## Task 3: Merge Quality Metrics
**Why:** No feedback loop on merge quality.
**Change:** Add merge rate and optional precision (when validation exists) to reporting.
**Files:** `scripts/curation/common/decision_logging.py`, `scripts/curation/reporting/morning_report.py`
**Done when:** Morning report shows merge rate and precision if available.

## Clarifications (tightened)
- **Rebuild placement:** After merges are applied and before communities are re-detected for the next round.
- **Cadence choice:** Start with fixed `--rebuild-neighbors-cadence 2` (every other round). Add merge-rate-based rebuild later if needed.
- **Threshold naming:** Use existing `--edge-threshold` consistently for graph construction; neighbor rebuild should use the same effective threshold (no new threshold flag in Phase 1).
- **Merge rate denominator:** Use the active item count for the round (`pending_now` from `compute_counts`), not total historical items.
- **Evaluation log round context:** Add and populate `round_index` so precision can be computed per round.

## Metrics (Phase 1)
- Merge rate per round (using active item count)
- Precision from validated samples (if any)
- Merge yield per round: “new merges per round” (explicitly not recall)

## Open Questions
- None blocking Phase 1. Revisit cadence heuristics after baseline metrics.

## Risks
- Rebuild cost may be too high on large datasets
- Prompt examples may over-constrain merges
- Precision depends on manual validation

## Success Criteria
- Prompt includes atomic/non-atomic examples and a clear complexity rule
- Neighbors rebuild at least once in a multi-round run
- Morning report shows merge rate and precision (if validation data exists)

## Next Step
Proceed with Phase 1 changes; revisit Phase 2 only after metrics show improvement.
