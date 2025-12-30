# Experience Curation Refinement - Phase 1 (LLM Atomicity Split Pre-pass)

## Goal
Use the existing curation LLM to detect non-atomic experiences, split them, and write results back to the DB before Phase 2.

## Hypothesis
The current reranker is not producing usable separation. A direct LLM split pre-pass will improve merge precision and reduce drift in later rounds.

## Proposed Pipeline
1. **Atomicity classification**: LLM returns `atomic` or `split`.
2. **Split generation**: If split, LLM returns a list of atomic experiences.
3. **DB writeback**: Insert split experiences; mark original as inactive.
4. **Provenance logging**: Record split lineage in a new DB table.
5. **Normal loop**: Run the existing neighbor/community/merge loop (Phase 2).

## LLM Setup (Pilot Spec)
- Use the same LLM configuration as current curation scripts.
- Prompt should:
  - Define atomic vs non-atomic clearly.
  - Ask for a strict JSON output: `{decision: "atomic"|"split", splits: [...]}`.
  - Require each split to be atomic, self-contained, and minimal.

## Safety / Constraints
- Do not invent new steps or tools; only split existing content.
- Keep split count small (2–4) unless clearly needed.
- Preserve category/section from the original.

## Evaluation Plan
- **Sample review**: Manually review 20–30 split results for correctness and usefulness.
- **Metrics**: Split precision (correctly split) and split quality (subjective).
- **Success bar**: ≥ 85% of reviewed splits are judged correct and useful.

## Risks
- Over-splitting could fragment context.
- Under-splitting keeps multi-step entries intact.
- LLM may hallucinate new steps; prompt must forbid this.

## Deliverables
- A pilot script that iterates experiences and performs LLM splitting.
- A new provenance table to record split lineage.
- A short report with split review results and recommendation.

## Next Step After Phase 1
If the pilot passes, keep the atomicity pre-pass and proceed with Phase 2.
