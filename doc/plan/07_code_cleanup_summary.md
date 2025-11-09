# Code Cleanup Summary - Legacy Configuration Removal

**Date:** 2025-11-09
**Context:** Post-Phase 2 cleanup to remove redundant YAML-based configuration paths

## Overview

After implementing Phase 1 (python-dotenv + .env) and Phase 2 (read-only Settings UI), we had redundant configuration paths: environment variables, .env file, YAML config, and Settings database. This cleanup removes the YAML-based credential and sheet ID configuration, keeping .env as the single source of truth for secrets and environment-specific settings.

## Motivation

**Before cleanup:**
- Credentials could be set in: .env → YAML → Settings DB (3 paths)
- Sheet IDs could be set in: .env → YAML (2 paths)
- Confusing priority rules
- Code complexity with fallback chains
- Inconsistent error messages

**After cleanup:**
- Credentials: .env only (GOOGLE_CREDENTIAL_PATH)
- Sheet IDs: .env only (IMPORT_SPREADSHEET_ID, EXPORT_SPREADSHEET_ID)
- Clear, simple error messages
- Reduced code complexity
- Single source of truth

## Changes Made

### 1. Simplified scripts_config.yaml.sample

**Removed:**
- `google_credentials_path` field
- `export.spreadsheet_id` field
- `import.spreadsheet_id` field

**Kept:**
- `data_path` - structural default
- `database_filename` - optional override
- `export.worksheets` - worksheet names (can be overridden via env vars)
- `import.worksheets` - worksheet names (can be overridden via env vars)
- `dry_run` and `verbose` flags

**Updated comments:**
- Clarified that YAML is for structural defaults only
- Directed users to .env.sample for secrets/sheet IDs
- Added notes about environment variable overrides

### 2. Simplified scripts/export.py

**Removed:**
- YAML fallback for `google_credentials_path`
- YAML fallback for `spreadsheet_id`
- `_credentials_path_from_settings()` helper function
- Settings DB fallback for credentials
- Complex priority chain logic

**Changed:**
- Now requires `GOOGLE_CREDENTIAL_PATH` in environment (fails if not set)
- Now requires `EXPORT_SPREADSHEET_ID` in environment (fails if not set)
- Simplified error messages pointing to .env file
- Validates credential file exists before proceeding

**Error messages now say:**
```
Configuration error: GOOGLE_CREDENTIAL_PATH not set in .env file.
Copy .env.sample to .env and set your credentials path.
```

### 3. Simplified scripts/import.py

**Removed:**
- YAML fallback for `google_credentials_path`
- YAML fallback for `spreadsheet_id`
- `_credentials_path_from_settings()` helper function
- Settings DB fallback for credentials
- Import of `SettingsService` (no longer used)

**Changed:**
- Now requires `GOOGLE_CREDENTIAL_PATH` in environment (fails if not set)
- Now requires `IMPORT_SPREADSHEET_ID` in environment (fails if not set)
- Simplified error messages pointing to .env file
- Validates credential file exists before proceeding

### 4. Marked SettingsService Methods as Deprecated

**File:** `src/services/settings_service.py`

**Deprecated methods:**
- `update_credentials()` - Added deprecation notice in docstring
- `load_sheet_config()` - Added deprecation notice in docstring

**Rationale for keeping:**
- `update_credentials()` still used by backup/restore functionality
- `load_sheet_config()` still used by diagnostics probe
- Both methods retained for backward compatibility
- May be removed in future major version

**Deprecation notices:**
```python
DEPRECATED: Credentials should be configured via .env file (GOOGLE_CREDENTIAL_PATH).
This method is retained for backward compatibility with backup/restore functionality.
```

```python
DEPRECATED: Sheet configuration should be managed via .env file:
- GOOGLE_CREDENTIAL_PATH for credentials
- IMPORT_SPREADSHEET_ID and EXPORT_SPREADSHEET_ID for sheet IDs
- IMPORT_WORKSHEET_* and EXPORT_WORKSHEET_* for worksheet names (optional)

This method is retained for backward compatibility with existing deployments
and the diagnostics probe functionality.
```

