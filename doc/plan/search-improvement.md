# Search and Rerank Improvements for CHL Toolset

## Goals
- Cut prompt boilerplate for LLM assistants.
- Reduce token waste by returning only what is needed.
- Avoid repeat surfacing of already-reviewed entries.
- Increase trust via lightweight quality rails.

## Proposed Changes (v1.1 scope)
1. Unified search endpoint  
   - `search_entries(category?, types=[experience|manual], query, min_score?)`  
   - Returns: type, score, source, section, heading, snippet, entry_id.  
   - Support filters: `author`, `section`, `limit`, `offset`.
2. Snippets and fields selection  
   - Each hit includes 1–2 matched sentences with heading context.  
   - `read_entries(ids, fields=[title, summary, content])` to fetch only requested fields.
3. Session memory  
   - Track `viewed_ids` and `cited_ids` per conversation.  
   - Option to down-rank or hide already seen IDs.  
   - Add `show_session_context()` to recall what was fetched.
4. Quality rails  
   - Warn when top score < 0.50 and suggest a reformulated SEARCH phrase.  
   - Auto-run `check_duplicates` on `write_entry` and return best match + score.

## Rationale
- Fewer calls and smaller payloads improve responsiveness and reduce LLM context cost.
- Snippets and headings make reranked lists actually scannable.
- Session awareness keeps search fresh and prevents re-reading.
- Guardrails steer the assistant toward better queries and avoid duplicate entries.

## Suggested Rollout
1) Ship unified search + snippets + min_score filter.  
2) Add fields selection and session memory.  
3) Layer in quality rails and auto-duplicate check.  
4) Iterate reranker with click/expand telemetry once basics land.
