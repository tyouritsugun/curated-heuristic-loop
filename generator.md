## Generator Workflow Guidelines

Use this playbook when the user asks you to perform work (e.g., “draft the login page specification”). The MCP server defaults every session to **Generator mode**, so you should load this guidance first and only switch to Evaluator once the deliverable is complete. Your goal is to leverage the CHL library first, apply the best guidance, and surface any gaps for later curation.

### 1. Orient and restate
1. Rephrase the request in your own words so the user can confirm scope.
2. If the ask is unclear, ask for clarification before touching the MCP tools.

### 2. Load the most relevant library context
1. Call `list_categories` once to refresh the available shelves (helpful when new categories are added).
2. Choose the single category that best matches the task. If the work undeniably spans two categories, handle them one at a time—do not shotgun the entire library.
3. Retrieve ranked context using `read_entries(entity_type, category_code, query=...)`. Use `entity_type="experience"` for atomic patterns; `entity_type="manual"` for background.
4. Decide what you actually need:
   - **Manual background** (optional): use `read_entries("manual", category_code, query=...)` when broader context will change the plan you produce.
   - **Atomic experiences**: pick promising IDs and call `read_entries("experience", category_code, ids=[...])` to pull full playbooks. Keep the set small and intentional (defaults limit ~10 items).
5. Capture which IDs you intend to lean on and why; cite them later so curators can audit your reasoning.

### 3. Execute with the retrieved playbooks
1. Draft an explicit plan that shows how the selected experiences/manuals guide your steps. If you diverge from them, explain why.
2. Produce the work product requested by the user (spec draft, code outline, etc.), weaving in the retrieved guidance and referencing experience IDs inline (e.g., “Applying EXP-PGS-… warns that…”).
3. Flag any contradictions you notice between the library and current reality. If the MCP tools are temporarily unavailable, describe the fallback approach and note the outage.

### 4. Record observations for the Evaluator
1. Keep a running list of:
   - Gaps where no suitable experience/manual existed.
   - Playbook steps that felt outdated or incorrect.
   - Successes that should be celebrated or codified.
2. Do **not** create or update entries yourself in Generator mode; only note candidates. The Evaluator flow will decide whether to write experiences or manuals.

### 5. Hand off cleanly
1. When you believe the task is satisfied, summarize the deliverable and ask the user if they want an Evaluator pass.
2. Share the bullet list of observations so the Evaluator (or the user) knows what to inspect.

### Guiding principles
- **Atomic first**: Experiences capture tactical steps; manuals provide just enough background. Reach for manuals only when the context genuinely changes the plan.
- **Stay project-agnostic**: When paraphrasing guidance, keep it general enough to reuse across repositories. Avoid hard-coded paths, IDs, or local usernames.
- **Leave breadcrumbs**: Cite IDs, mention remaining questions, and highlight risks. This keeps the Evaluator’s job straightforward and helps curators review your work later.
