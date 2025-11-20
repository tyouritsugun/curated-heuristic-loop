# CHL Agent Instructions (MCP Clients)

Copy/paste the parts below into your code assistant’s common instructions (Claude Code, Cursor, Codex, etc.) so it stays consistent across sessions.

## Startup Checklist (every session)
- Confirm the API server is running at `CHL_API_BASE_URL` (default `http://localhost:8000`); if unsure, ask the user to start it.
- Call `list_categories()` once to warm the cache; if it fails, report the HTTP error and stop.
- Load generator/evaluator guidance with `get_guidelines()` before doing work; stay in generator by default.
- When you search, always call `read_entries(...)` through MCP (never read the DB directly).

## During Tasks
- Use categories: call `list_categories()` first; if the request includes one, honor it.
- For retrieval, prefer a single good `read_entries(query=..., category_code=...)` call instead of guessing from memory.
- When editing/adding guidance, run `check_duplicates(...)` before `write_entry(...)` to avoid collisions.
- Echo which provider is active (semantic vs keyword) if the response includes `search_mode` or vector availability.

## After Tasks (reflection & curation)
- Prompt the user to summarize outcomes and decide whether to:
  - Add an atomic experience (`write_entry(entity_type="experience", ...)`)
  - Update a manual (`write_entry(entity_type="manual", ...)`)
- If writing, include `category_code`, concise title, and clean playbook/content; use `check_duplicates(...)` first.
- Remind the user they can rebuild embeddings/FAISS via `/operations` or scripts if vectors are pending (GPU mode only).

## Safety & Boundaries
- MCP is HTTP-only; do not attempt direct DB/FAISS access.
- Stop and surface errors if MCP calls fail; don’t guess silently.
- If instructions conflict, ask the user to clarify before proceeding.
