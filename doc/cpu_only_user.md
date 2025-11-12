# CPU-Only Implementation Checklist

This document tracks all code touch points for implementing CPU-only mode across the CHL codebase. Use this as a reference when working through Phases 1-4 of the implementation plan.

## Status Legend
- ✅ Complete - Implementation finished and verified
- 🚧 In Progress - Currently being worked on
- ⏸️ Pending - Not started yet
- ❌ Blocked - Waiting on dependencies

## Phase 0: Baseline Alignment ✅

**Goal:** Establish shared terminology and guardrails before touching code.

- ✅ Define `CHL_SEARCH_MODE` in `src/config.py` (lines 127, 203-209, 278-286)
- ✅ Update `README.md` with CPU-only quick start (lines 7-138)
- ✅ Update `doc/manual.md` with CPU-only workflow (section 9)
- ✅ Document backward compatibility and FAISS portability caveats
- ✅ Create this touch points checklist

**Acceptance:** CLI/docs mention the new flag, expectations for CPU users, and lack of FAISS snapshot portability.

## Phase 1: Configuration & Server Bootstrap ✅

**Goal:** Make the backend respect `CHL_SEARCH_MODE=sqlite_only` without noisy warnings.

### src/api_server.py
- ✅ Wrap embedding client initialization behind mode check (line 123-131)
- ✅ Wrap FAISS initialization behind mode check (lines 158-186)
- ✅ Wrap reranker initialization behind mode check (lines 188-200)
- ✅ Wrap background worker setup behind mode check (lines 231-273)
- ✅ Skip all ML imports and initialization when `search_mode=sqlite_only` (lines 132-278)
- ✅ Log single info message: "Search mode=sqlite_only; vector components disabled." (line 124)
- ✅ Force `primary_provider=sqlite_text` when in sqlite_only mode (line 126)

### src/search/service.py
- ✅ SearchService accepts forced `primary_provider=sqlite_text` parameter
- ✅ Verified `get_vector_provider()` returns `None` when vector_provider=None passed

### src/config.py
- ✅ Parse `CHL_SEARCH_MODE` environment variable (line 127)
- ✅ Validate valid values: `auto`, `sqlite_only` (lines 203-209)
- ✅ Skip FAISS directory creation when `search_mode=sqlite_only` (lines 278-286)

### Testing
- ✅ Config loads correctly with `CHL_SEARCH_MODE=sqlite_only`
- ✅ FAISS directory is NOT created in sqlite_only mode
- ✅ FAISS directory IS created in auto mode
- ✅ Invalid mode values are rejected with clear error message
- ✅ SearchService initializes with sqlite_text provider only in sqlite_only mode
- ✅ No vector_faiss provider is registered in sqlite_only mode

**Acceptance:** Starting server with `CHL_SEARCH_MODE=sqlite_only` produces no warnings, `/settings` loads without errors. ✅ (code changes complete, UI testing in Phase 2)

## Phase 2: UI and Documentation Changes ⏸️

**Goal:** Make the SQLite-only posture obvious in dashboards and docs.

### Documentation
- ✅ `README.md`: Restructured Quick Start with GPU/CPU paths (lines 7-138)
- ✅ `doc/manual.md`: Added section 9 on CPU-only mode
- ✅ Document mode switching and FAISS portability

### MCP Guidance Files
- ⏸️ Create `evaluator_cpu.md` in project root (next to `generator.md`/`evaluator.md`)
- ⏸️ Document keyword search constraints and search strategy guidance

### src/api/routers/guidelines.py
- ⏸️ Inject `config` via `Depends(get_config)`
- ⏸️ Branch on `config.search_mode` when `guide_type="evaluator"`
- ⏸️ Serve `evaluator_cpu.md` when `search_mode="sqlite_only"`
- ⏸️ Serve standard `evaluator.md` when `search_mode="auto"`

### Seeding Scripts
- ⏸️ Update `scripts/seed_default_content.py` to read and seed `evaluator_cpu.md`
- ⏸️ Update `scripts/sync_guidelines.py` to handle both evaluator variants
- ⏸️ Update `GUIDE_TITLE_MAP` (or equivalent) for "Evaluator (CPU-only)"

### Settings UI
- ⏸️ Create `src/web/templates/settings_cpu.html` template
- ⏸️ Route picks template dynamically based on `config.search_mode`
- ⏸️ CPU template omits FAISS/model sections
- ⏸️ CPU template surfaces keyword-search instructions
- ⏸️ CPU template links to CPU docs
- ⏸️ Show "Search Mode" banner in both templates
- ⏸️ Ensure validation panels don't render "missing model files" errors in SQLite-only mode

### Operations UI
- ⏸️ Dim or hide FAISS upload/rebuild buttons when `search_mode=sqlite_only`
- ⏸️ Show appropriate message about SQLite-only mode

### Cleanup (Optional)
- ⏸️ Document or remove unused `src/mcp/handlers_guidelines.py` module

