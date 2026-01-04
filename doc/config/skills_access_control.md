# Skills Access Control

## Overview

CHL uses a single environment variable to control skill access:
- `CHL_SKILLS_ENABLED`: Master toggle for all skill operations

When enabled, skills are fully available (read/write/import/export/curation).
When disabled, all skill operations are blocked.

## Configuration

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
- All skill-related MCP operations blocked
- API skill endpoints return 404
- Import/export skip skills (Sheets/Excel/CSV)
- Embeddings/indexing skip skill jobs
- UI hides or disables skill actions with a warning

**When set to `true`**:
- Full skill access (read/write/import/export/curation)

## Implementation Details

### Config Helper Methods

```python
# In src/common/config/config.py

def skills_read_allowed(self) -> bool:
    """True when skill read operations are allowed."""
    return self.skills_enabled

def skills_write_allowed(self) -> bool:
    """True when skill write operations (create/update) are allowed."""
    return self.skills_enabled
```

### MCP Handler Behavior

**Read operations** (`read_entries` with `entity_type='skill'`):
- If `config.skills_read_allowed()` is `False`: Return error "Skills are disabled"
- Otherwise: Proceed with read

**Write operations** (`create_entry`, `update_entry` with `entity_type='skill'`):
- If `config.skills_write_allowed()` is `False`: Return error "Skills are disabled"
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

## Common Scenarios

### Scenario 1: Fresh Installation (Default)
```bash
# No env vars set - uses defaults
CHL_SKILLS_ENABLED=true (default)
```
**Result**: Full skill access via MCP ✅

### Scenario 2: Disable Skills Entirely
```bash
export CHL_SKILLS_ENABLED=false
```
**Result**:
- All skill operations blocked ❌
- Cannot participate in team curation ⚠️
- Use when: Team doesn't use skills or you're not participating

## See Also

- [Skill Import/Export Plan](../plan/2_skill_import_export.md)
- [Skill Curation Preparation](../plan/0_skill_curation_prepare.md)
