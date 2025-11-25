# Search and Rerank Improvements for CHL Toolset

## Goals
- Cut prompt boilerplate for LLM assistants.
- Reduce token waste by returning only what is needed.
- Avoid repeat surfacing of already-reviewed entries.
- Increase trust via lightweight quality rails.

## Updated Plan (v1.1, local-only, breaking changes OK)

### 1) Unified search API (why unify?)
- Problem: MCP today does two round-trips for rich display (search → read). Unified results with snippets let LLMs decide from one call, cutting latency and tokens.
- Replace existing `/api/v1/search` shape with:
  - Request: `query`, `types` (default `["experience","manual"]`), optional `category`, `limit/offset`, `min_score`, `filters` (`author`, `section`), `snippet_len`, `fields`, `hide_viewed`, `downrank_viewed`, `session_id`.
  - Response: `results` each with `entity_id`, `entity_type`, `title`, `section`, `score`, `rank`, `reason`, `provider`, `degraded`, `hint/provider_hint`, `heading`, `snippet`, `author`, `updated_at`; plus `count`, `top_score`, `warnings`, `session_applied` (bool).
- Keep `/api/v1/entries/read` for full-body fetch; enhanced (see §2) but still available. Breaking change is limited to `/api/v1/search`; MCP will be updated accordingly.
- Cross-type behavior: when `types=["experience","manual"]`, search both, merge by score, single global rank; `category` filters both types to that category.
- Field origins:
  - Experience → heading = title (or first Markdown heading if present); snippet from `playbook` using `snippet_len`; context is *not* included in snippets.
  - Manual → heading = title; snippet from `content` using `snippet_len`.

### 2) Snippets + field selection
- Search returns heading + snippet (320 char default; max 640; up to 2 sentences).
- `entries/read` gains `fields` and `snippet_len`; defaults return previews instead of full text unless fields explicitly include full bodies. This preserves “rich read” for cases that still need full content.
- `fields` semantics:
  - `/api/v1/search`: defaults include the standard result set + snippet; `fields` is additive (e.g., `["playbook"]` also returns full playbook alongside snippet).
  - `/api/v1/entries/read`: `fields=None` → full bodies (current behavior); `fields=["preview"]` → only previews/snippets; other explicit fields are allowlisted.
- `snippet_len` applies to snippet generation only; full bodies are unaffected when explicitly requested.

### 3) Session memory (per MCP process/conversation)
- API accepts `session_id` (or header `X-CHL-Session`) to track `viewed_ids`, `cited_ids`, `last_queries`.
- Options: `hide_viewed` (drop seen hits) and `downrank_viewed` (score penalty, default true).
  - Interaction:  
    - `hide_viewed=true` → remove viewed entries; ignore `downrank_viewed`.  
    - `hide_viewed=false`, `downrank_viewed=true` → apply score factor 0.5 to viewed hits.  
    - `hide_viewed=false`, `downrank_viewed=false` → no session effect.
- Introspection endpoints:
  - `GET /api/v1/search/session?session_id=...` → stored lists (404 if expired).
  - `POST /api/v1/search/session/cited` with `{session_id, ids}`.
- Store: in-memory LRU (500 sessions, 60m idle TTL), per-process only (restart wipes).
- Thread-safe via simple lock; no disk or multi-tenant concerns.
- Session precedence: header `X-CHL-Session` wins; else body `session_id`; else no session (`session_applied=false`).

### 4) Quality rails
- Enforce `min_score` (default 0.50 vector, 0.35 text) and emit warning when top_score below threshold.
- `write_entry` auto-runs duplicate check (750 ms budget, advisory) with decision tree:  
  - Timeout → proceed, add warning `duplicate_check_timeout=true`.  
  - Max score ≥ 0.85 → write, return duplicates + `recommendation="review_first"`.  
  - 0.50–0.84 → write, return duplicates as FYI.  
  - <0.50 → write normally.  
