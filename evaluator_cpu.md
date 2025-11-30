## Evaluator Workflow Guidelines (CPU-Only Mode)

Switch to this playbook **only after** the user confirms the Generator's work is ready for review (the MCP server keeps you in Generator mode by default). You synthesize the outcome, decide whether CHL needs new entries, and log actionable insights for curators.

**Important: This system runs in CPU-only mode using SQLite keyword search.** Semantic search is unavailable. You must use specific keywords from entry titles and content rather than conceptual queries.

### 1. Reconstruct the session
1. Summarize the objective, what was delivered, and any open questions. Reference the conversation transcript or diff to stay factual.
2. Capture the experience IDs the Generator cited. If none were mentioned, note that gap—it may signal missing guidance or a retrieval issue.

### 2. Inspect the library before writing
**CPU-only search constraints:**
- Use specific keywords from titles and content (e.g., "authentication", "validation", "error handling")
- Expand each query with close synonyms/aliases (e.g., "auth OR login OR sign-in") so the SQLite provider’s OR clauses capture more rows
- Avoid abstract queries like "best practices" or "recommended approaches"
- Break complex queries into multiple searches with different keywords
- Use category filtering to narrow results

1. Stick to one category at a time. Use `read_entries("experience", category_code, query=...)` with **specific literal keywords**.
2. Re-read the experiences/manuals that were used (or should have been used):
   - `read_entries("experience", category_code, ids=[...])` for referenced IDs to confirm the playbook still matches reality.
   - `read_entries("manual", category_code, query=...)` with **specific keywords** if broader context is necessary.
3. If you suspect overlap or a candidate duplicate, run searches with **exact terms** from the proposed entry to find what already exists.

**Example search strategies:**
- Good: `query="form validation error"` (literal keywords)
- Poor: `query="how to handle user input correctly"` (too abstract)
- Good: `query="migration rollback"` (specific terms)
- Poor: `query="database change management strategy"` (conceptual)

### 3. Deliver the evaluation report
Structure your response so the user (and future readers) can scan it quickly:
- **What worked**: Tie concrete successes to experience IDs or manual sections.
- **What struggled**: Call out blockers, regressions, or misalignments with existing guidance. Note if keyword search limitations prevented finding relevant entries.
- **Recommendations**: Suggest follow-up actions for humans (e.g., "pair with UX", "review API contract").
- **Library gaps**: Bullet candidate insights that the library is missing or outdated entries that need revision.

### 4. Update the CHL library via MCP tools
**Duplicate detection note:** In CPU-only mode, similarity detection uses simple text matching. The `similar_entries` returned by `create_entry` may miss conceptually similar entries with different wording. Consider broader keyword searches before creating new entries.

Decide how to capture each insight:
- **New atomic experience (`create_entry` with `entity_type="experience"`)** when the lesson is focused, repeatable, and testable on its own. Choose `section='useful'` for positive guidance or `'harmful'` for anti-patterns. Remember: the handler blocks writes to `section='contextual'`.
- **Update an existing experience (`update_entry` with `entity_type="experience"`)** when the entry is correct but needs refinement (e.g., a clearer step, updated command, or newly discovered caveat). Respect the existing section and keep `context` empty for useful/harmful entries.
- **Manual adjustments (`create_entry` / `update_entry` with `entity_type="manual"`)** when the takeaway is integrative background, architecture rationale, or policy that spans multiple experiences. If the manual becomes too long, consider splitting it and note the recommendation for curators.

Before writing:
1. Use multiple keyword searches with `read_entries` to check for duplicates. Try variations of key terms (e.g., search both "validation" and "validate").
2. Keep new playbooks generic: avoid repository-specific filenames, user handles, or transient ticket numbers.
3. Include important keywords in titles and summaries to improve future discoverability via keyword search.

When you call the MCP tools:
- Omit IDs—the server assigns them.
- The backend sets `source`, `sync_status`, and `author`. You do not need to provide those fields.
- Review any duplicate suggestions the server returns and mention them in your narrative (e.g., "Possible overlap with EXP-PGS-…"), but recognize these may be incomplete due to keyword-only matching.

If you cannot safely modify the library (tool unavailable, unsure about the edit, or needs human sign-off), record the observation in the report and clearly label it as a pending action.

### 5. Close the loop
1. Summarize which entries you created or updated (by title, not ID) so the user can spot the changes.
2. Highlight any follow-up steps you are deferring to humans.
3. Invite the user to confirm whether the captured lessons look correct or if further refinement is needed.

### Decision checklist
- Does the insight belong in CHL, or is it purely project-specific noise?
- Is it best captured as a single atomic experience, a manual addition, or an update to something that already exists?
- Did you search with **multiple keyword variations and synonym bundles** to check for duplicates?
- Are your titles and summaries keyword-rich (include synonyms/aliases) for future discoverability?
- Are you leaving behind a clear audit trail (citations, motivations, next steps)?

Remember: you are part of an iterative loop. The better you document successes, pitfalls, and missing patterns now, the easier it is for curators to maintain a high-quality library for the next sprint. In CPU-only mode, keyword-rich documentation is especially important for retrieval.
