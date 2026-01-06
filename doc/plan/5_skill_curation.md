# Skill Curation Plan

## Goal
Merge, split, and distribute skills after import. Low volume implies LLM-assisted manual review, but skills can also be included in the shared overnight workflow with experiences.
If `CHL_SKILLS_ENABLED=false`, skip the entire skill curation loop (no skill export/import, no skill decisions).

## Phases
1. **Member export**: Each member exports skills into `data/curation/members/<user>/` with author/source stamped.
2. **Curation merge**: Carlos merges member exports into the curation DB.
3. **Auto-curation**: Outline → atomicity split → candidate grouping → LLM relationship analysis → auto-apply decisions.
4. **Export & review**: Export curated skills to `skills.csv`; Carlos reviews in Sheets/Excel.
5. **Team publish**: Team imports curated skills; local skills overwrite with curated versions.
6. **Conflict resolution**: Any conflicts are resolved post-retro and reflected in a follow-up export/import.

## Inputs
- Member exports via `GET /api/v1/entries/export-csv` (zip with `experiences.csv`, `skills.csv`).
- Export from `chl.db`.
 - External SKILL.md inputs must include YAML frontmatter with `name` and `description` (see plan 4).
- Each export must include `author` and `source` so curation can attribute ownership.

## Member export workflow (before curation)
Each team member exports their local skills into `data/curation/members/<user>/`:
1. **Web UI**: `/operations` → "Export for Curation" → downloads `{username}_export.zip`.
2. **CLI**: `python scripts/export_for_curation.py --output data/curation/members/<user>/`.
3. **API**: `GET /api/v1/entries/export-csv?entity_type=skill` → save `skills.csv`.

**Export requirements**:
- `skills.csv` must include `author` (member username) and `source` (e.g., `imported_<user>`).
- If experiences are enabled, include `experiences.csv` in the same export package.

## Database topology
- **Main database**: `data/chl.db` - user's working database for daily operations.
- **Curation database**: `data/curation/chl_curation.db` - isolated environment for Carlos to merge/split/curate.
- Skills flow: `chl.db` → export → curation DB → curate → Sheets → import → `chl.db`.
- Curation DB is temporary; curated results published back to team via Sheets.

## Category portability and mapping
- The Agent Skills Standard has no category field; do **not** emit `category_code` in standard SKILL.md exports.
- For CHL roundtrip, preserve category in `metadata["chl.category_code"]`.
- For imports from ChatGPT/Claude (or any external source without category):
  - Run LLM category mapping against the full internal taxonomy.
  - Store `metadata["chl.category_confidence"]` with the mapping confidence.
  - If confidence < 0.70: flag for manual category review (see cross-category fallback).
- `category_code` is optional in the DB; prefer `metadata["chl.category_code"]` for portability.
- `read_entries` should use progressive disclosure:
  - **List**: return only `name`, `description` (and `id` if needed).
  - **Detail**: fetch full `content` on-demand for selected skills.

## Process
1. Collect and merge
   - Place member exports under `data/curation/members/<user>/`.
   - Run `scripts/curation/common/merge_all.py` to merge experiences + skills into curation DB.
   - Merge must stamp `author=<user>` and `source=imported_<user>` if missing (derive `<user>` from directory name).
   - Curation DB now contains all member skills and experiences (isolated from production).
2. Generate outlines
   - For each skill without outline: LLM generates structured outline (same format as import).
   - Store outline in `metadata["chl.outline"]` (flat key).
3. LLM atomicity pass
   - For each skill outline: LLM verifies the skill has a single target/purpose.
   - If non-atomic: LLM proposes a split into atomic skills and generates new content for each.
   - Apply split immediately: create split skills, mark original `sync_status=2` (superseded).
   - Immediately regenerate outline for each split skill (before step 4) and store in `metadata["chl.outline"]`.
4. Candidate grouping
   - Group skills by `category_code`.
   - Within each category: generate embeddings for outlines.
   - For each skill: search top-K candidates using outline embedding.
   - K = min(30, category_size - 1) to handle variable category sizes.
   - If category_size < 2: skip (no candidates).
   - Rerank candidates with reranker model on outline pairs.
   - Cross-category fallback (optional, expensive):
     - Trigger: category mapping confidence < 0.70 or suspected mismatch.
     - Run global top-10 similarity search using outline embeddings.
     - If top match is cross-category with score >=0.85: flag for manual category review.
     - Limit: run fallback for at most 10% of skills to control cost.
5. LLM relationship analysis (auto-apply)
   - For each skill pair in reranked candidates (if score >= threshold):
     - LLM classifies relationship: subsumption, overlap, complement, distinct, conflict.
     - LLM proposes action: merge, keep_separate, split, flag_conflict.
     - If merge proposed: LLM generates merged content.
   - Thresholds: >=0.85 auto-analyze, 0.70-0.84 flag for review, <0.70 skip.
   - Auto-apply decisions:
     - Merge: Create new skill with merged content, mark originals `sync_status=2` (superseded).
     - Split: Create multiple focused skills, mark original `sync_status=2`.
     - Keep separate: No action, both skills remain `sync_status=1`.
     - Conflict: Both skills set `sync_status=0` (pending), flag for team decision.
   - Post-apply validation: enforce `name` uniqueness/format, `description` length (per export target), and regenerate outline for any new/edited skill.
   - Provenance: write merge/split metadata into new skill records (see Data model extensions).
6. Export curated skills
   - Query skills with `sync_status=1` from curation DB.
   - Generate `skills.csv` for Sheets import.
   - Generate decision log (CSV) and curation report (Markdown).
