# Skill Curation Plan

## Goal
Merge, split, and distribute skills after import. Low volume implies LLM-assisted manual review, but skills can also be included in the shared overnight workflow with experiences.
If `CHL_SKILLS_ENABLED=false`, skip the entire skill curation loop (no skill export/import, no skill decisions).

## Inputs
- Member exports via `GET /api/v1/entries/export-csv` (zip with `experiences.csv`, `skills.csv`).
- Export from `chl.db`.

## Database topology
- **Main database**: `data/chl.db` - user's working database for daily operations.
- **Curation database**: `data/curation/chl_curation.db` - isolated environment for Carlos to merge/split/curate.
- Skills flow: `chl.db` → export → curation DB → curate → Sheets → import → `chl.db`.
- Curation DB is temporary; curated results published back to team via Sheets.

## Process
1. Collect and merge
   - Place member exports under `data/curation/members/<user>/`.
   - Run `scripts/curation/common/merge_all.py` to merge experiences + skills into curation DB.
   - Curation DB now contains all member skills and experiences (isolated from production).
2. Generate outlines
   - For each skill without outline: LLM generates structured outline (same format as import).
   - Store outline in `metadata.chl.outline`.
3. Candidate grouping
   - Group skills by `category_code`.
   - Within each category: generate embeddings for outlines.
   - For each skill: search top-K candidates using outline embedding.
   - K = min(30, category_size - 1) to handle variable category sizes.
   - Rerank candidates with reranker model on outline pairs.
4. LLM relationship analysis
   - For each skill pair in reranked candidates (if score >= threshold):
     - LLM classifies relationship: subsumption, overlap, complement, distinct, conflict.
     - LLM proposes action: merge, keep_separate, split, flag_conflict.
     - If merge proposed: LLM generates merged content.
   - Thresholds: >=0.85 auto-analyze, 0.70-0.84 flag for review, <0.70 skip.
5. Human review
   - Carlos reviews LLM decisions in interactive CLI or web UI.
   - Accept: Apply LLM's merge/split/keep decision.
   - Edit: Modify merged content before applying.
   - Override: Change relationship classification and action.
   - Flag: Mark conflict for team retrospective discussion.
6. Apply decisions
   - Merge: Create new skill with merged content, mark originals `sync_status=2` (superseded).
   - Split: Create multiple focused skills, mark original `sync_status=2`.
   - Keep separate: No action, both skills remain `sync_status=1`.
   - Conflict: Both skills set `sync_status=0`, flag for team decision.
7. Export curated skills
   - Query skills with `sync_status=1` from curation DB.
   - Generate `skills.csv` for Sheets import.
   - Generate decision log (CSV) and curation report (Markdown).
8. Distribute to team
   - Carlos uploads `skills.csv` to Google Sheets.
   - Team reviews in sprint retrospective.
   - Team imports via `/operations` → updates each member's `chl.db`.

## Outline format
Same as import pipeline (LLM-generated):
```
Purpose: [1-2 sentence summary]
Key Steps: [numbered list of main procedures]
Inputs: [required information/resources]
Outputs: [expected deliverables/outcomes]
Constraints: [limitations, requirements, edge cases]
Examples: [brief example scenarios if applicable]
```

## Reranking strategy
- **Input to reranker**: Outline pairs, not full content.
- **Top-K per skill**: K = min(30, category_size - 1).
- **Rationale**: Outlines are concise (100-200 tokens) vs full content (1000+ tokens); keeps comparisons fast and stable.
- **Fallback**: If reranker unavailable, use embedding similarity only.

## LLM merge prompt
When LLM proposes merge (high overlap detected):
```
Merge these two skills into one comprehensive skill:

Skill A (by {author_a}):
Name: {name_a}
Description: {description_a}
Content: {content_a}

Skill B (by {author_b}):
Name: {name_b}
Description: {description_b}
Content: {content_b}

Requirements:
1. Preserve all unique information from both skills.
2. Remove redundancies (don't repeat same content).
3. Organize logically by topic, not by source.
4. Maintain technical accuracy.
5. Keep distinct examples from both.
6. Note conflicting information in [Alternatives] section if present.

Output merged skill:
Name: [synthesized name]
Description: [synthesized description]
Content: [merged content with clear sections]

Metadata:
- Original authors: {author_a}, {author_b}
- Source skill IDs: {id_a}, {id_b}
- Merge reasoning: [brief explanation]
```

