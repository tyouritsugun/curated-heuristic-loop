## Generator Workflow (Search-First)

Load these notes whenever you are in Generator mode. The goal is to surface the most relevant experiences and manuals before you draft anything.

### 0. Clarify task intent (before rushing to code)

When user mentions bugs, errors, or problems, pause and clarify what they want:

**Ask yourself:**
- Are they asking me to: fix code, write a bug ticket, investigate, or document?
- Did they explicitly request code changes, or just reporting an issue?

**Red flags that suggest "don't rush to code":**
- User says "I found..." / "I noticed..." / "There's a bug..."
- User pastes error output without explicit fix request
- Context suggests documentation task (e.g., after discussing tickets)

**If unclear, ask explicitly:**
"Would you like me to: (1) Fix this issue, (2) Write a bug ticket, or (3) Investigate further?"


### 1. Align on the request
1. Restate the user's ask in your own words and confirm any missing details.
2. Capture the intended persona, output format, and success criteria in your scratchpad—this informs the queries you compose next.

### 2. Pick a single shelf
1. Run `list_categories` only if you need a refresher of the available shelves.
2. Choose the one category that best matches the work. If you truly need two, finish one pass end-to-end before switching.

### 3. Craft two-phase queries

Search uses two phases:
1. **SEARCH phrase** (fast vector search) → casts wide net with keywords
2. **TASK context** (smart reranking) → picks most relevant for your goal

**Query format:**
```
[SEARCH] authentication implementation patterns
[TASK] I want to implement secure OAuth2 login with refresh tokens. Will this help?
```

**Basic principle:**
- SEARCH: Combine [process] + [domain] (3-6 words)
  - Examples: "migration planning", "performance troubleshooting", "feature rollout", "API design"
  - Broader beats narrow; patterns beat technologies
- TASK: Frame as a natural question asking if the experience helps
  - Format: "I want to {goal}. Will this help?" or "I need to {goal}. Will this experience help?"
  - Adapt phrasing to task type (implement/troubleshoot/review/analyze)

**Issue 2–3 variants** with different SEARCH phrases to explore the semantic space.

**Examples:**

| User Request | Query |
|---|---|
| Implement OAuth2 login | `[SEARCH] authentication implementation patterns`<br>`[TASK] I want to implement secure OAuth2 login with refresh tokens. Will this help?` |
| Fix slow database queries | `[SEARCH] query performance troubleshooting`<br>`[TASK] I'm trying to optimize slow Postgres queries in production API. Will this help?` |
| Draft page specification | `[SEARCH] specification workflow`<br>`[TASK] I want to draft page specification for user dashboard. Will this help?` |
| Scan existing specs | `[SEARCH] specification review patterns`<br>`[TASK] I need to scan the codebase and audit existing specifications. Will this manual help?` |

If top score <0.50, reformulate the SEARCH phrase.

### 4. Load experiences (size-based strategy)
1. Call `list_categories()` to check entry counts
2. **Small category (<20 entries)**: Load all at once
   - `read_entries(entity_type="experience", category_code=..., fields=['playbook'])`
   - Gets complete knowledge base in one call
3. **Large category (>=20 entries)**: Progressive loading
   - Load previews: `read_entries(entity_type="experience", category_code=...)`
   - Fetch full content by IDs: `read_entries(..., ids=['EXP-xxx'], fields=['playbook'])`
   - Search if needed: `read_entries(..., query="[SEARCH] ... [TASK] ...")` (top score <0.50 → reformulate)

### 5. Run a duplicate check before writing
1. Before calling `write_entry`, use `check_duplicates` with the proposed `title` and full `playbook`/`content`:
   - `check_duplicates(entity_type=\"experience\", category_code=..., title=..., content=..., limit=1)`
2. If the top candidate has a high similarity score (e.g., >0.85):
   - Prefer updating/merging the existing entry via `update_entry` when appropriate.
   - If you still decide to write a new entry (because it captures a genuinely different pattern), explicitly explain why in your response.
3. If no strong candidate is returned, proceed to `write_entry` as usual.

### 6. Layer manuals only when they change the plan
- Use `entity_type=\"manual\"` when broader background will materially affect your deliverable (process overviews, terminology, regulatory context).
- Limit yourself to the top 1–2 manuals; if nothing useful appears, treat it as a knowledge gap and flag it later.

### 7. Check coverage and gaps
- If every variant still yields weak matches, log the gap (include queries tried and their best scores) so an Evaluator can curate a new entry.
- Otherwise, proceed with the work product, weaving in the cited experience IDs for traceability.

### Guiding principles
- **Atomic first**: default to experiences; they carry the actionable steps.
- **Seek patterns, not answers**: manuals and experiences teach how to work; they will not contain the exact schema, spec, or code your user is asking for.
- **Stay concise**: shorter, cleaner queries produce better rankings.
- **Leave breadcrumbs**: record which IDs informed your work and any missing coverage you discovered.
- **Scope of knowledge**: The KB contains manuals/experiences (shared heuristics), not domain- or customer-specific content. There is no product-specific spec; only general “how to design a page spec” patterns organized by category.
