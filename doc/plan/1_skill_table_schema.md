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
  allowed_tools TEXT,                     -- Comma-separated: "Read, Grep, Glob, Bash"
  model TEXT,                             -- Model preference: "claude-sonnet-4-5", etc.

  -- Audit Trail
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

  -- Validation Constraints
  CONSTRAINT name_format CHECK (
    -- Agent Skills Standard: lowercase kebab-case, 1-64 chars
    LENGTH(name) BETWEEN 1 AND 64 AND
    name = lower(name) AND
    name GLOB '[a-z0-9]*' AND          -- starts with alphanumeric
    name GLOB '*[a-z0-9]' AND          -- ends with alphanumeric
    name NOT GLOB '*--*' AND           -- no consecutive hyphens
    name NOT GLOB '*[^a-z0-9-]*'       -- only allowed chars
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
- **allowed_tools**: Comma-separated list (Claude Code only)
- **model**: Model preference (Claude Code only)

#### Design Decisions
1. **Dual identity**: `id` (internal) + `name` (export)
2. **Category internal**: Keep in DB, don't export to frontmatter
3. **Metadata as JSON**: Flexible, avoids schema changes
4. **Content purity**: Store markdown only, generate frontmatter on export
5. **Platform isolation**: Optional fields for platform-specific features

#### Codex Compatibility Note
Codex follows the Agent Skills Standard for `name` and `description` constraints.
We enforce the Agent Skills Standard limits in DB and validate on export.

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

### Phase 3: MCP Updates (LLM-assisted updates)
Enable MCP write/update for new fields so an LLM can draft `name`/`description`
directly in CHL during the review cycle.
Required changes:
- MCP `create_entry`/`update_entry` must accept `name`, `description`, and optional standard fields
- MCP `read_entries` should support lightweight responses (name/description only) for review workflows

### Phase 4: Human Review (Sheets/Excel)
**Critical: Human review required**

For each skill, craft a `description` that includes:
- What the skill does (capabilities)
- When to use it (triggers)
- Keywords users naturally say

Example:
- Bad: "Checklist for page specifications"
- Good: "Generate UI page specifications with user goals, journeys, data dependencies, and accessibility notes. Use when writing page specs, creating UI documentation, or when the user asks to document a web page or screen."

### Phase 5: Import Reviewed Data
1. Import reviewed Sheet/Excel into CHL
2. Validate `name` and `description` (length, format, uniqueness)
3. Reject invalid rows with clear remediation report

### Phase 6: Validation
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

### Write payload validation
- Enforce kebab-case `name` (1-64 chars) and description length (1-1024).
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

## 7) Export to SKILL.md Files
Export creates `{name}/SKILL.md` with:
- YAML frontmatter generated from DB fields
- Markdown body from `content`

Directory structure:
```
{base}/{name}/SKILL.md
```

Optional directories (future):
```
{base}/{name}/
  SKILL.md
  scripts/
  references/
  assets/
```

### Field Transformations (DB -> YAML)
- `allowed_tools` -> `allowed-tools`
- `category_code` -> `metadata.chl.category_code`
- `metadata` JSON string -> YAML key-value pairs under `metadata`

### Example Export Output
Database record:
```json
{
  "id": "SKL-PGS-20250104-103045",
  "name": "page-spec-checklist",
  "description": "Generate UI page specifications...",
  "content": "# Instructions\n\n...",
  "category_code": "PGS",
  "license": "MIT",
  "allowed_tools": "Read, Grep, Glob",
  "model": "claude-sonnet-4-5",
  "metadata": "{\"author\": \"CHL Team\", \"version\": \"1.0\"}"
}
```

Generated SKILL.md:
```
---
name: page-spec-checklist
description: Generate UI page specifications...
license: MIT
allowed-tools: Read, Grep, Glob
model: claude-sonnet-4-5
metadata:
  author: CHL Team
  version: "1.0"
  chl:
    category_code: PGS
---

# Instructions

...
```

## 8) Import from SKILL.md Files
Import reads `{name}/SKILL.md` and extracts:
- Frontmatter → `name`, `description`, optional fields
- Markdown body → `content`

Validation rules:
- Directory name must match `name`
- Required fields present (`name`, `description`, `content`)
- `name` format and uniqueness enforced

Optional subdirectories are ignored in v1 but reserved for future use.

### Parsing Details
1. YAML frontmatter extraction:
   - Parse YAML between `---` markers
   - Validate required fields: `name`, `description`
2. Field mapping (YAML -> DB):
   - `allowed-tools` -> `allowed_tools`
   - `metadata.chl.category_code` -> `category_code`
   - Other `metadata.*` -> JSON string in `metadata`
3. Validation:
   - Directory name must exactly match `name`
   - `name` must pass kebab-case validation
   - `description` length 1-1024 chars
   - If `category_code` missing, require user to provide a default or run category mapping
4. Error handling:
   - Invalid YAML -> reject with parse error
   - Missing required fields -> reject with clear message
   - Directory/name mismatch -> reject with correction prompt

## 9) Progressive Disclosure (MCP/API)
To support large skill sets and fast startup:
- Discovery should return **only** `name` + `description` (and `id` if needed)
- Full `content` should be fetched on-demand
- Avoid sending large bodies in list views to reduce token usage and latency

## 10) Export Compatibility Matrix
| Field           | Standard Export | Claude Code Export | Notes                               |
|----------------|------------------|--------------------|-------------------------------------|
| name           | ✅               | ✅                 | Required                            |
| description    | ✅               | ✅                 | Required                            |
| content        | ✅               | ✅                 | Markdown body                       |
| license        | ✅               | ✅                 | Optional                            |
| compatibility  | ✅               | ✅                 | Optional                            |
| metadata       | ✅               | ✅                 | Optional                            |
| allowed-tools  | ⚪               | ✅                 | Experimental in standard; comma-separated |
| model          | ❌               | ✅                 | Claude Code only                    |
| category_code  | ❌               | ❌                 | CHL internal                        |
| id             | ❌               | ❌                 | CHL internal                        |

## 11) Success Criteria

- Schema: Valid names (kebab-case), descriptions (1-1024 chars), no duplicates, valid categories
- Integration: MCP tools enforce constraints, queries work, round-trip successful
- Breaking change: No backward compatibility, migration script required

## 12) Dependencies & Risks

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

## 13) Timeline

- Phase 1: Schema review (1 day) - done
- Phase 2: Draft + export scripts (2 days)
- Phase 3: MCP updates (1 day)
- Phase 4: Human review (1 day)
- Phase 5: Import + validation (1 day)
- Phase 6: Testing (1 day)
- **Total: ~7 days**

## 14) Open Questions

1. **Category subdirectories?** Start flat, add later if needed
2. **Multi-file skills?** Phase 2 feature (scripts/, references/, assets/)
3. **Version control?** Use metadata.version initially
4. **Soft delete?** Not in v1

## 15) Clarifications

1. **Multi-file skills (import/export)**  
   - v1: Import/export only `SKILL.md`. Ignore `scripts/`, `references/`, `assets/` with a warning.  
   - Future: Add bundle mode that copies optional directories.

2. **Metadata format**  
   - Storage: JSON string in DB `metadata` field.  
   - Export: Convert to YAML key-value under `metadata`.  
   - Special keys:  
     - `short-description`: Optional user-facing summary (Codex compatible).  
     - `chl.*`: Reserved namespace for CHL-internal fields.  
   - Note: `description` is separate and always required.

3. **Category in export**  
   - Standard exports (Agent Skills / Claude / Codex): do **not** include `category_code`.  
   - CHL roundtrip exports: include `metadata.chl.category_code` to preserve internal mapping.

## 16) References

- Agent Skills Standard: https://agentskills.io/specification
- Claude Code Skills: https://code.claude.com/docs/en/skills
- OpenAI Codex Skills: https://developers.openai.com/codex/skills/
- Agent Skills GitHub: https://github.com/agentskills/agentskills