7. Distribute to team
   - Carlos uploads `skills.csv` to Google Sheets or Excel.
   - Carlos reviews results post-export (no inline human review in the pipeline).
   - Team imports via `/operations` → updates each member's `chl.db`.
8. Resolve conflicts (post-retro)
   - Team decision recorded in decision log (`resolution_notes`) and reflected in updated skills.csv.
   - If resolved via merge/split: create the approved skill(s), mark originals `sync_status=2`.
   - If resolved via keep: set both to `sync_status=1` and clear `conflict_flag`.

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
  - `status` (proposed/accepted/edited/overridden), `conflict_flag`, `resolution_notes`.
- **SQL schema (skills curation decisions)**:
```sql
CREATE TABLE skill_curation_decisions (
  decision_id TEXT PRIMARY KEY,
  skill_a_id TEXT NOT NULL,
  skill_b_id TEXT NOT NULL,
  relationship TEXT NOT NULL,       -- subsumption, overlap, complement, distinct, conflict
  action TEXT NOT NULL,             -- merge, keep_separate, split, flag_conflict
  confidence REAL NOT NULL,
  curator TEXT NOT NULL,            -- 'llm' or username
  timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  model TEXT,
  prompt_path TEXT,
  raw_response TEXT,
  status TEXT DEFAULT 'proposed',   -- proposed, accepted, edited, overridden
  conflict_flag INTEGER DEFAULT 0,
  resolution_notes TEXT,
  FOREIGN KEY (skill_a_id) REFERENCES category_skills(id),
  FOREIGN KEY (skill_b_id) REFERENCES category_skills(id)
);
```
- **Split provenance**: Create `skill_split_provenance` table:
  - `source_skill_id`: Original skill before split.
  - `split_skill_id`: New focused skill after split.
  - `split_group_id`: Links all skills from same split operation.
  - `decision_id`, `curator`, `timestamp`, `model`, `prompt_path`, `raw_response`.
- **Outline storage**: Use `metadata["chl.outline"]` in `skills`.
- **Merge provenance (metadata)**: On merged skills, store `metadata["chl.merge.from_ids"]`, `metadata["chl.merge.from_authors"]`, `metadata["chl.merge.reason"]`.
- **Split provenance (metadata)**: On split skills, store `metadata["chl.split.group_id"]`, `metadata["chl.split.source_id"]`.
- **Migration note**: Add a SQLite migration alongside other schema migrations used for plan 1/2 changes.

## Conflict resolution
**Scenario 1: Both authors modified global skill**
- Cannot do 3-way merge (no versioning).
- LLM performs 2-way merge on current versions from Alice and Bob.
- If significant conflicts detected: flag for manual review.
- Carlos resolves by accepting one version, merging manually, or escalating to team.
**Detection rule**:
- Same `name`, `sync_status=1`, and different `updated_at` vs team version.
- Match by `name` first, then fall back to high-similarity match when names diverge.

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

**skills.csv columns (minimum)**:
- Required: `name`, `description`, `content`, `category_code`
- Optional: `license`, `compatibility`, `metadata`, `allowed_tools`, `model`
- Internal/audit: `id`, `author`, `sync_status`, `updated_at`
**Category rule**: `category_code` is included for review/validation but does not change the team taxonomy; it must match the local predefined list.

## Overnight run (combined)
Carlos runs the combined overnight workflow (experiences + skills) with one command:
```
python scripts/curation/common/overnight_all.py
```

Notes:
- This wrapper calls the existing experience overnight flow and the new skills overnight flow in sequence.
- If either domain fails, the wrapper reports per-domain status and continues (configurable).
4. If `CHL_SKILLS_ENABLED=false`: no skill outputs are produced.
**Status**: Experience curation is complete; skills merge/overnight wiring is pending implementation.

## sync_status transitions
During curation:
- `1` (SYNCED) → `1`: Kept unchanged.
- `1` (SYNCED) → `2` (SUPERSEDED): Replaced by merge or split.
- `1` (SYNCED) → `0` (PENDING): Conflict detected or requires team decision.

After team import:
- All curated skills set to `sync_status=1` (active).
## Import reconciliation (member workflow)
After importing curated `skills.csv` into a local `chl.db`:
1. **Curated skills**: set `sync_status=1`, `source=curated`, overwrite existing by `name`.
2. **Superseded skills**: if a local skill was merged/split, set `sync_status=2` and keep for audit.
3. **Unmatched local skills**: keep as-is (user can retain or delete).
4. **Conflicts**: if decision log shows conflict, keep both with `sync_status=0` until resolution.

## Open questions
1. **Cross-category skills**: How to handle skills that span multiple categories?
   - Decision: Choose a primary category, add cross-references as needed.

2. **Attribution granularity**: Should merged skills track which sections came from which author?
   - Current: Track original authors in metadata.
   - Enhancement: Add inline attribution markers in content.
   - Recommendation: Keep simple; metadata attribution sufficient.

3. **Local edits reconciliation**: If Alice modifies curated skill locally before next curation, how to merge?
   - Decision: Overwrite local skill with curated version on team publish/import.

## Success criteria
- Curation completes in under 2 hours per sprint (excluding retrospective).
- Merge accuracy: >95% (no team complaints about lost content).
- Conflict detection: All true conflicts surfaced (no silent overwrites).
- Zero information loss: All unique content preserved in merge operations.
- Team satisfaction: "Curation improves shared knowledge without burden."
