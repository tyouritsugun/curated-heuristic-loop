# Phase 0 Refinement — Fixing Trust Issues in Member Exports

## Problem: Untrustworthy sync_status from Individual Local Databases

### Current Issue
Team members export data from their individual local databases with `sync_status` values (0=PENDING, 1=SYNCED, 2=REJECTED) that may not be comparable across members:

- Alice's `sync_status=1` may come from an outdated canonical baseline
- Bob's `sync_status=1` may come from a newer canonical baseline  
- Same `sync_status` values represent different points in time/versions
- No verification that the `sync_status` actually reflects the current canonical state
- Creates false confidence in data consistency during merge

### Root Cause
The curation workflow assumes that individual member's `sync_status` values are meaningful and consistent, but:
1. Team members may load data at different times from canonical sheet
2. Local modifications may happen without proper sync operations
3. Same `sync_status` values mean different things in different contexts
4. No way to validate whether a member's sync_status is current

### Solution: Remove sync_status from Member Exports

#### Phase 0 Refinement Plan
1. **Remove sync_status from member CSV exports** - eliminate the trust issue at the source
2. **Reset sync_status during merge** - all imported entries become `sync_status=0` (PENDING) for curation
3. **Track export lineage separately** - record when/which canonical version each member exported from
4. **Let curation workflow set authoritative sync_status** - only after proper review and merge decisions

#### Implementation Changes Required

**1. Modify member export process:**
- Remove `sync_status` from required columns in member exports
- Add export metadata tracking (when exported, canonical version at time of export)

**2. Update merge_exports.py:**
- Remove `sync_status` from required column validation
- Do not import `sync_status` from member exports
- Instead, all entries get `sync_status=0` during import to curation DB

**3. Update import_to_curation_db.py:**
- Set `sync_status=0` for all imported entries (pending curation)
- Do not preserve source `sync_status` values

**4. Update test data generation (Phase 0):**
- Generate test data without relying on `sync_status` values
- Focus test scenarios on content similarity and curation decisions rather than status-based logic

**5. Adjust curation workflow scripts (Phase 1):**
- `find_pending_dups.py` will only process entries with `sync_status=0`
- Let curation decisions (`merge`, `keep`, `reject`) set final `sync_status` values
- `export_curated.py` filters appropriately based on curation decisions

#### Migration Path
1. Update Phase 0 test data generation to not rely on `sync_status` from members
2. Modify export scripts to stop including `sync_status` in member exports
3. Update curation scripts to handle `sync_status` reset during import
4. **Note**: No backward compatibility support will be provided. All member exports must follow the new schema without `sync_status`. Any existing exports with `sync_status` will need to be regenerated.

#### Benefits
- Eliminates trust issues between team member exports
- Ensures consistent baseline for curation workflow
- Makes the curation process the authoritative source of truth for `sync_status`
- Simplifies validation and reduces potential for merge conflicts
- Maintains data integrity throughout the curation pipeline

#### Important Note on Backward Compatibility
**No backward compatibility support will be provided** for the old schema that includes `sync_status` in member exports. This is a clean break approach where:
- All existing member export CSVs with `sync_status` will be invalid
- All team members must regenerate their exports following the new schema
- The curation pipeline will only accept exports without the `sync_status` field
- Any attempt to use old exports will result in schema validation failure

#### Expected Outcome
- All member exports contribute equally to curation regardless of their local `sync_status`
- Clean separation between individual local databases and team curation process
- Reliable, verifiable `sync_status` values set only after proper review
- Robust foundation for Phase 1 curation workflow