## Files Modified

1. `scripts/scripts_config.yaml.sample` - Removed credential/sheet ID fields
2. `scripts/export.py` - Removed YAML/DB fallbacks, simplified validation
3. `scripts/import.py` - Removed YAML/DB fallbacks, simplified validation
4. `src/services/settings_service.py` - Added deprecation notices

## Files Requiring Future Updates

### doc/manual.md

**Status:** Not updated in this cleanup (out of scope)

**Requires comprehensive rewrite:**
- Multiple references to `scripts_config.yaml` for credentials
- Instructions to set `google_credentials_path` in YAML
- Export/import workflow documentation references YAML
- Environment variables section describes YAML as preferred path

**Recommendation:**
Create a separate task to update manual.md after Phase 3 is complete. The manual should reflect the final workflow:
1. Copy .env.sample to .env
2. Run scripts/setup.py
3. Use web UI Operations page

### Test Files

**Files with YAML credential tests:**
- `tests/api/test_settings_ui.py`
- `tests/api/test_settings.py`

**Status:** Not updated in this cleanup

**Recommendation:**
These tests may need updates to reflect:
- Settings page is now read-only
- Credential/sheet config forms removed
- YAML config loading deprecated

Tests should be updated after Phase 3 implementation and before release.

## Breaking Changes

### For Scripts (import.py / export.py)

**Before:**
```bash
# Could run without .env if YAML configured
python scripts/export.py
```

**After:**
```bash
# MUST have .env configured
# Fails with clear error if GOOGLE_CREDENTIAL_PATH or EXPORT_SPREADSHEET_ID not set
python scripts/export.py
```

**Migration:**
Users must copy values from YAML to .env:
```yaml
# Old scripts/scripts_config.yaml
google_credentials_path: ../data/credentials/service-account.json
export:
  spreadsheet_id: abc123xyz
```

Becomes:
```bash
# .env
GOOGLE_CREDENTIAL_PATH=data/credentials/service-account.json
EXPORT_SPREADSHEET_ID=abc123xyz
```

### For Settings UI

**Before:**
- Could upload credentials via web form
- Could load scripts_config.yaml with credentials

**After:**
- Web forms removed (Phase 2)
- YAML loading deprecated (but still functional for diagnostics)
- Must configure via .env

## Benefits

### 1. Simplified Codebase
- Removed 50+ lines of fallback logic
- Removed 2 helper functions
- Eliminated complex priority chains
- Clearer code flow

### 2. Better Error Messages
```
Before: "Set GOOGLE_CREDENTIAL_PATH in .env, or provide google_credentials_path
        in scripts_config.yaml, or upload credentials via the Settings UI."

After:  "GOOGLE_CREDENTIAL_PATH not set in .env file.
        Copy .env.sample to .env and set your credentials path."
```

### 3. Single Source of Truth
- Secrets in .env only
- No confusion about which config file to edit
- Easier to document and support

### 4. Consistent with Industry Standards
- .env pattern is standard for secrets management
- Aligns with 12-factor app methodology
- Familiar to developers

### 5. Reduced Attack Surface
- Fewer code paths to validate
- No database storage of credential metadata
- File permissions enforced at OS level

## Backward Compatibility

### What Still Works

✅ **Worksheet name configuration in YAML**
- `export.worksheets` and `import.worksheets` still read from YAML
- Can be overridden with environment variables
- YAML provides sensible defaults (Categories, Experiences, Manuals)

✅ **Structural configuration in YAML**
- `data_path` still works
- `database_filename` still works
- `dry_run` and `verbose` flags still work

✅ **Settings UI diagnostics**
- Diagnostics probe can still load YAML for validation
- Backup/restore still functional
- Audit log still captures events

### What Stopped Working

