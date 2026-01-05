# Skill Format Compliance Plan (Agent Skills Standard)

## Goal
Ensure CHL import/export of SKILL.md is **fully compatible** with the Agent Skills Standard.
The standard requires **YAML frontmatter** with required fields and a Markdown body.

## Standard Reference
- **Agent Skills Specification**: https://agentskills.io/specification

## Standard Requirements (from agentskills.io)
- File name: `SKILL.md`
- Structure:
  - YAML frontmatter between `---` and `---`
  - Markdown body after frontmatter
- Required fields: `name`, `description`
- `name` must be kebab-case and match directory name
- `description` length constraint (1-1024) for standard
- `allowed-tools` (if present) should be **space-delimited** for standard
- Recommended content length: <500 lines for the Markdown body

## Implementation Status (Verified)
The following are already implemented in code:
- ✅ YAML frontmatter parser + emitter (requires frontmatter, writes frontmatter)
- ✅ Name validation (kebab-case, lowercase, 1–64 chars)
- ✅ Description length validation (1–1024 chars)
- ✅ Directory name matching (configurable via `require_dir_match`)
- ✅ Allowed-tools normalization (comma/space/list)
- ✅ Delimiter handling (Claude = comma, Standard = space)

## Remaining Gaps / Risk Areas
These areas still need documentation alignment or additional behavior:

1) **SKILL.md parsing & emitting**
   - `src/common/skills/skill_md.py`
     - Confirm behavior is documented and aligned with spec.
     - Add explicit validation guidance to docs.

2) **Operations import/export handlers**
   - `src/api/services/operations_service.py`
     - `import-claude`, `export-claude`
     - `import-codex`, `export-codex`
     - Ensure they use YAML frontmatter parser/emitter (not plain markdown body).
     - Ensure standard export uses space-delimited `allowed-tools`.

3) **Web UI expectations**
   - `src/api/templates/common/partials/config_status_card.html`
     - Update UI copy if it implies “markdown only.”
     - Clarify “SKILL.md (YAML frontmatter).”

4) **Docs / Plans**
   - `doc/plan/1_skill_table_schema.md` (SKILL.md import/export sections)
   - `doc/plan/2_skill_import_export.md`
   - `doc/plan/5_skill_curation.md`

5) **Scripts / future tooling (Phase 2)**
   - `scripts/curation/skills/` (directory created, no scripts yet)
     - Future: add import/export scripts that emit YAML frontmatter and validate required fields.

## Progressive Disclosure (MCP/API behavior)
To minimize token usage and support large skill sets:
- **Discovery/listing**: Return only `name` + `description` (no content)
- **Full fetch**: Load `content` only when skill is activated
- **Optional resources**: Load `scripts/`, `references/`, `assets/` on explicit request only

Implementation status:
- [x] MCP `read_entries` supports lightweight mode (name+description only)
- [x] API `/entries/read` supports preview vs full content mode
- [x] API `fields` parameter supports `preview` and explicit field selection
- [ ] Documentation updated with token usage guidance

## Multi-File Skills (Optional Directories)
The standard supports optional subdirectories beyond SKILL.md:
- `scripts/`: executable code referenced by the skill
- `references/`: supplemental documentation
- `assets/`: static resources

Phase 1 (current scope):
- [ ] Import: ignore optional directories with a warning
- [ ] Export: emit only SKILL.md (no subdirectories)
- [ ] Document v1 limitation in UI/API responses

Phase 2 (future):
- [ ] Bundle mode: copy optional directories on export
- [ ] Reference resolution: validate paths in SKILL.md content

## Description Quality Standards
Descriptions should enable agent selection by including:
1. **Capabilities**: What the skill does
2. **Triggers**: When to use it
3. **Keywords**: Terms users naturally say

Examples:
- ❌ "Checklist for page specifications"
- ✅ "Generate UI page specifications with user goals, journeys, data dependencies, and accessibility notes. Use when writing page specs, creating UI documentation, or when the user asks to document a web page or screen."

Validation:
- [x] Length: 1–1024 characters (enforced)
- [ ] Quality: manual review during migration (recommended)
- [ ] Optional strict mode: reject generic descriptions like "Helps with X"

## Implementation Verification (Core Features)
These features are implemented and verified:
- [x] Parser rejects SKILL.md without YAML frontmatter.
- [x] Emitter always includes `name` + `description` in YAML frontmatter.
- [x] Kebab-case and directory-name match enforced on import.
- [x] Standard export uses **space-delimited** `allowed-tools`.
- [x] Claude export uses **comma-delimited** `allowed-tools`.

## Remaining Work (Documentation & Polish)
- [ ] Update UI text to make frontmatter requirement explicit.
- [ ] Update related docs (plan 1, 2, 5) with YAML frontmatter requirement.
- [ ] Implement content >500 lines warning (accept but flag).

## Validation Plan
- Sample SKILL.md files with:
  - Valid YAML frontmatter + markdown body
  - Missing frontmatter (should reject)
  - Empty frontmatter (`---` then `---`) (should reject)
  - Malformed YAML (tabs/unclosed quotes) (should reject with parse error)
  - Frontmatter with only `name` (should reject)
  - Frontmatter with only `description` (should reject)
  - Frontmatter with extra unknown fields (should accept)
  - Invalid name format (should reject)
  - Description length too long (should reject)
