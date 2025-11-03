## Generator Workflow (Search-First)

Load these notes whenever you are in Generator mode. The goal is to surface the most relevant experiences and manuals before you draft anything.

### 1. Align on the request
1. Restate the user’s ask in your own words and confirm any missing details.
2. Capture the intended persona, output format, and success criteria in your scratchpad—this informs the queries you compose next.

### 2. Pick a single shelf
1. Run `list_categories` only if you need a refresher of the available shelves.
2. Choose the one category that best matches the work. If you truly need two, finish one pass end-to-end before switching.

### 3. Craft high-signal queries

- Remember the library holds **process guidance**, not domain knowledge about the specific artifact you are creating. Query for the *pattern* (e.g., “database schema authoring playbook”), not the exact schema you need to build.
- Brainstorm persona / task / need for yourself, but send **only** the final noun-heavy string to `read_entries`.
- Issue 2–3 variants that cover:  
  • the concrete outcome you want (`access control spec heuristics`)  
  • a synonym set or alternate phrasing (`permissions filtering checklist`)  
  • a risk or failure phrasing if relevant (`access control retrofit pitfalls`)
- Keep each query short (<12 words), focused on nouns and verbs that could appear in titles or playbooks.

### 4. Pull experiences first
1. Call `read_entries(entity_type=\"experience\", category_code=..., query=...)` for each variant.
2. Inspect the ranked list: if the top score is weak (<0.50) or irrelevant, adjust the query immediately instead of skimming everything.
3. When you find strong hits, fetch the full text with `read_entries(..., ids=[...])` and note the IDs you intend to use.

### 5. Layer manuals only when they change the plan
- Use `entity_type=\"manual\"` when broader background will materially affect your deliverable (process overviews, terminology, regulatory context).
- Limit yourself to the top 1–2 manuals; if nothing useful appears, treat it as a knowledge gap and flag it later.

### 6. Check coverage and gaps
- If every variant still yields weak matches, log the gap (include queries tried and their best scores) so an Evaluator can curate a new entry.
- Otherwise, proceed with the work product, weaving in the cited experience IDs for traceability.

### Guiding principles
- **Atomic first**: default to experiences; they carry the actionable steps.
- **Seek patterns, not answers**: manuals and experiences teach how to work; they will not contain the exact schema, spec, or code your user is asking for.
- **Stay concise**: shorter, cleaner queries produce better rankings.
- **Leave breadcrumbs**: record which IDs informed your work and any missing coverage you discovered.
