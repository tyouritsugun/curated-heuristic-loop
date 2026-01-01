# Skills Access Control

## Overview

CHL provides two environment variables to control skill access:
- `CHL_SKILLS_ENABLED`: Master toggle for all skill operations
- `CHL_SKILLS_MODE`: Source-of-truth mode when skills are enabled


## Configuration Hierarchy

### Priority Rules

Skills access is determined by a **two-level hierarchy**:

1. **First check**: `CHL_SKILLS_ENABLED`
   - If `false`: **All skill operations blocked** (both read AND write)
   - If `true`: Proceed to level 2

2. **Second check**: `CHL_SKILLS_MODE` (only when `ENABLED=true`)
   - If `chl`: **Full access** (read + write)
   - If `external`: **Read-only** (write blocked)

### Configuration Matrix

| CHL_SKILLS_ENABLED | CHL_SKILLS_MODE | Read | Write | Import/Export | Use Case |
|-------------------|-----------------|------|-------|---------------|----------|
| `false` | *(ignored)* | ❌ | ❌ | ❌ | Skills completely disabled |
| `true` | `chl` | ✅ | ✅ | ✅ | CHL is source-of-truth (default) |
| `true` | `external` | ✅ | ❌ | ✅ | External tool is source-of-truth |

**Note**: Skill import/export (Phase 1) requires `CHL_SKILLS_ENABLED=true` for all operations.

## Environment Variables

### CHL_SKILLS_ENABLED
- **Type**: Boolean
- **Default**: `true`
- **Values**: `true` | `false`
- **Purpose**: Master kill switch for skills feature

**When to use `false`**:
- Team doesn't use skills at all
- Individual hasn't started using skills yet
- Reduce MCP tool surface area
- Performance optimization (skip skill queries)

**When set to `false`**:
- All skill-related MCP tools hidden
- Cannot import/export skills (Phase 1 unavailable)
- Use only if you're certain you won't need skills

### CHL_SKILLS_MODE
- **Type**: String
- **Default**: `chl`
- **Values**: `chl` | `external`
- **Purpose**: Define source-of-truth for skill management

**`chl` mode** (Option A):
- CHL database is the source-of-truth
- Users create/edit skills via CHL MCP tools
- Imported skills: `sync_status=1` (active)
- Export: From `chl.db` → curation DB
- Bidirectional: CHL → external formats

**`external` mode** (Option B):
- External tool (Claude Code, Codex) is source-of-truth
- CHL provides read-only access via MCP
- Users create/edit skills in external tool only
- Imported skills: `sync_status=0` (pending)
- Export: From `chl.db` (cached copy) → curation CSV
- Bidirectional: Curation results → external formats

## Implementation Details

### Config Helper Methods

```python
# In src/common/config/config.py
def skills_read_allowed(self) -> bool:
    """True when skill read operations are allowed."""
    return self.skills_enabled

def skills_write_allowed(self) -> bool:
    """True when skill write operations (create/update) are allowed."""
    return self.skills_enabled and self.skills_mode == "chl"
```

### MCP Handler Behavior

**Read operations** (`read_entries` with `entity_type='skill'`):
- If `config.skills_read_allowed()` is `False`: Return error "Skills are disabled"
- Otherwise: Proceed with read

**Write operations** (`create_entry`, `update_entry` with `entity_type='skill'`):
- If `config.skills_write_allowed()` is `False`: Return error
  - If `skills_enabled=false`: "Skills are disabled"
  - If `skills_mode=external`: "Skills are read-only in external mode"
- Otherwise: Proceed with write

## Curation Participation

### Requirement
To participate in team skill curation, **you MUST have `CHL_SKILLS_ENABLED=true`**.

### Why?
The curation process requires:
1. **Outline generation** (LLM processing)
2. **Category mapping** (LLM + category taxonomy)
3. **Duplicate detection** (embeddings + semantic search)
4. **Team merge** (all members' skills in shared format)

These operations require skills to be in CHL database with processed metadata.

### Workflow by Mode

**CHL Mode** (`MODE=chl`):
```
1. Create/edit skills via MCP → chl.db
2. Export curation: chl.db → CSV
3. Team merge & curate
4. Import results: CSV → chl.db
```

**External Mode** (`MODE=external`):
```
1. Create/edit skills in Claude Code/ChatGPT
2. Re-import to CHL: External files → chl.db (sync cache)
3. Export curation: chl.db → CSV (from cache)
4. Team merge & curate
5. Import results: CSV → chl.db
6. Export to external: chl.db → External files (round-trip)
```

**Disabled** (`ENABLED=false`):
```
❌ No curation participation
```

### Future Enhancement
Future versions may support direct export from external sources when `ENABLED=false`, but this requires solving:
- Where to store processed metadata (outlines, categories, embeddings)
- How to avoid re-processing on every export
- How to merge with team members who use CHL

## Common Scenarios

### Scenario 1: Fresh Installation (Default)
```bash
# No env vars set - uses defaults
CHL_SKILLS_ENABLED=true (default)
CHL_SKILLS_MODE=chl (default)
```
**Result**: Full skill access via MCP ✅

### Scenario 2: Disable Skills Entirely
```bash
export CHL_SKILLS_ENABLED=false
# CHL_SKILLS_MODE is ignored
```
**Result**:
- All skill operations blocked ❌
- Cannot participate in team curation ⚠️
- Use when: Team doesn't use skills or you're not participating

### Scenario 3: Claude Code User (External Source)
```bash
export CHL_SKILLS_ENABLED=true
export CHL_SKILLS_MODE=external
```
**Result**:
- Read-only MCP access ✅📖
- Edit skills in Claude Code
- CHL database caches for curation
- Can participate in team curation ✅
- Must re-import before exporting to sync changes

### Scenario 4: CHL Power User
```bash
export CHL_SKILLS_ENABLED=true
export CHL_SKILLS_MODE=chl
```
**Result**: Full CRUD via MCP ✅✏️

## Migration Guide

### Switching from External → CHL Mode

1. Export current skills from external tool
2. Import into CHL database
3. Update config: `CHL_SKILLS_MODE=chl`
4. Update imported skills: `sync_status` 0 → 1
5. Restart MCP server

### Switching from CHL → External Mode

1. Export skills from CHL
2. Convert to external format
3. Import into external tool
4. Update config: `CHL_SKILLS_MODE=external`
5. Restart MCP server

## Validation

Config validation ensures only valid values are accepted:

```python
# Raises ValueError if invalid
valid_modes = ("chl", "external")
if self.skills_mode not in valid_modes:
    raise ValueError(
        f"Invalid CHL_SKILLS_MODE='{self.skills_mode}'. "
        f"Must be one of: {', '.join(valid_modes)}"
    )
```

## See Also

- [Skill Import/Export Plan](../plan/1_skill_import_export.md)
- [Skill Curation Preparation](../plan/0_skill_curation_prepare.md)
