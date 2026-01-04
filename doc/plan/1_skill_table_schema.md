# Skill Table Schema Refactoring (Plan 1)

## Goals
- Align CHL skill schema with the open Agent Skills Standard (https://agentskills.io)
- Maintain platform-agnostic design with optional platform-specific features
- Break backward compatibility for cleaner, standards-compliant schema

## 0) Prerequisites and dependencies

### Execution order
1. This plan must complete before implementing skill import/export (plan 2)
2. Category taxonomy expansion (plan 0, section 2) should complete first
3. Skills must be enabled via config (plan 0, section 3)

### External dependencies
- Database migration capability for `chl.db`
- Manual description generation for existing skills (cannot be auto-generated)
- Understanding of Agent Skills Standard specification

### Breaking changes
- **Complete schema redesign** - no backward compatibility
- Existing skill data requires migration and manual description authoring
- `content` field no longer includes YAML frontmatter (pure markdown only)

## 1) Current State Analysis

### Current Schema
- Fields: `id`, `title`, `content`, `entity_type`, `category_code`, `created_at`, etc.
- Storage: SQLite via MCP
- Access: MCP tools (read_entries, create_entry)

### Problems
1. No `name` field (cannot map to directory names)
2. No `description` field (no auto-selection trigger)
3. `title` not identifier-safe (spaces, capitals, special chars)
4. Missing platform fields (`allowed-tools`, `model`)
5. Missing standard fields (`license`, `compatibility`, `metadata`)

## 2) Three-Way Standards Comparison

### Universal Required Fields (All Platforms)

| Field | Agent Skills Standard | Claude Code | OpenAI Codex |
|-------|----------------------|-------------|--------------|
| **name** | ✅ Required (1-64 chars, kebab-case) | ✅ Required | ✅ Required |
| **description** | ✅ Required (1-1024 chars) | ✅ Required (max 1024) | ✅ Required |
| **Directory name** | Must match `name` | Must match `name` | Must match `name` |
| **Body format** | Markdown (no restrictions) | Markdown (keep <500 lines) | Markdown |

### Optional Fields

| Field | Agent Skills Standard | Claude Code | OpenAI Codex |
|-------|----------------------|-------------|--------------|
| **license** | ⚪ Optional | ❌ Not mentioned | ❌ Not mentioned |
| **compatibility** | ⚪ Optional (1-500 chars) | ❌ Not mentioned | ❌ Not mentioned |
| **metadata** | ⚪ Optional (key-value) | ❌ Not mentioned | ⚪ Has `short-description` |
| **allowed-tools** | ⚪ Optional (experimental) | ⚪ Optional | ❌ Not mentioned |
| **model** | ❌ Not mentioned | ⚪ Optional | ❌ Not mentioned |

## 3) Proposed Unified Schema

### Database Schema (SQLite)

```sql
CREATE TABLE skills (
  -- Primary Identity
  id TEXT PRIMARY KEY,                    -- Internal CHL ID (e.g., "SKL-PGS-20250104-...")
  name TEXT NOT NULL UNIQUE,              -- Kebab-case identifier, 1-64 chars (e.g., "page-spec-checklist")

  -- Required Content (Agent Skills Standard)
  description TEXT NOT NULL,              -- Keyword-rich trigger description, 1-1024 chars
  content TEXT NOT NULL,                  -- Markdown body (no YAML frontmatter)

  -- CHL Organization (internal only, not exported)
  category_code TEXT NOT NULL,            -- "PGS", "DSD", "GLN", etc.

  -- Optional Standard Fields (Agent Skills Standard)
  license TEXT,                           -- License identifier: "MIT", "Apache-2.0", etc.
  compatibility TEXT,                     -- Environment requirements (1-500 chars)

  -- Metadata (JSON for flexible key-value storage)
  metadata TEXT,                          -- JSON: {"short-description": "...", "author": "...", ...}

  -- Platform-Specific Features
  allowed_tools TEXT,                     -- Space-delimited: "Read Grep Glob Bash"
  model TEXT,                             -- Model preference: "claude-sonnet-4-5", etc.

  -- Audit Trail
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

  -- Validation Constraints
  CONSTRAINT name_format CHECK (
    -- Agent Skills Standard: lowercase kebab-case, 1-64 chars
    LENGTH(name) BETWEEN 1 AND 64 AND
    name = lower(name) AND
    name NOT LIKE '-%' AND
    name NOT LIKE '%-' AND
    name NOT LIKE '%--%' AND
    name NOT LIKE '%[^a-z0-9-]%'
  ),
  CONSTRAINT description_length CHECK (
    LENGTH(description) BETWEEN 1 AND 1024
  ),
  CONSTRAINT compatibility_length CHECK (
    compatibility IS NULL OR LENGTH(compatibility) BETWEEN 1 AND 500
  ),
  CONSTRAINT content_not_empty CHECK (
    LENGTH(TRIM(content)) > 0
  ),
  FOREIGN KEY (category_code) REFERENCES categories(code)
);

-- Indexes for Performance
CREATE INDEX idx_skills_category ON skills(category_code);
CREATE INDEX idx_skills_name ON skills(name);
CREATE INDEX idx_skills_updated ON skills(updated_at DESC);

-- Full-text Search (if needed)
CREATE VIRTUAL TABLE skills_fts USING fts5(
  name,
  description,
  content,
  content=skills,
  content_rowid=rowid
);
```

### Field Specifications

#### Required Fields
- **id**: Internal CHL ID (e.g., `SKL-PGS-20250104-103045123456`), auto-generated
- **name**: Kebab-case identifier (1-64 chars), matches directory name, e.g., `page-spec-checklist`
- **description**: Keyword-rich trigger (1-1024 chars), includes what/when/keywords users say
- **content**: Pure markdown instructions (no frontmatter), <500 lines recommended
- **category_code**: Internal category (e.g., "PGS"), not exported

#### Optional Fields
- **license**: License identifier (e.g., "MIT")
- **compatibility**: Environment requirements (1-500 chars)
- **metadata**: JSON key-value (short-description, author, version, tags)
- **allowed_tools**: Space-delimited list (Claude Code only)
- **model**: Model preference (Claude Code only)

#### Design Decisions
1. **Dual identity**: `id` (internal) + `name` (export)
2. **Category internal**: Keep in DB, don't export to frontmatter
3. **Metadata as JSON**: Flexible, avoids schema changes
4. **Content purity**: Store markdown only, generate frontmatter on export
5. **Platform isolation**: Optional fields for platform-specific features

#### Codex Compatibility Note (Single-line + 500 char)
Codex requires `name` (<=100 chars, single line) and `description` (<=500 chars, single line).
We keep the Agent Skills Standard constraints in DB (<=1024 chars), but export must enforce
Codex limits by either:
- Hard validation on export (error if description > 500 or contains newlines), or
- Sanitizing by collapsing newlines and truncating to 500 chars with a warning.
For simplicity and cross-tool consistency, prefer **hard validation** for Codex export.

## 4) Migration Strategy

### Phase 1: Schema Migration
1. Backup existing skills table
2. Drop old table and create new schema (see section 3)
3. Restore data with transformations

### Phase 2: Draft Generation + Export for Review
1. Generate draft `name` from `title` (kebab-case conversion)
2. Generate draft `description` from `title` + `content` (AI-assisted or templated)
3. Handle name collisions (append category code or number suffix)
4. Export to Sheets/Excel for human review (see Phase 3)

### Phase 3: Human Review (Sheets/Excel)
**Critical: Human review required**

For each skill, craft a `description` that includes:
- What the skill does (capabilities)
- When to use it (triggers)
- Keywords users naturally say

Example:
- Bad: "Checklist for page specifications"
- Good: "Generate UI page specifications with user goals, journeys, data dependencies, and accessibility notes. Use when writing page specs, creating UI documentation, or when the user asks to document a web page or screen."

### Phase 4: Import Reviewed Data
1. Import reviewed Sheet/Excel into CHL
2. Validate `name` and `description` (length, format, uniqueness)
3. Reject invalid rows with clear remediation report

### Phase 5: Validation
Check before deployment:
- Valid names (kebab-case, 1-64 chars)
- Descriptions present (no TODOs, 1-1024 chars)
- No duplicate names
- Valid category codes
- Non-empty content

## 5) MCP API Changes

### create_skill
New required parameters: `name`, `description`, `content`, `category_code`
New optional parameters: `license`, `compatibility`, `metadata`, `allowed_tools`, `model`

### update_skill
All new fields can be updated

### read_entries (skills output contract)
Skill outputs should match Agent Skills Standard fields:
- Required: `name`, `description`, `content`
- Optional: `license`, `compatibility`, `metadata`, `allowed_tools`, `model`
- CHL internal fields may be included but must not replace standard fields:
  `id`, `category_code`, `source`, `author`, `sync_status`, timestamps.

If we keep `title`/`summary` for legacy reasons, they must be derived aliases:
- `title` → `name`
- `summary` → `description`
But do not emit both unless legacy compatibility is explicitly required.

### Write payload validation
- Enforce kebab-case `name` (1-64 chars) and description length (1-1024).
- Ensure description is single-line if targeting Codex export.
- Reject unknown category codes.

## 6) Sheets/Excel Columns (skills)
To keep UI import/export aligned with the standard:

**Skills sheet columns (export):**
- Required: `name`, `description`, `content`, `category_code`
- Optional standard: `license`, `compatibility`, `metadata`, `allowed_tools`, `model`
- CHL internal: `id`, `source`, `author`, `sync_status`, `embedding_status`, `created_at`, `updated_at`, `synced_at`, `exported_at`

**Import acceptance:**
- Accept legacy columns `title`/`summary` but map to `name`/`description`.
- Reject if `name` is missing after mapping.

## 7) Success Criteria

- Schema: Valid names (kebab-case), descriptions (1-1024 chars), no duplicates, valid categories
- Integration: MCP tools enforce constraints, queries work, round-trip successful
- Breaking change: No backward compatibility, migration script required

## 8) Dependencies & Risks

### Dependencies
- Category taxonomy (plan 0, section 2)
- Skills config flag (plan 0, section 3)
- Manual description authoring

### Risks
- Name collisions → Manual resolution with suffix
- Poor descriptions → Manual review required
- Format drift → Monitor platform docs
- Strict constraints → Document overrides
- Lost metadata → Preserve backups

## 9) Timeline

- Phase 1: Schema review (1 day) - done
- Phase 2: Draft + export scripts (2 days)
- Phase 3: Human review (1 day)
- Phase 4: Import + validation (1 day)
- Phase 5: MCP updates (1 day)
- Phase 6: Testing (1 day)
- **Total: ~7 days**

## 10) Open Questions

1. **Category subdirectories?** Start flat, add later if needed
2. **Multi-file skills?** Phase 2 feature (scripts/, references/, assets/)
3. **Version control?** Use metadata.version initially
4. **Soft delete?** Not in v1

## 11) References

- Agent Skills Standard: https://agentskills.io/specification
- Claude Code Skills: https://code.claude.com/docs/en/skills
- OpenAI Codex Skills: https://developers.openai.com/codex/skills/
- Agent Skills GitHub: https://github.com/agentskills/agentskills
