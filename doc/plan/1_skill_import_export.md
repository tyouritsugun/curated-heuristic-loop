# Skill Import/Export Plan

## Goal
Import external skills (Claude Code, Codex) into CHL and export skills for team curation and distribution.

**⚠️ Phase Requirement**: This entire phase requires `CHL_SKILLS_ENABLED=true`. The import pipeline depends on CHL database for storing processed metadata (outlines, categories, embeddings) needed for duplicate detection and team curation. Users with `ENABLED=false` cannot participate in skill import or curation.

## Inputs
- Claude Code: `~/.claude/skills/` or `.claude/skills/` with `SKILL.md` per directory.
- Codex: `~/.codex/skills/` with JSON skill files.
- Note: formats can vary; parser should be tolerant.

## Import pipeline
1. Discover and parse
   - Detect source type and extract title, content, summary, tags, and raw metadata.
2. Generate outline
   - LLM generates structured outline from content using scripts/curation/agents/prompts/skill_outline_generation.yaml
   - Outline format (defined in prompt):
     ```
     Purpose: [1-2 sentence summary]
     Key Steps: [numbered list of main procedures]
     Inputs: [required information/resources]
     Outputs: [expected deliverables/outcomes]
     Constraints: [limitations, requirements, edge cases]
     Examples: [brief example scenarios if applicable]
     ```
3. Category mapping
   - LLM chooses best-fit category from complete predefined category list using scripts/curation/agents/prompts/skill_category_mapping.yaml
   - Input: skill title, content, outline + all category codes/names/descriptions.
   - LLM must pick one category (no UNCAT fallback); chooses nearest match.
   - Thresholds: >=0.90 auto-assign, 0.70-0.89 flag for review, <0.70 require manual override.
   - If category not found in local taxonomy, import must block and emit a remediation list.
4. Duplicate screening
   - Generate embedding for outline (not full content) to focus on conceptual similarity and reduce noise from implementation details.
   - Search existing skills in target category using outline embedding.
   - Rerank candidates using reranker on outline pairs.
   - Threshold-based workflow:
     * Score >=0.85: Feed both skill contents to LLM for merge decision (scripts/curation/agents/prompts/skill_merge_decision.yaml)
     * Score 0.70-0.84: Flag as potential duplicate for manual review
     * Score <0.70: Treat as distinct skill
   - LLM returns: should_merge (bool), confidence, reasoning, merged_content (if applicable).
   - Fallback to title/keyword similarity in CPU mode.
5. Review bundle
   - Emit review file (CSV or JSON) with proposed category, confidence, duplicate/merge hints, outline preview, and source path.
6. Import
   - Insert into `category_skills` with generated id.
   - Store outline in `summary` field.
   - Set `embedding_status=pending`, `source=imported_claude|imported_codex`, `author` from OS user.
   - Set `sync_status` (see sync_status semantics below).
7. Post-import
   - Rebuild embeddings for new skills.
   - Queue for skill curation if multiple members have contributed.

## Source-of-truth modes
User selects one mode at setup (default: Option A for Claude/ChatGPT users):

**Option A: CHL is source-of-truth** (default, `CHL_SKILLS_MODE=chl`)
- Enable CHL skill tools in MCP (full read/write).
- Imported skills set `sync_status=1` (synced/active).
- User creates/edits skills via MCP → stored in `chl.db`.
- Export for curation: from `chl.db` to curation CSV.
- Bidirectional export: CHL → external formats (for sharing with external tool users).

**Option B: External is source-of-truth** (Claude Code or Codex, `CHL_SKILLS_MODE=external`)
- CHL skill tools in MCP provide read-only access.
- Imported skills set `sync_status=0` (pending/draft).
- User creates/edits skills in external tool only.
- CHL database acts as **read-only cache** for curation processing.
- Export for curation: from `chl.db` (cached copy) to curation CSV.
  - Before export: Re-import from external source to sync latest changes.
- Bidirectional export: CHL curation results → external formats (round-trip).

**Configuration** (see doc/config/skills_access_control.md for details):
- `CHL_SKILLS_ENABLED=true|false` (default: `true`, from Phase 0).
  - When `false`: All skill operations blocked (read, write, import, export, curation).
  - When `true`: Skills enabled, access controlled by CHL_SKILLS_MODE.
