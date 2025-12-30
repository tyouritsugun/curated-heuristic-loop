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

### 2. Decide search scope
1. **Default to category-scoped** when the task clearly maps to a shelf (e.g., page spec → PGS, database → DSD). This keeps results on-topic and avoids token waste.
2. **Use global search** only if the category is unclear or the ask spans multiple domains. Omit `category_code` in that case.
3. Run `list_categories` if you need to check shelf sizes to choose a loading strategy.

### 3. Load experiences
**Category-scoped (preferred when category is clear):**
1. Call `list_categories()` to check entry counts.
2. Small category (<20): `read_entries(entity_type="experience", category_code=..., fields=['playbook'], limit=25)` to fetch full bodies (default limit is 10).
3. Large category (>=20): Load previews first (`read_entries(entity_type="experience", category_code=...)`), then fetch the chosen IDs with `fields=['playbook']`.

**Global or ambiguous:**
- `read_entries(entity_type="experience", query="[SEARCH] ... [TASK] ...")` (returns previews by default)
- Omit `category_code` to search all categories
- If top score <0.50, reformulate the SEARCH phrase and retry

**Two-step query (only when you are searching):** keep it terse:
```
[SEARCH] {process + domain}      # e.g., "query performance troubleshooting"
[TASK] I want to {goal}. Will this help?
```
Issue 2–3 variants if top score <0.50.

### 4. Run a duplicate check before creating
1. Before calling `create_entry`, use `check_duplicates` with the proposed `title` and full `playbook`/`content`:
   - `check_duplicates(entity_type=\"experience\", category_code=..., title=..., content=..., limit=1)`
2. If the top candidate has a high similarity score (e.g., >0.85):
   - Prefer updating/merging the existing entry via `update_entry` when appropriate.
   - If you still decide to create a new entry (because it captures a genuinely different pattern), explicitly explain why in your response.
3. If no strong candidate is returned, proceed to `create_entry` as usual.
4. **CPU mode:** if `read_entries` meta shows `search_mode='cpu'`, skip `check_duplicates`; instead load relevant entries in that category via keyword `read_entries` and manually compare before writing.

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
- **Scope of knowledge**: The KB contains manuals/experiences (shared heuristics), not domain- or customer-specific content. There is no product-specific spec; only general “how to design a page spec” patterns organized by category.
