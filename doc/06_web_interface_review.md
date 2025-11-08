# Web Interface Implementation Review
**Phase 3 Completion Assessment**  
**Date**: November 8, 2025  
**Reviewer**: Claude Code

---

## Executive Summary

The combined Phase 0–3 web deliverables are functionally complete and feel cohesive. Settings onboarding, operations orchestration, MCP HTTP transport, and documentation now point new users to a single entry point (`uv run uvicorn src.api_server:app ...`) and let the UI drive the rest. The UI stack (FastAPI + Jinja2 + htmx + Pico + SSE) is solid, secrets stay on disk, and advisory locks plus audit logging cover every state-changing action.

Production readiness is **~80 %**: the feature set is there, but a handful of high-impact gaps remain:

1. **Operations subprocess safety** – `_run_script` has no timeout and blindly accepts arbitrary `env` overrides from payloads (`src/services/operations_service.py`).
2. **Index snapshot hardening** – archive members are extracted before validation, so a crafted ZIP could escape the temp directory; security events are not audited (`src/api/routers/ui.py`).
3. **Duplicate HTTP clients** – `src/api_client.py` (scripts) and `src/mcp/api_client.py` (MCP) implement similar logic, causing drift and double maintenance.
4. **Global MCP cache** – `server.py` uses a module-level categories cache with no locking or invalidation when settings change, which risks stale data and race conditions in multi-threaded hosts.
5. **Credential permissions** – insecure credential files (world-readable) only raise a warning; the UI proceeds even when we detect weak permissions (`src/services/settings_service.py`).

Addressing these items plus a few polish tasks (job listing API, SSE reconnection indicator, test cleanup) will take roughly two focused sprints.

---

## 1. Implementation Review

### 1.1 Settings Dashboard (Complete)
- `/settings` covers credential upload or path registration, Google Sheets metadata, model overrides, diagnostics, audit log, and JSON backup/restore. Uploads enforce 512 KiB limit, UTF‑8 JSON decoding, and `chmod 0o600` on save.
- Diagnostics reuse `SettingsService.update_credentials` so the same validation path runs whether a user uploads or probes.
- Managed credential directory (`settings_service.secrets_root`) is surfaced in the UI, reinforcing the "secrets live on disk" contract.
- **Remaining gaps**: permissions below `0o600` only show a warning, and error messages still assume shell literacy (e.g., “fix chmod” without guidance).

### 1.2 Operations Dashboard (Complete with Risks)
- `/operations` streams queue depth, worker heartbeats, job history, and FAISS snapshot info via SSE (`/ui/stream/telemetry`). htmx partials provide fallback refreshes.
- Job triggers (`import`, `export`, `index`) now invoke the real scripts whenever `CHL_OPERATIONS_MODE=scripts`; `noop` mode keeps CI/tests inert. Advisory locks prevent concurrent runs and persist to `operation_locks`.
- Worker actions (pause/resume/drain) display only when a worker pool registers; otherwise the card shifts to guidance copy. FAISS snapshots support download, upload (ZIP, ≤512 MiB), automatic backups, and best-effort hot reload through the vector provider.
- **Remaining gaps**: `_run_script` lacks timeout/allowlist, we do not surface stdout/stderr beyond the “tail” stored in DB, the index upload flow uses `ZipFile.extractall` before inspecting entries (Zip Slip risk), and there is no SSE indicator when the stream stalls.

### 1.3 API + Services (Complete, missing one endpoint)
- REST surface mirrors UI needs: settings CRUD, operations trigger/cancel/status, worker controls, telemetry snapshots. Secrets are never returned; only metadata (path, checksum, validated_at) lives in SQLite (`settings`, `job_history`, `operation_locks`, `telemetry_samples`, `audit_log`).
- `OperationsService` centralizes locking + job history, and `last_runs_by_type` powers the "Last run" badges in the UI.
- **Missing endpoint**: there is still no `/api/v1/operations/jobs` list, so programmatic clients cannot read history even though the service has `list_recent`.

### 1.4 MCP HTTP Client & Server (Complete, needs cache fix)
- `CHL_MCP_HTTP_MODE` (plus `--chl-http-mode`) controls HTTP/auto/direct modes. Auto mode falls back to direct handlers if transport errors occur, and the handshake payload advertises the active transport.
- The HTTP client sports circuit breaker, retry/backoff, structured logging, and a `MCPTransportError`. `CHL_SKIP_MCP_AUTOSTART` keeps tests fast.
- **Gaps**: `_categories_cache` in `src/server.py` is a module-global dict with no lock or invalidation hook from settings changes. Legacy `CHL_USE_API` is still silently supported, risking confusion unless we emit a warning.

### 1.5 Documentation & Onboarding (Complete)
- README tells new contributors to `uv run uvicorn src.api_server:app --reload` and then finish setup via `/settings` and `/operations`. The onboarding cards inside the UI explain every step (credential options, telemetry expectations, FAISS snapshot workflow).
- `doc/chl_guide.md`, `doc/chl_manual.md`, and the phase docs all reflect the dual credential workflow, localhost binding assumption, and the operations dashboard features.