- `CHL_SKILLS_MODE=chl|external` (default: `chl`).
  - When `chl` (Option A): Full read/write access via MCP, CHL is source-of-truth.
  - When `external` (Option B): Read-only MCP access, external tool is source-of-truth.

## Export paths

**For curation** (member exports):
- Option A users (MODE=chl): `GET /api/v1/entries/export-csv` from `chl.db` → produces `categories.csv`, `experiences.csv`, `skills.csv`.
- Option B users (MODE=external): Export from `chl.db` (already imported skills), not re-parsed from external source.
  - Rationale: Skills already imported during setup; export reflects current CHL state.
  - If external source modified: Re-import first, then export.
- All exports go to `data/curation/members/<user>/` for merge.

**For team distribution** (after curation):
- Curated skills exported via `/operations` to Google Sheets.
- Team imports via `/operations` → updates `chl.db`.
- Option B users: Also export from Sheets to external format for manual sync.

## sync_status semantics (legacy field)
`sync_status` values for skills:
- `0` (PENDING): Imported but not yet reviewed/approved; or draft/local-only skill.
- `1` (SYNCED): Active, approved skill available for search/retrieval.
- `2` (REJECTED/SUPERSEDED): Skill replaced by merge, split, or manual deletion; kept for audit trail.

**Workflow by mode**:
- **Option A (chl mode)**: Imported skills → `sync_status=1` (active immediately).
  - User creates/edits via MCP → always `sync_status=1`.
  - After team curation: Curated skills remain `sync_status=1`.
- **Option B (external mode)**: Imported skills → `sync_status=0` (pending).
  - Skills stay `sync_status=0` until team curation completes.
  - After team curation: Update approved skills to `sync_status=1`.
  - Rationale: External source is primary; CHL copy is secondary until team validates.

Note: This is a legacy field inherited from experiences. For skills, it primarily distinguishes active (1) from inactive (0, 2).

## Category governance
- Categories are team-owned and published via shared Sheets by team admin (Carlos).
- Categories must be complete and predefined before import.
- LLM always chooses nearest category from existing list (no UNCAT).
- If new category needed: Admin adds to category list → publishes to Sheets → users sync via `/operations` → re-import skills with new category.
- Recommendation: Start with comprehensive category taxonomy covering all expected skill domains.
- **Category operations**:
  - **Creation**: Admin-only (via category management interface)
  - **Export to Sheets**: Admin-only (publishes team taxonomy)
  - **Import/sync from Sheets**: All users (downloads published taxonomy)
  - **Skill mapping**: All users (map skills to existing categories during import)

## Required system changes
- ✅ Add `CHL_SKILLS_MODE` env var (completed in config.py).
- ✅ Add `CHL_SKILLS_ENABLED` env var (completed in Phase 0).
- ✅ Create LLM prompt templates (completed in scripts/curation/agents/prompts/).
- ✅ Document toggle hierarchy (completed in doc/config/skills_access_control.md).
- Add outline generation step to import pipeline (LLM call using scripts/curation/agents/prompts/skill_outline_generation.yaml).
- Store outline in `category_skills.summary` field.
- Disable MCP skill write tools when `skills_mode=external` (update handlers to check config.skills_write_allowed()).
- Gate skill import/export based on `skills_enabled` flag.
- Validate category codes during import; emit actionable remediation if missing.
- Implement LLM-based merge decision for high-similarity duplicates (using scripts/curation/agents/prompts/skill_merge_decision.yaml).
- Add bidirectional converters (implementation details in separate spec).
- Implement category mapping with confidence thresholds (using scripts/curation/agents/prompts/skill_category_mapping.yaml).

## Risks and constraints
- LLM outline generation requires network access; cache results.
- LLM merge decisions add latency; make async with progress indicator.
- Category completeness critical; incomplete taxonomy forces bad mappings.
- Bidirectional export requires format compatibility; may lose CHL-specific metadata.

## Success criteria
- Import external skills in under 5 minutes (excluding LLM merge time).
- Outline generation quality: >90% of outlines accurately capture skill structure.
- Category mapping accuracy: >90% for high-confidence assignments.
- Duplicate detection: >85% recall on true duplicates, <10% false positive rate.
- No data loss on round-trip export/import (CHL → external → CHL).
