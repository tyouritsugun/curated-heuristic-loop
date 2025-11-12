# Changelog

All notable changes to this project will be documented in this file.

The format is based on Keep a Changelog, and this project adheres to Semantic Versioning where possible.

## Unreleased

### Added
- CPU-only mode via `CHL_SEARCH_MODE=sqlite_only` (no FAISS/embeddings/reranker/worker). Config validation updated to skip FAISS directory creation in this mode.
- Health endpoint reports vector components as `disabled` (HTTP 200) in CPU-only mode; avoids paging for intentional configuration.
- Telemetry snapshot includes `meta.search_mode`; startup metric `search_mode_sqlite_only` increments when CPU-only mode is active.
- UI updates for CPU-only mode:
  - Banners: hardware-agnostic copy (“Semantic Search Enabled”), CPU-only chip on Settings and Operations.
  - Mode-aware onboarding content; FAISS mentions omitted in CPU-only mode.
  - Diagnostics panel gates FAISS checks in CPU-only mode (informational status instead of warn/error).
- MCP layer and search results:
  - `SQLiteTextProvider` marks results `degraded: true` with `hint` guidance; HTTP read_entries responses surface `degraded` and `provider_hint` for both experiences and manuals.
  - MCP `read_entries` adds `meta.search_mode` without changing HTTP API schema.
- Operations: model preference updates trigger a `reembed` job automatically in vector mode (skipped in CPU-only mode).
- API root (`/`) now returns a JSON service banner `{service, version, status}` for smoke checks; UI remains at `/settings`.
- Tests (Phase 4):
  - Marker `@pytest.mark.sqlite_only` to run tests with CPU-only mode.
  - Config tests for invalid/valid `CHL_SEARCH_MODE` and FAISS dir behavior.
  - CPU-only tests for health (disabled components), telemetry meta, and degraded search hints.
- CI: GitHub Actions workflow running tests without ML extras.

### Changed
- Reduced noisy logs: startup logs clarify when vector stack is intentionally disabled.
- Settings diagnostics updated to read `.env` for credentials/sheets over legacy DB settings.

### Fixed
- Manual search results now include `degraded`/`provider_hint` fields for parity with experience results in text-search mode.
- Broken internal docs links updated to `/docs/manual#9-cpu-only-mode`.

### Notes
- Switching from CPU-only to vector mode requires installing ML extras, running `scripts/setup-gpu.py --download-models`, and rebuilding FAISS.
- FAISS snapshots are not portable across modes; they are ignored in CPU-only mode.

