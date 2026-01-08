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
- Member exports via the Web UI only (zip with `experiences.csv`, `skills.csv`).
- Export from `chl.db` when `CHL_SKILLS_ENABLED=true`.
 - External SKILL.md inputs must include YAML frontmatter with `name` and `description` (see plan 4).
- Each export must include `author` and `source` so curation can attribute ownership.

## Member export workflow (before curation)
Each team member exports their local skills into `data/curation/members/<user>/` using the **Web UI only**:
1. **Web UI**: `/operations` → "Export for Curation" → downloads `{username}_export.zip`.

**Export requirements**:
- `skills.csv` must include `author` (member username) and `source` (e.g., `imported_<user>`).
- If experiences are enabled, include `experiences.csv` in the same export package.

**When `CHL_SKILLS_ENABLED=true`**:
- Export includes skills from the local `chl.db`.

**When `CHL_SKILLS_ENABLED=false`**:
- Export pulls skills from ChatGPT/Claude local folders instead of `chl.db`.
- Source folders must be normalized into `skills.csv` during export (one row per skill, content populated).
- Folder conventions:
  - ChatGPT skills: `~/.codex/skills/` (one `{skill_name}/SKILL.md` per skill).
  - Claude skills: `<workspace>/skills/` or repo-specific skill directories (one `{skill_name}/SKILL.md` per skill).
- `category_code` is left NULL (or omitted) in `skills.csv`.
- During merge, if `category_code` is NULL/missing, send the skill content plus the full category definitions to the LLM and ask it to assign the best-fit category. Populate `metadata["chl.category_code"]` and `metadata["chl.category_confidence"]` from the result.

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
   - This step must complete before step 4 begins.
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

## Model configuration
- **LLM (outline/atomicity/merge/split)**: Use the same model as experience curation unless overridden in config.
- **Embedding model**: Use the same embedding model as experience curation (configured in `scripts/scripts_config.yaml`).
- **Reranker model**: Use the same reranker model as experience curation when available.

## Error handling and recovery
- **LLM failures**: Retry up to 3x with exponential backoff; if still failing, skip the item and log to `error.csv`.
- **Rate limits**: Batch requests and respect provider limits; pause/retry on 429s.
- **Partial success**: Persist progress checkpoints per step; resume from last completed step.
- **Reranker unavailable**: Fall back to embedding-only similarity.
- **Embedding service unavailable**: Abort the current step and mark run as failed; retry after service recovery.

## Rollback snapshots
- Before auto-apply (step 5), snapshot the curation DB:
  - `data/curation/chl_curation.db` → `data/curation/chl_curation_pre_apply.db`
- Keep last 3 snapshots for rollback.

## Category validation
- Category taxonomy is defined in plan 0 and must be in sync across team members.
- On import/mapping:
  - If `metadata["chl.category_code"]` exists and is invalid: flag for manual review.
  - If missing: run LLM category mapping and store `metadata["chl.category_confidence"]`.

## CHL_SKILLS_ENABLED enforcement
- **Primary gate**: `scripts/curation/common/overnight_all.py` skips skills workflow when disabled.
- **Secondary gate**: skills scripts exit gracefully if disabled.
- **API/MCP**: return error or no-op for skill operations when disabled.

## Cost and scale notes
- For N skills, outlines + atomicity + relationship analysis can be expensive (O(N*K)).
- Use batching and thresholds; cap cross-category fallback to the lowest-confidence 10%.
- If >10% are low-confidence, process the bottom 10% by confidence and queue the rest for manual review.

## Field conflict handling (non-curated fields)
- Fields `license`, `compatibility`, `allowed_tools`, `model` are pass-through.
- On merge, prefer non-null values; if both differ, keep one and log conflicts in `metadata["chl.merge.field_conflicts"]`.