- Round-trip: CHL → SKILL.md → CHL should preserve `name`, `description`, `content`, `metadata`.
- Content >500 lines should warn but accept.

## Enhanced Validation Test Cases
### Name Validation Tests
- ✅ Valid: `page-spec-checklist`, `api-documentation`, `db-schema-design`
- ❌ Invalid name format:
  - `Page-Spec` (uppercase) → reject with "must be lowercase"
  - `-page-spec` (leading hyphen) → reject with "cannot start with hyphen"
  - `page-spec-` (trailing hyphen) → reject with "cannot end with hyphen"
  - `page--spec` (consecutive hyphens) → reject with "no consecutive hyphens"
  - `page_spec` (underscore) → reject with "use hyphens not underscores"
- ❌ Invalid length:
  - 65+ character name → reject with "max 64 characters"

### Directory Matching Tests
- ❌ Directory `page-spec` with name `page-spec-checklist` → reject
- ✅ Directory `page-spec-checklist` with name `page-spec-checklist` → accept

### Frontmatter Tests
- ❌ File without `---` delimiters → reject with "Missing YAML frontmatter"
- ❌ Empty frontmatter (`---` then `---`) → reject
- ❌ Malformed YAML (tabs/unclosed quotes) → reject with parse error
- ❌ Frontmatter missing `name` → reject with "Missing required field: name"
- ❌ Frontmatter missing `description` → reject with "Missing required field: description"
- ✅ Frontmatter with `allowed-tools` → accept
- ✅ Frontmatter with `allowed_tools` → accept (compat)
- ✅ Frontmatter with unknown extra fields → accept and preserve

### Allowed-Tools Delimiter Tests
- Input: `allowed-tools: Read Grep Glob` (space-delimited)
  - Standard export → `allowed-tools: Read Grep Glob`
  - Claude export → `allowed-tools: Read, Grep, Glob`
- Input: `allowed_tools: ["Read", "Grep"]`
  - Both exports normalize correctly

### Content Tests
- ❌ Empty markdown body → reject with "Missing required field: content"
- ⚠️  Content >500 lines → warn (recommended limit) but accept
- ✅ Content <500 lines → accept silently

## Implementation Status Summary
| Requirement                        | Status         | Location                        |
|------------------------------------|----------------|---------------------------------|
| YAML frontmatter parser            | ✅ Implemented | src/common/skills/skill_md.py   |
| Name validation (kebab-case, 1-64) | ✅ Implemented | src/common/skills/skill_md.py   |
| Description length (1-1024)        | ✅ Implemented | src/common/skills/skill_md.py   |
| Directory name matching            | ✅ Implemented | src/common/skills/skill_md.py   |
| Allowed-tools normalization        | ✅ Implemented | src/common/skills/normalize.py  |
| Delimiter handling (comma/space)   | ✅ Implemented | src/api/services/operations_service.py |
| Metadata flattening (dot-notation) | ✅ Implemented | src/common/skills/normalize.py  |
| YAML frontmatter emitter           | ✅ Implemented | src/common/skills/skill_md.py   |
| Skills toggle (CHL_SKILLS_ENABLED) | ✅ Implemented | src/api/services/operations_service.py |

## Error Messages Reference
| Scenario | Error Message | Remediation |
|----------|--------------|-------------|
| Missing frontmatter | "Missing YAML frontmatter (expected '---' on first line)" | Add `---` as first line |
| Missing closing delimiter | "Missing closing '---' for YAML frontmatter" | Add `---` after YAML block |
| Missing name field | "Missing required field: name" | Add `name:` to frontmatter |
| Invalid name format | "Skill name must be kebab-case (lowercase letters, digits, hyphens)" | Use format: `my-skill-name` |
| Name too long | "Skill name must be 1-64 characters" | Shorten name to <=64 chars |
| Description too long | "Skill description must be 1-1024 characters" | Shorten description |
| Directory mismatch | "Directory name '{dir}' must match skill name '{name}'" | Rename directory to match |
| Empty content | "Missing required field: content" | Add markdown body after frontmatter |

## Backward Compatibility
**Breaking Change:** YAML frontmatter is now REQUIRED.

**Migration path:**
1. Old skills without frontmatter: import will reject with clear error
2. Users must manually add frontmatter with `name` and `description`
3. No auto-migration in v1 to avoid incorrect field generation

**Future helper (optional):**
- Script to draft `name` from directory name
- Script to draft `description` from first paragraph
- Manual review required before import

## API Field Selection (Progressive Disclosure)
- `fields=None` → full content (backward compatible)
- `fields=["preview"]` → preview only (~320 chars)
- `fields=["content"]` → full content explicitly
- `fields=["content", "license"]` → specific fields only

## Notes
- This plan is *format compliance only*. It does not change schema or curation logic.
- All exports should remain backward-compatible with Claude/Codex by selecting delimiter rules.