**Acceptance:** Operators see clear banner about SQLite-only mode, no bogus validation failures, docs explain mode switching.

## Phase 3: Observability & API Behavior ⏸️

**Goal:** Keep clients informed while preventing noisy alerts.

### src/api/routers/health.py
- ⏸️ When `search_mode=sqlite_only`, report `components["faiss_index"] = {"status": "disabled", "detail": "Intentional SQLite-only mode"}`
- ⏸️ Emit HTTP 200 (healthy) if database is fine, even without FAISS
- ⏸️ Keep current behavior when `search_mode=auto` and FAISS is missing (degraded)

### Telemetry/Logging
- ⏸️ Gate repetitive "vector provider not available" warnings
- ⏸️ Emit once at startup or not at all in SQLite mode
- ⏸️ Consider adding metric flag: `search_mode_sqlite_only=1`

### src/mcp/handlers_entries.py
- ⏸️ Confirm `degraded=True` surfaces from `SQLiteTextProvider`
- ⏸️ Confirm `provider_hint` is included in responses
- ⏸️ Include `meta.search_mode` in responses
- ⏸️ Reinforce keyword guidance via `provider_hint` when fallback is active
- ⏸️ Document that automatic query rewriting is NOT used (keep UX predictable)

**Acceptance:** Health checks don't page for "missing FAISS" in text mode; MCP clients get consistent hints about fallback provider.

## Phase 4: Tests & Release Hygiene ⏸️

**Goal:** Guard against regressions and socialize the change.

### Test Coverage
- ⏸️ Add pytest marker `@pytest.mark.sqlite_only`
- ⏸️ Test: bring up API server with `CHL_SEARCH_MODE=sqlite_only`
- ⏸️ Test: hit `/health` endpoint
- ⏸️ Test: exercise `read_entries(query=...)`
- ⏸️ Test: exercise `write_entry` with duplicate hints
- ⏸️ Test: assert FAISS directories are not created
- ⏸️ Test: `Config` initializes cleanly with `sqlite_only`
- ⏸️ Test: invalid `CHL_SEARCH_MODE` values raise clear errors
- ⏸️ Test: FAISS directory creation is skipped in SQLite-only mode

### CI Configuration
- ⏸️ Run at least one CI job without `ml` extra
- ⏸️ Ensure no implicit llama-cpp imports remain

### Release Documentation
- ⏸️ Update CHANGELOG (if maintained) to highlight new mode
- ⏸️ Document lack of vector snapshot compatibility guarantees
- ⏸️ Create follow-up work items for optional enhancements

**Acceptance:** Automated coverage exists for new mode, documentation published, config validation works correctly in both modes.

## Touch Points Summary

### Files Modified (Phase 0)
1. `src/config.py` - Added CHL_SEARCH_MODE parsing and validation
2. `README.md` - Added CPU-only quick start and mode switching docs
3. `doc/manual.md` - Added section 9 on CPU-only mode
4. `doc/cpu_only_user.md` - This checklist

### Files Modified (Phase 1)
1. `src/api_server.py` - Conditional ML component initialization (lines 121-278)
   - Wrap all ML imports and initialization behind `config.search_mode` check
   - Force `primary_provider=sqlite_text` in sqlite_only mode
   - Skip embedding client, FAISS, reranker, and worker initialization
2. `src/search/service.py` - No changes needed (already supports forced primary provider)

### Files to Modify (Phase 2)
1. `evaluator_cpu.md` - New CPU-specific evaluator guidance
2. `src/api/routers/guidelines.py` - Dynamic guideline selection
3. `scripts/seed_default_content.py` - Seed CPU evaluator guide
4. `scripts/sync_guidelines.py` - Handle both evaluator variants
5. `src/web/templates/settings_cpu.html` - New CPU-only settings template
6. `src/web/templates/settings.html` - Add search mode banner
7. Settings route handler - Dynamic template selection
8. Operations template - Conditional FAISS button visibility

### Files to Modify (Phase 3)
1. `src/api/routers/health.py` - Distinguish intentional vs accidental degradation
2. `src/mcp/handlers_entries.py` - Ensure degraded hints surface correctly
3. Logging configuration - Gate repetitive warnings

### Files to Create (Phase 4)
1. Test file with `@pytest.mark.sqlite_only` tests
2. Config validation tests
3. CI configuration updates
4. CHANGELOG entry

## Phase Execution Order

1. **Phase 0** (Complete) → **Phase 1** (Backend runtime)
2. **Phase 1** → **Phase 2** (UI/docs) and **Phase 3** (observability) in parallel
3. **Phase 2** + **Phase 3** → **Phase 4** (tests)

## Notes

- Mode changes require server restart
- FAISS artifacts remain on disk when switching to `sqlite_only` but are ignored
- Pending embedding tasks are dropped on restart in `sqlite_only` mode
- Consider health endpoint warning if `config.search_mode="sqlite_only"` but vector provider is still active (optional future enhancement)
