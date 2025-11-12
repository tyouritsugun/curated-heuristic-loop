# CPU-Only Execution: Implementation Plan

This plan organizes the CPU-only enablement work into concrete phases. Each phase highlights the components to touch, the user-visible behavior, and acceptance criteria. Refer to `doc/cpu_only_user.md` for the rationale; this document focuses on execution details.

## Terminology
- `CHL_SEARCH_MODE`: Environment variable set by the operator (`auto`, `sqlite_only`). A stricter `vector_only` mode is deferred for future work.
- `config.search_mode`: Runtime attribute produced by `src/config.py` after parsing the environment.
- *SQLite-only mode*: `CHL_SEARCH_MODE=sqlite_only`, meaning no FAISS, embeddings, reranker, or background worker are initialized.

---

## Phase 0 – Baseline alignment
**Goal:** Establish shared terminology and guardrails before touching code.

- Define `CHL_SEARCH_MODE` (handled in `src/config.py`) with two supported values for this release:
  - `auto` (default): Try vector search; fall back to SQLite if initialization fails.
  - `sqlite_only`: Force text search; skip embedding/reranker/FAISS initialization entirely.
  - `vector_only` is explicitly deferred and tracked as an enhancement after this work lands.
- Document the flag and CPU-only workflow in `README.md` and `doc/manual.md`, including the install command (`uv sync --python 3.11`) and the fact that ML extras stay optional.
- Clarify backward compatibility: FAISS snapshots built on GPU machines are **not** usable in SQLite-only mode. Switching back to `auto` requires reinstalling ML extras, rerunning `scripts/setup.py`, and rebuilding FAISS from scratch.
- Capture all known touch points (search service, health, settings diagnostics, logging, MCP prompt, UI templates) in an issue checklist.

**Acceptance:** CLI/docs mention the new flag, expectations for CPU users, and the lack of FAISS snapshot portability when switching modes.

---

## Phase 1 – Configuration & server bootstrap
**Goal:** Make the backend respect `CHL_SEARCH_MODE=sqlite_only` without noisy warnings.

- Extend `Config` (`src/config.py`) to parse `CHL_SEARCH_MODE`, defaulting to `auto`. Reject invalid values early so Settings UI doesn’t show generic validation errors.
- In `src/api_server.py`:
  - Wrap the embedding client, FAISS, reranker, and background worker setup behind a mode check. In `sqlite_only` mode, skip these blocks and log a single info message (`"Search mode=sqlite_only; vector components disabled."`).
  - Downgrade the “Failed to initialize embedding client / FAISS / reranker” logs from warning → info when `search_mode=sqlite_only`. They stay warnings when in `auto`.
  - Pass the resolved mode into `SearchService` so `primary_provider` is forced to `sqlite_text`, avoiding needless retries.
- Note: `SearchService.get_vector_provider()` already returns `None` when no provider is registered; verify this remains true in SQLite-only mode.
- Update configuration validation so the FAISS directory creation block in `Config.__init__` (`src/config.py:268-276`) only runs when `config.search_mode != "sqlite_only"`. `_validate_faiss_config` can stay as-is for vector modes; it simply won’t execute in SQLite-only mode.

**Acceptance:** Starting the server with `CHL_SEARCH_MODE=sqlite_only` produces no “failed to initialize …” warnings, and `/settings` loads without config error banners.

---

## Phase 2 – UI and documentation changes
**Goal:** Make the SQLite-only posture obvious in the dashboards and docs.

- `README.md` + `doc/manual.md`: Restructure the Quick Start as:
  - General “Quick Start” intro with guidance on deciding between GPU vs CPU installs (e.g., “Have ≥8 GB VRAM and want semantic search? Follow the GPU track; otherwise use CPU-only.”).
  - “For GPU” subsection that keeps the current Web UI-first flow (install ML extras, embeddings, FAISS).
  - “For CPU Only” subsection describing the `sqlite_only` mode, keyword search constraints, and the pared-down install steps (`uv sync` without `--extra ml`, `CHL_SEARCH_MODE=sqlite_only`, etc.).
  - In both subsections, explain how to switch modes later and reiterate the lack of FAISS snapshot portability across modes.
- Author a dedicated MCP guidance file (`evaluator_cpu.md`, stored next to `generator.md`/`evaluator.md` in the project root). Update the API guideline router (`src/api/routers/guidelines.py`) so when `guide_type="evaluator"` **and** `config.search_mode="sqlite_only"`, it serves `evaluator_cpu.md` instead of the default file. Implementation notes:
  - Inject `config` via the existing FastAPI dependency pattern (`Depends(get_config)`) so the handler can branch on `config.search_mode`.
  - Ensure `scripts/seed_default_content.py` and `scripts/sync_guidelines.py` read and seed the CPU-specific file as an “Evaluator (CPU-only)” manual in the GLN category, so GLN mirrors what the API returns.
  - Update `GUIDE_TITLE_MAP` (or equivalent mapping) to understand both evaluator variants.