- Provider transparency: always return `provider`, `search_mode`, and optional `provider_hint` (e.g., “Vector unavailable; text fallback used”).
- Provider hint examples: vector unavailable; FAISS needs rebuild; low confidence (top score below threshold); search timeout/partial results.
- write_entry flow: validate → run duplicate check (async, 750ms cap) → insert → return duplicates/recommendation; no opt-out in v1.1; expect +50–750ms latency.

### 5) MCP wiring (no user action)
- Generate `session_id` once per MCP process (uuid4 hex) in `src/mcp/core.py` (or `server.py`) and inject `X-CHL-Session` on all API calls.
- CHLAPIClient change: add optional `session_id` ctor param; `_raw_request` injects header automatically. MCP handlers stay unchanged.
- Allow override via `CHL_SESSION_ID` env; optional internal `rotate_session()` helper if we later want per-conversation resets.
- Scope wording: per MCP process (can rotate per conversation later without API change).

## Rationale
- Smaller, snippet-focused payloads cut LLM context/token spend and speed scans.
- Session-aware ranking keeps results fresh within a conversation without user prompts.
- Built-in guardrails reduce low-quality responses and accidental duplicates.
- Local-only scope lets us break the old schema for a cleaner v1.1.

## Rollout (ordered)
1) Implement unified `/api/v1/search` + `entries/read` schema changes, including snippets/fields and min_score handling. Update Pydantic models.
2) Add session store + endpoints; wire search/read to update `viewed_ids`; expose `session_applied`.
3) Add duplicate-check on `write_entry` with warnings; surface provider/search_mode metadata and score warnings.
4) Update MCP core/CHLAPIClient to auto-generate/inject `session_id`; document env override.

## Testing Notes
- Unit: request/response models, min_score filtering, snippet truncation edge cases, session cache eviction, duplicate-check timeout path, score-penalty math.
- Concurrency: lock around session mutations; concurrent access sanity tests.
- Integration: vector vs text fallback, hide/downrank viewed combinations, write_entry returning duplicates/warnings.
- MCP: client injects `X-CHL-Session`, session_applied flag true, LRU expiry behavior, session expiry mid-run, invalid/malformed session ignored gracefully, 501st session evicts oldest.
- Load (lightweight): 500-session LRU performance sanity.

## Schema Sketches (for clarity)
```python
class UnifiedSearchRequest(BaseModel):
    query: str
    types: list[Literal["experience","manual"]] = ["experience","manual"]
    category: str | None = None
    limit: int = Field(10, ge=1, le=25)
    offset: int = Field(0, ge=0)
    min_score: float | None = None
    filters: dict | None = None  # AND semantics; exact match; keys: author, section; null values ignored
    snippet_len: int = Field(320, ge=80, le=640)
    fields: list[str] | None = None
    hide_viewed: bool = False
    downrank_viewed: bool = True
    session_id: str | None = None

class UnifiedSearchResult(BaseModel):
    entity_id: str
    entity_type: Literal["experience","manual"]
    title: str
    section: str | None = None
    score: float  # always present for search results
    rank: int
    reason: str
    provider: str
    degraded: bool = False
    hint: str | None = None
    heading: str | None = None
    snippet: str | None = None
    author: str | None = None
    updated_at: str | None = None

class UnifiedSearchResponse(BaseModel):
    results: list[UnifiedSearchResult]
    count: int               # len(results)
    total: int | None = None # optional if expensive to compute
    has_more: bool
    top_score: float | None = None
    warnings: list[str] = []
    session_applied: bool = False
```

## Call-Site Impact (breaking list)
- `/api/v1/search`: MCP handlers will be updated to send the new request shape and consume richer results; no other callers identified.
- `/api/v1/entries/read`: backward-compatible additions (`fields`, `snippet_len`, `session_id`), so existing calls keep working.

## Open questions resolved
- Provider hints: surfaced when degraded/fallback modes are used (e.g., vector unavailable).
- Session scope: per MCP process; can later rotate per conversation without API change.
- Limit cap rationale: `limit` capped at 25 to keep response/token size small for LLM contexts.
- Filters: AND semantics, exact match, null ignored, no partials in v1.1.