❌ **YAML-based credentials**
- `google_credentials_path` in YAML no longer read by scripts
- Must use `GOOGLE_CREDENTIAL_PATH` environment variable

❌ **YAML-based sheet IDs**
- `export.spreadsheet_id` no longer read by scripts
- `import.spreadsheet_id` no longer read by scripts
- Must use `EXPORT_SPREADSHEET_ID` and `IMPORT_SPREADSHEET_ID`

❌ **Settings DB credentials**
- Scripts no longer fall back to credentials in Settings database
- Must use .env

## Migration Guide

### For Existing Users

**Step 1: Create .env file**
```bash
cp .env.sample .env
```

**Step 2: Transfer values from YAML**

Find your current values:
```yaml
# scripts/scripts_config.yaml
google_credentials_path: ../data/credentials/service-account.json
export:
  spreadsheet_id: your-export-sheet-id
import:
  spreadsheet_id: your-import-sheet-id
```

Add to .env:
```bash
# .env
GOOGLE_CREDENTIAL_PATH=data/credentials/service-account.json
IMPORT_SPREADSHEET_ID=your-import-sheet-id
EXPORT_SPREADSHEET_ID=your-export-sheet-id
```

**Step 3: Update YAML (optional cleanup)**

Remove from `scripts/scripts_config.yaml`:
- `google_credentials_path` (no longer used)
- `export.spreadsheet_id` (no longer used)
- `import.spreadsheet_id` (no longer used)

Or simply replace with the sample:
```bash
cp scripts/scripts_config.yaml.sample scripts/scripts_config.yaml
```

**Step 4: Test**
```bash
# Dry-run to verify config
python scripts/export.py --dry-run

# Should show configuration loaded from .env
```

### For New Users

Simply follow the standard setup:
```bash
cp .env.sample .env
# Edit .env
python scripts/setup.py
```

## Verification

### Quick Test

**Test import script requires .env:**
```bash
# Without .env
mv .env .env.backup
python scripts/import.py

# Should fail with:
# Configuration error: GOOGLE_CREDENTIAL_PATH not set in .env file.

# Restore and verify it works
mv .env.backup .env
python scripts/import.py --help  # Should work
```

**Test export script requires .env:**
```bash
# Remove sheet ID from .env
# (comment out EXPORT_SPREADSHEET_ID)
python scripts/export.py --dry-run

# Should fail with:
# Configuration error: EXPORT_SPREADSHEET_ID not set in .env file.
```

## Future Cleanup Opportunities

### Potential Phase 3+ Tasks

1. **Remove Settings DB credential storage entirely**
   - Currently kept for backup/restore
   - Could simplify to only backup experience/manual data
   - Remove `settings.credentials` database field

2. **Remove load_sheet_config() from SettingsService**
   - Currently kept for diagnostics probe
   - Could replace diagnostics with direct .env reading
   - Simplify SettingsService to models-only

3. **Update manual.md comprehensively**
   - Remove all YAML credential references
   - Document .env-first workflow
   - Update screenshots if any

4. **Update test suite**
   - Remove YAML credential tests
   - Add .env validation tests
   - Update Settings UI tests for read-only behavior

## Summary Statistics

**Code Removed:**
- ~60 lines of fallback logic
- 2 helper functions
- 1 import statement
- 3 configuration fields from YAML sample

**Code Simplified:**
- 2 scripts (import.py, export.py)
- 1 configuration sample
- Error message complexity reduced

**Documentation Added:**
- 2 deprecation notices in SettingsService
- Updated YAML sample comments
- This cleanup summary document

## Related Documents

- `doc/plan/07_phase1_implementation_summary.md` - python-dotenv integration
- `doc/plan/07_phase2_implementation_summary.md` - Settings UI simplification
- `doc/plan/07_web_refine.md` - Original plan
- `.env.sample` - Environment configuration template
- `scripts/scripts_config.yaml.sample` - Structural defaults template