## Team notification and cadence
- After exporting `skills.csv`, notify the team (e.g., Slack/email) with summary stats and links to `skill_curation_report.md`.
- Recommended cadence: sprint-end curation every 2 weeks.
- Freeze window: 24h before curation run; export deadline communicated to the team.

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
- **SQL schema (split provenance)**:
```sql
CREATE TABLE skill_split_provenance (
  id INTEGER PRIMARY KEY,
  source_skill_id TEXT NOT NULL,
  split_skill_id TEXT,
  split_group_id TEXT NOT NULL,
  decision TEXT NOT NULL,           -- split, atomic, error
  decision_id TEXT,
  curator TEXT NOT NULL,
  timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  model TEXT,
  prompt_path TEXT,
  raw_response TEXT,
  FOREIGN KEY (source_skill_id) REFERENCES category_skills(id),
  FOREIGN KEY (split_skill_id) REFERENCES category_skills(id)
);
```
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
- Match by `name` first, then fall back to outline embedding similarity:
  - >= 0.90: treat as same skill.
  - 0.85–0.89: flag for manual review.

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
2. **Decision log**: `data/curation/skill_decisions_log.csv` with all merge/split/keep actions.
3. **Curation report**: `data/curation/skill_curation_report.md` with statistics and notable actions.

**skills.csv columns (minimum)**:
- Required: `name`, `description`, `content`
- Optional CHL: `category_code` (if present, must match local taxonomy)
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
**Name mismatch handling**:
- Exact match is preferred (case-insensitive).
- If no match: use outline embedding similarity >= 0.95 plus a small edit-distance check (<=2) for safe auto-match; otherwise flag for manual selection.

## Decisions
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

## Implementation checklist
**Step 1: Collect and merge**
- [x] Add Web UI export option that produces `{username}_export.zip` with `skills.csv` and `experiences.csv`.
- [x] Exporter: when `CHL_SKILLS_ENABLED=false`, normalize ChatGPT/Claude SKILL.md folders into `skills.csv`.
- [x] Merge: stamp `author` and `source` from member directory name when missing.
- [x] Merge: if `category_code` missing, send skill content + category list to LLM and populate `metadata["chl.category_code"]` + `metadata["chl.category_confidence"]`.
  Verify:
  - UI: Operations → Export CSV → confirm `{username}_export.zip` contains `{username}/skills.csv`.
  - CLI: `python scripts/curation/common/merge_all.py --inputs data/curation/members/alice data/curation/members/bob`

**Step 2: Generate outlines**
- [x] Outline generation and storage in `metadata["chl.outline"]`.
  Verify:
  - `python scripts/curation/experience/merge/import_to_curation_db.py --db-path data/curation/chl_curation.db`
  - Inspect any imported skill in DB; confirm `metadata` JSON includes `chl.outline`.

**Step 3: Atomicity pass**
- [x] Atomicity split pass with immediate outline regeneration.
  Verify:
  - `python scripts/curation/skills/prepass/atomicity_split_prepass.py --db-path data/curation/chl_curation.db --limit 5 --dry-run`
  - Run without `--dry-run` to apply and confirm splits/sync_status updates.

**Step 4: Candidate grouping**
- [x] Candidate grouping with cross-category fallback rules.
  Verify:
  - `python scripts/curation/skills/merge/build_skill_candidates.py --db-path data/curation/chl_curation.db`
  - Confirm `data/curation/skill_candidates.jsonl` exists and has records.

**Step 5: LLM relationship analysis (auto-apply)**
- [x] LLM relationship analysis with auto-apply thresholds.
- [x] Post-apply validation (name/description constraints) and provenance metadata.
  Verify:
  - `python scripts/curation/skills/merge/analyze_relationships.py --db-path data/curation/chl_curation.db --limit 10`
  - Confirm `data/curation/skill_decisions_log.csv` is populated and merged/split skills created.

**Step 6–8: Export, distribute, resolve**
- [x] Export `skills.csv`, `skill_decisions_log.csv`, and `skill_curation_report.md`.
  Verify:
  - `python scripts/curation/skills/export_curated.py --db-path data/curation/chl_curation.db`
  - Confirm files exist under `data/curation/approved/`.

**Data/migrations and runtime safeguards**
- [x] Add `skill_curation_decisions` and `skill_split_provenance` tables (migration).
- [x] Add checkpoints, retry/backoff, and snapshot rollback before auto-apply.
- [x] Enforce `CHL_SKILLS_ENABLED` gates in `overnight_all.py` and skills scripts.
  Verify:
  - Run migration on a test DB; confirm tables exist.
  - Run relationship analysis with `--dry-run` and simulate failure to confirm resume/rollback behavior.
  - Run `python scripts/curation/common/overnight_all.py` with `CHL_SKILLS_ENABLED=false` and confirm skills steps are skipped.
