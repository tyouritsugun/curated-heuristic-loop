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

### 3) Rerank placement
Idea: use rerank after an initial embed-only pass, to increase precision once broad clusters form.

Possible workflow:
1. Round 0: embed-only neighbors → LLM merge/keep.
2. Rebuild neighbors with rerank → LLM merge/keep.
3. Continue with rerank-based neighbors until convergence (or revert to embed-only if cost is too high).

## Evaluation plan (lightweight)
To decide if the changes help:
- Track merge precision via a small manual sample.
- Track merge recall via “new merges per round”.
- Track stability via number of communities and overlap between rounds.
- Track cost/time per round (rerank impact).

## Open questions
- Do we optimize for **precision** (fewer false merges) or **recall** (more duplicates caught)?
- Typical dataset size and GPU budget? (rerank costs scale quickly.)
- Should we introduce a “split suggestion” output path now, or first just add atomicity guidance?

## Next steps (proposal)
- Draft atomicity rubric + examples for the curation prompt.
- Decide on neighbor rebuild cadence experiment.
- If promising, implement a small A/B test run (baseline vs new cadence/rerank order).
