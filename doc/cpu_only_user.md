# CPU-Only Enablement Plan

This note documents how we can let users without a CUDA-capable GPU (or without the ML extras installed) run the CHL MCP stack in a *SQLite-search-only* mode. The goal is to keep the MCP tools usable (list/read/write/update) while skipping embedding, reranking, and FAISS maintenance.

## Goals
- Allow a CPU-only user to install and start the MCP/API server without pulling the `ml` optional dependencies from `pyproject.toml`.
- Skip all embedding/reranking touch points while keeping the database/API/web dashboards functional.
- Ensure MCP `read_entries` queries keep working through the existing `SQLiteTextProvider` fallback (see `src/search/sqlite_provider.py`), with clear “degraded search” hints surfaced to clients.
- Provide an explicit runbook and configuration flag so the user (and telemetry/health endpoints) know the instance is intentionally in SQLite-only mode, rather than “vector search failed unexpectedly”.

## Current behavior recap
- `uv sync --python 3.11 --extra ml` installs `llama-cpp-python`, `faiss-cpu`, etc. (`pyproject.toml`). Without that extra, `src/embedding/client.py` and `src/embedding/reranker.py` fail to import `llama_cpp` during instantiation and the API server logs an error before falling back to text search.
- `SearchService` (`src/search/service.py`) already registers `SQLiteTextProvider` and uses it whenever the vector provider is missing or throws `SearchProviderError`. `SQLiteTextProvider` marks results as `degraded=True` with a hint, and `read_entries` propagates those fields in the MCP payload (`src/mcp/handlers_entries.py`).
- Duplicate detection during `write_entry`/`update_entry` also flows through `SearchService.find_duplicates`, so it already degrades to simple LIKE matching.
- Health checks (`src/api/routers/health.py`) expose FAISS/embedding status as `degraded` when vector search is offline, but they cannot currently distinguish “intentional SQLite-only mode” from “unexpected failure”.
- The background embedding worker (`src/services/background_worker.py`) is created only if an `EmbeddingClient` exists, so it no-ops automatically today, but the UI/operations panels still expect FAISS snapshots to exist.

## Proposed work plan

### 1. Installation/profile updates
- Add a **CPU-only quick start** block to `README.md`/`doc/manual.md` that instructs users to run `uv sync --python 3.11` (no `--extra ml`) and to pass `--skip-models` when running `scripts/setup.py`.
- Extend `scripts/setup.py` so model-download steps are skipped automatically when a new `--sqlite-only` flag (or env `CHL_FORCE_SQLITE=1`) is set, instead of failing when HuggingFace caches are absent.
- Document that the user only needs the base dependencies plus `faiss-cpu` if they ever want to import snapshots (optional).

### 2. Explicit SQLite-only runtime mode
- Introduce a config switch (e.g., `CHL_SEARCH_MODE=sqlite_only`) in `src/config.py`. When enabled, skip importing/instantiating `EmbeddingClient`, FAISS, reranker, and worker logic inside `src/api_server.py`.
- Update `SearchService` initialization so `primary_provider` is forced to `sqlite_text` when the flag is on. Today the service already falls back, but forcing it prevents repeated error logs and wasted retries.
- Surface the flag in `/settings` so operators know the search pipeline is intentionally degraded.

### 3. UX and observability
- Update `/health` and the operations dashboard to display “Intentional SQLite mode” instead of a generic “degraded” warning when the new flag is present. This avoids noisy alerts for machines that will never host FAISS.
- In the MCP responses, keep the existing `degraded` + `provider_hint`, but add a short note telling assistants that semantic ranking is disabled so they should issue narrower keyword queries.
- Consider adding an operations banner (maybe in `src/web/templates/settings.html` and `/operations`) that hides FAISS upload/rebuild actions when SQLite-only mode is active.

### 4. Content sharing path
- Document how teams with GPUs can periodically publish FAISS snapshots + embeddings so CPU-only users can periodically download them into `data/faiss_index/`. Long term we could add a cli helper (`scripts/download_snapshot.py`) that fetches the latest artifact from S3 and unpacks it without needing the ML stack.
- Make sure the MCP/UI clearly communicates when the installed FAISS snapshot is stale versus absent. That can key off the metadata already stored by `ThreadSafeFAISSManager`.

### 5. Testing and validation
- Add a smoke test configuration (pytest marker) that runs the API server with `CHL_SEARCH_MODE=sqlite_only`, verifying that `/health`, `read_entries(query=...)`, and `write_entry` still succeed and that duplicate hints come from the text provider.
- Update CI docs so we run at least one suite without the `ml` extra to guarantee we do not accidentally reintroduce hard dependencies on llama-cpp or PyTorch.

## Acceptance checklist
- [ ] CPU-only install instructions published (`README.md`, `doc/manual.md`, this page).
- [ ] `CHL_SEARCH_MODE=sqlite_only` bypasses ML initialization and produces clean logs.
- [ ] Settings/health/operations surfaces label the mode clearly, and MCP responses keep degraded hints.
- [ ] Smoke tests (and optionally a GitHub Actions job) cover the SQLite-only configuration.
- [ ] Optional: artifact sharing story documented so CPU users can still benefit from GPU-built FAISS indexes later.