---

## 2. Critical Gaps & Risks

| # | Area | Description | Impact |
|---|------|-------------|--------|
| 1 | Operations subprocess safety | `_run_script` never times out and copies arbitrary `payload["env"]` pairs into the process environment. A hung import/export can tie up executors indefinitely, and untrusted env vars could override credentials or inject shell behaviour. | High |
| 2 | Index snapshot hardening | `_restore_index_archive` calls `ZipFile.extractall(temp_dir)` *before* validating members. A malicious archive could write outside `temp_dir` (Zip Slip) before we block it, and we do not emit audit/security logs when such attempts occur. | High |
| 3 | Duplicate HTTP clients | `src/api_client.py` (scripts) and `src/mcp/api_client.py` (MCP) drift separately—different retry, headers, error mapping. Bugs fixed in one client will not reach the other, and we maintain ~400 redundant lines. | High |
| 4 | MCP cache coherence | `_categories_cache` is a plain dict shared across tool calls. FastMCP can dispatch requests concurrently, so mutations are not thread-safe, and the cache never clears after settings changes (users can see stale shelves for ~30 s). | Medium/High |
| 5 | Credential permission enforcement | Diagnostics downgrade world-readable credentials to "warn" but still report success; the UI never blocks saving or explains how to fix permissions within the browser. | Medium |

---

## 3. Additional Improvements (High → Low)

1. **Job history API** – expose `/api/v1/operations/jobs?limit=50` so scripts or MCP tools can fetch history without scraping the UI.
2. **SSE resiliency** – add a reconnect indicator (htmx `sse-error` event) and fall back to periodic polling if SSE fails.
3. **Script coordination** – revisit `--skip-api-coordination` defaults in `scripts/import.py` and `scripts/rebuild_index.py`; now that OperationsService handles locking, the scripts should either respect API coordination flags or drop the switch entirely.
4. **Audit security events** – when index upload rejects a file (path traversal, wrong suffix) or credential permissions are unsafe, log to `audit_log` with `event_type` like `security.index_upload_blocked`.
5. **Test cleanup** – `tests/integration/test_concurrent_faiss*.py` duplicate 60 % of scenarios; merge into a single module. `tests/api/test_entries.py` contains seven `pytest.skip` branches that hide failures—refactor to fixtures so scenarios either pass or fail deterministically.
6. **Deprecation messaging** – emit a warning when `CHL_USE_API` or `CHL_MCP_HTTP_MODE=direct` is used, and document the removal plan.

---

## 4. Prioritized Recommendations

1. **Add subprocess guardrails** (timeout + env allowlist) in `OperationsService._run_script`, and surface timeout configuration via `CHL_OPERATION_TIMEOUT`.
2. **Validate archives before extraction**: iterate over `ZipFile.infolist()` first, reject suspicious entries, and only then extract the allowed files; log any blocked attempts.
3. **Unify HTTP clients** by moving scripts to `src/mcp/api_client.APIClient` (or extracting a shared module) and deleting the legacy client once scripts are migrated.
4. **Make MCP cache safe**: wrap `_categories_cache` access in a lock or switch to `functools.lru_cache` + TTL, and expose an invalidation hook that the settings endpoints call after mutations.
5. **Enforce credential permissions**: treat world-readable credentials as errors in diagnostics/UI, and display copy-pastable shell commands for fixing permissions.
6. **Ship job-history API + SSE indicator** to round out Phase 3 polish.
7. **Tackle test duplication** to keep CI times down and failure signals crisp.

---

## 5. Appendix – Action Items

| Priority | File/Area | Action |
|----------|-----------|--------|
| Critical | `src/services/operations_service.py` | Add `timeout` to `subprocess.run`, clamp which env vars can be overridden, and redact sensitive values when storing stdout/stderr tails. |
| Critical | `src/api/routers/ui.py` | Inspect ZIP members before extraction, reject path traversal earlier, and log/audit any blocked upload. |
| Critical | `src/api_client.py` & `src/mcp/api_client.py` | Consolidate into a single client shared by scripts, MCP, and future tooling. |
| High | `src/server.py` | Protect `_categories_cache` with locking or replace with a cache helper that includes invalidation hooks. |
| High | `src/services/settings_service.py` | Fail fast on weak file permissions and surface remediation steps. |
| High | `tests/integration/test_concurrent_faiss*.py` | Merge overlapping suites and drop redundant cases. |
| Medium | `tests/api/test_entries.py` | Replace `pytest.skip` branches with fixtures that guarantee prerequisites, ensuring each test either passes or fails. |
| Medium | `src/config.py` / README | Emit warning when `CHL_USE_API` or MCP direct mode is used; document sunset timeline. |
| Medium | UI templates | Add SSE reconnection indicator + tooltip explaining `CHL_OPERATIONS_MODE`. |
| Medium | `src/api/routers/operations.py` | Add `/jobs` listing endpoint and paginate results for UI + API clients. |

---

**Review complete. Next review recommended once the critical subprocess and archive fixes land.**
