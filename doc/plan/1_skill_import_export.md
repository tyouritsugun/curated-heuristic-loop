# Skill Import/Export Plan

## Goal
Import external skills (Claude Code, Codex) into CHL and export skills for team curation and distribution.

## Inputs
- Claude Code: `~/.claude/skills/` or `.claude/skills/` with `SKILL.md` per directory.
- Codex: `~/.codex/skills/` with JSON skill files.
- Note: formats can vary; parser should be tolerant.

## Import pipeline
1. Discover and parse
   - Detect source type and extract title, content, summary, tags, and raw metadata.
2. Generate outline
   - LLM generates structured outline from content.
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
   - LLM chooses best-fit category from complete predefined category list.
   - Input: skill title, content, outline + all category codes/names/descriptions.
   - LLM must pick one category (no UNCAT fallback); chooses nearest match.
   - Thresholds: >=0.90 auto-assign, 0.70-0.89 flag for review, <0.70 require manual override.
   - If category not found in local taxonomy, import must block and emit a remediation list.
4. Duplicate screening
   - Generate embedding for outline (not full content).
   - Search existing skills in target category using outline embedding.
   - Rerank candidates using reranker on outline pairs.
   - If top match score >=0.85, feed both skill contents to LLM for merge decision.
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

**Option A: CHL is source-of-truth** (default)
- Enable CHL skill tools in MCP.
- Imported skills set `sync_status=1` (synced/active).
- User creates/edits skills via MCP → stored in `chl.db`.
- Export for curation: from `chl.db` to curation DB.
- Bidirectional export: CHL → external formats (for sharing with external tool users).

**Option B: External is source-of-truth** (Claude Code or Codex)
- Disable CHL skill tools in MCP (read-only access).
- Imported skills set `sync_status=0` (pending/draft).
- User creates/edits skills in external tool only.
- Export for curation: from external source (re-import) to curation DB.
- Bidirectional export: CHL curation results → external formats (round-trip).

**Configuration**:
- Add env var: `CHL_SKILLS_MODE=chl|external` (default: `chl`).
- When `external`: MCP skill write tools return "read-only" error.
- Add env var: `CHL_SKILLS_ENABLED=true|false` (default: `true`).
- When `false`: disable skill read/write/retrieve via MCP and block skill import/export entirely.

## Export paths

**For curation** (member exports):
- Option A users: `GET /api/v1/entries/export-csv` from `chl.db` → produces `categories.csv`, `experiences.csv`, `skills.csv`.
- Option B users: Re-parse external skills → export to CSV format.
- All exports go to `data/curation/members/<user>/` for merge.
- If `CHL_SKILLS_ENABLED=false`: skip skill export (no `skills.csv`).

**For team distribution** (after curation):
- Curated skills exported via `/operations` to Google Sheets.
- Team imports via `/operations` → updates `chl.db`.
- Option B users: Also export from Sheets to external format for manual sync.
- If `CHL_SKILLS_ENABLED=false`: skip skill sheets entirely in both import and export.

## sync_status semantics (legacy field)
`sync_status` values for skills:
- `0` (PENDING): Imported but not yet reviewed/approved; or draft/local-only skill.
- `1` (SYNCED): Active, approved skill available for search/retrieval.
- `2` (REJECTED/SUPERSEDED): Skill replaced by merge, split, or manual deletion; kept for audit trail.

Note: This is a legacy field inherited from experiences. For skills, it primarily distinguishes active (1) from inactive (0, 2).

## Category governance
- Categories are team-owned and published via shared Sheets.
- Categories must be complete and predefined before import.
- LLM always chooses nearest category from existing list (no UNCAT).
- If new category needed: Carlos adds to category list → publishes → users sync → re-import with new category.
- Recommendation: Start with comprehensive category taxonomy covering all expected skill domains.
- Category import/export should be removed or admin-only; end users map to existing categories.

## Required system changes
- Add outline generation step to import pipeline (LLM call).
- Store outline in `category_skills.summary` field.
- Add `CHL_SKILLS_MODE` env var and disable MCP skill write tools when `mode=external`.
- Add `CHL_SKILLS_ENABLED` env var and gate skill read/write/retrieve, import/export, and sheet validation.
- Validate category codes during import; emit actionable remediation if missing.
- Implement LLM-based merge decision for high-similarity duplicates.
- Add bidirectional converters (implementation details in separate spec).
- Extend export logic to handle Option B users (re-parse from external source).

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