- `/settings` experience:
  - Keep the existing `settings.html` untouched for GPU-capable installs.
  - Add a dedicated `settings_cpu.html` template and have the FastAPI route pick the template dynamically based on `config.search_mode`. The CPU template omits FAISS/model sections, surfaces keyword-search instructions, and links to the CPU docs.
  - Regardless of template, show a compact “Search Mode” banner so operators know which variant they’re viewing.
  - Ensure validation panels don’t render “missing model files” errors when those checks are skipped by design.
- `/operations` view: optionally dim or hide FAISS upload/rebuild buttons when SQLite-only mode is active.
- (Optional cleanup) Since the MCP server is an HTTP forwarder, document or remove the unused `src/mcp/handlers_guidelines.py` module to avoid confusion once the API router owns the logic.

**Acceptance:** Operators see a clear banner/message about SQLite-only mode, no bogus validation failures appear, and docs/README explain the mode switch and compatibility caveats.

---

## Phase 3 – Observability & API behavior
**Goal:** Keep clients informed while preventing noisy alerts.

- Health endpoint (`src/api/routers/health.py`):
  - When `search_mode=sqlite_only`, report `components["faiss_index"] = {"status": "disabled", "detail": "Intentional SQLite-only mode"}` instead of generic `degraded`.
  - Emit HTTP 200 (healthy) if the database is fine, even though FAISS is absent by choice.
- Telemetry/logging:
  - Gate repetitive “vector provider not available” warnings; emit them once at startup or not at all in SQLite mode.
  - Consider adding a metric flag (e.g., `search_mode_sqlite_only=1`) for monitoring dashboards.
- MCP `read_entries` (`src/mcp/handlers_entries.py`):
  - Confirm `degraded=True` and `provider_hint` surfaces from `SQLiteTextProvider`. Document this behavior so client teams know to expect literal matching.
  - Include `meta.search_mode` and reinforce the keyword guidance via `provider_hint` whenever the fallback is active; avoid automatic query rewriting/rejection to keep UX predictable.
  - (Optional future work) Explore opt-in validation/suggestions if we need stronger guidance later.

**Acceptance:** Health checks no longer page for “missing FAISS” when the user opted into text mode; MCP clients get consistent hints about the fallback provider.

---

## Phase 4 – Tests & release hygiene
**Goal:** Guard against regressions and socialize the change.

- Add a pytest marker (`@pytest.mark.sqlite_only`) that brings up the API server with `CHL_SEARCH_MODE=sqlite_only`, hits `/health`, exercises `read_entries(query=…)` plus `write_entry` duplicate hints, and asserts that FAISS directories are not created.
- Add standalone config tests verifying:
  - `Config` initializes cleanly with `sqlite_only`.
  - Invalid `CHL_SEARCH_MODE` values raise clear errors.
  - FAISS directory creation is skipped in SQLite-only mode.
- Run at least one CI job without the `ml` extra to ensure no implicit llama-cpp imports remain.
- Update release notes / CHANGELOG (if maintained) to highlight the new mode and the lack of vector snapshot compatibility guarantees.
- After shipping, create follow-up work items for optional enhancements (e.g., download prebuilt FAISS snapshots).

**Acceptance:** Automated coverage exists for the new mode, documentation is published, and config validation behaves correctly in both modes.

---

## Phase execution order & mode switching
- Phase 0 is prerequisite for every other phase.
- Phase 1 (config/runtime) must land before Phase 2 (UI/docs).
- Phase 2 and Phase 3 can progress in parallel once Phase 1 is complete.
- Phase 4 runs last to lock in regression coverage.
- Mode changes require a server restart. Switching from `sqlite_only` → `auto` involves:
  1. Set `CHL_SEARCH_MODE=auto`.
  2. Install ML extras (`uv sync --python 3.11 --extra ml`).
  3. Run `scripts/setup.py --download-models`.
  4. Restart the API/MCP server.
  5. Rebuild embeddings/FAISS (background worker or `scripts/rebuild_index.py`).
- Switching from `auto` → `sqlite_only` just requires updating the env var and restarting; FAISS artifacts remain on disk but are ignored until vector mode is re-enabled. Any pending embedding tasks in the worker queue are dropped on restart, which is acceptable because text-only mode no longer processes embeddings.
- (Optional future enhancement) The health endpoint could warn if `config.search_mode="sqlite_only"` but a vector provider is still active, signaling that a restart is needed after toggling the flag.