## Data model extensions
- **Experience curation decisions**: rename table to `experience_curation_decisions` to avoid collisions with future skill tables.
- **Curation decisions for skills**: Create new `skill_curation_decisions` table:
  - `decision_id`, `skill_a_id`, `skill_b_id`, `relationship`, `action`, `confidence`.
  - `curator`, `timestamp`, `model`, `prompt_path`, `raw_response`.
- **Split provenance**: Create `skill_split_provenance` table:
  - `source_skill_id`: Original skill before split.
  - `split_skill_id`: New focused skill after split.
  - `split_group_id`: Links all skills from same split operation.
  - `decision_id`, `curator`, `timestamp`, `model`, `prompt_path`, `raw_response`.
- **Outline storage**: Use `metadata.chl.outline` in `skills`.

## Conflict resolution
**Scenario 1: Both authors modified global skill**
- Cannot do 3-way merge (no versioning).
- LLM performs 2-way merge on current versions from Alice and Bob.
- If significant conflicts detected: flag for manual review.
- Carlos resolves by accepting one version, merging manually, or escalating to team.

**Scenario 2: Category mismatch**
- Alice: `MNL-TMG-110` "API Documentation Standards"
- Bob: `MNL-PGS-210` "API Endpoint Documentation"
- LLM re-analyzes against category descriptions.
- Recommends primary category; Carlos makes final decision.
- If truly fits both: create cross-references or split into category-specific aspects.

**Scenario 3: Complete contradiction**
- Alice: "Always use Redux"
- Bob: "Avoid Redux"
- LLM detects conflict, sets `sync_status=0` for both.
- Flag for team retrospective decision.
- Team chooses approach or documents both as alternatives.

## Outputs
1. **Curated skills CSV**: `data/curation/approved/skills.csv` for Sheets import.
2. **Decision log**: `data/curation/skill_curation_decisions.csv` with all merge/split/keep actions.
3. **Curation report**: `data/curation/skill_curation_report.md` with statistics and notable actions.

## Overnight run (combined)
Carlos runs the combined overnight workflow (experiences + skills) with one command:
```
python scripts/curation/common/overnight_all.py
```

Notes:
- This wrapper calls the existing experience overnight flow and the new skills overnight flow in sequence.
- If either domain fails, the wrapper reports per-domain status and continues (configurable).
4. If `CHL_SKILLS_ENABLED=false`: no skill outputs are produced.

## sync_status transitions
During curation:
- `1` (SYNCED) → `1`: Kept unchanged.
- `1` (SYNCED) → `2` (SUPERSEDED): Replaced by merge or split.
- `1` (SYNCED) → `0` (PENDING): Conflict detected, needs team decision.

After team import:
- All curated skills set to `sync_status=1` (active).

## Open questions
1. **Cross-category skills**: How to handle skills that span multiple categories?
   - Option A: Choose primary category, add cross-references.
   - Option B: Split into category-specific aspects.
   - Recommendation: Flag for manual decision during curation.

2. **Attribution granularity**: Should merged skills track which sections came from which author?
   - Current: Track original authors in metadata.
   - Enhancement: Add inline attribution markers in content.
   - Recommendation: Keep simple; metadata attribution sufficient.

3. **Local edits reconciliation**: If Alice modifies curated skill locally before next curation, how to merge?
   - Without versioning: Cannot detect what changed.
   - Recommendation: Treat as new local skill; include in next curation cycle.

## Success criteria
- Curation completes in under 2 hours per sprint (excluding retrospective).
- Merge accuracy: >95% (no team complaints about lost content).
- Conflict detection: All true conflicts surfaced (no silent overwrites).
- Zero information loss: All unique content preserved in merge operations.
- Team satisfaction: "Curation improves shared knowledge without burden."
