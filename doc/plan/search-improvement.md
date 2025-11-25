# Search and Rerank Improvements for CHL Toolset

## Goals
- Cut prompt boilerplate for LLM assistants.
- Reduce token waste by returning only what is needed.
- Avoid repeat surfacing of already-reviewed entries.
- Increase trust via lightweight quality rails.

## Updated Plan (v1.1, local-only, breaking changes OK)

### 1) Unified search API
- Replace existing `/api/v1/search` shape with:
  - Request: `query`, `types` (default `["experience","manual"]`), optional `category`, `limit/offset`, `min_score`, `filters` (`author`, `section`), `snippet_len`, `fields`, `hide_viewed`, `downrank_viewed`, `session_id`.
  - Response: `results` each with `entity_id`, `entity_type`, `title`, `section`, `score`, `rank`, `reason`, `provider`, `degraded`, `hint`, `heading`, `snippet`, `author`, `updated_at`; plus `count`, `top_score`, `warnings`, `session_applied` (bool).
- Drop legacy compatibility; callers must send the new schema.

### 2) Snippets + field selection
- Search returns heading + snippet (320 char default; max 640; up to 2 sentences).
- `entries/read` gains `fields` and `snippet_len`; defaults return previews instead of full text unless fields explicitly include full bodies.

### 3) Session memory (per MCP process/conversation)
- API accepts `session_id` (or header `X-CHL-Session`) to track `viewed_ids`, `cited_ids`, `last_queries`.
- Options: `hide_viewed` (drop seen hits) and `downrank_viewed` (score penalty, default true).
- Introspection endpoints:
  - `GET /api/v1/search/session?session_id=...` → stored lists (404 if expired).
  - `POST /api/v1/search/session/cited` with `{session_id, ids}`.
- Store: in-memory LRU (500 sessions, 60m idle TTL), per-process only (restart wipes).
- Thread-safe via simple lock; no disk or multi-tenant concerns.

### 4) Quality rails
- Enforce `min_score` (default 0.50 vector, 0.35 text) and emit warning when top_score below threshold.
- `write_entry` auto-runs duplicate check (750 ms budget, advisory); returns `duplicates` and `recommendation`; warn on timeout.
- Provider transparency: always return `provider`, `search_mode`, and optional `provider_hint`.

### 5) MCP wiring (no user action)
- Generate `session_id` once per MCP process (uuid4 hex) in `src/mcp/core.py` (or `server.py`) and inject `X-CHL-Session` on all API calls.
- Allow override via `CHL_SESSION_ID` env; optional internal `rotate_session()` helper if we later want per-conversation resets.

## Rationale
- Smaller, snippet-focused payloads cut LLM context/token spend and speed scans.
- Session-aware ranking keeps results fresh within a conversation without user prompts.
- Built-in guardrails reduce low-quality responses and accidental duplicates.
- Local-only scope lets us break the old schema for a cleaner v1.1.

## Rollout (ordered)
1) Implement unified `/api/v1/search` + `entries/read` schema changes, including snippets/fields and min_score handling.
2) Add session store + endpoints; wire search/read to update `viewed_ids`; expose `session_applied`.
3) Add duplicate-check on `write_entry` with warnings; surface provider/search_mode metadata and score warnings.
4) Update MCP core to auto-generate/inject `session_id`; document env override.

## Testing Notes
- Unit: request/response models, min_score filtering, snippet truncation, session cache eviction, duplicate-check timeout path.
- Integration: vector vs text fallback, hide/downrank viewed behavior, write_entry returning duplicates/warnings.
- Smoke: MCP agent flow with auto session header; verify warnings and provider fields surface.
