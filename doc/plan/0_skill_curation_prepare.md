# Skill curation prep (plan 0)

## Goals
- Avoid name collisions between experience and skill curation tables.
- Freeze/expand the category taxonomy to cover most software development work before team-scale curation.
- Add an env/config gate to enable/disable skill read/write + import/export.

## 0) Prerequisites and dependencies
### Execution order
1. Section 2 (category taxonomy) must complete before Section 3 (skills flag).
2. Section 1 (table rename) happens during plan 2 implementation.
3. Sections 2 and 3 must complete before plan 1 starts.

### External dependencies
- Google Sheets access (IMPORT_SPREADSHEET_ID configured).
- Database write access to `chl.db`.
- MCP server restart capability for config changes.
- Guidelines are read directly from repo files via MCP `read_guidelines` (no skills dependency).

### Breaking changes
- Category import becomes validation-only (no creation).
- Skills can be fully disabled via config (affects existing workflows).

## 1) Experience table prefixes (avoid collisions)
### Status: deferred to plan 2
### Proposed naming
- `curation_decisions` → **`experience_curation_decisions`**
- `split_provenance` (experience) → **`experience_split_provenance`** (already in schema; keep as canonical)
- Future skill tables:
  - `skill_curation_decisions`
  - `skill_split_provenance`

### Why
We will create skill-level curation tables. Prefixing experience tables prevents ambiguity in SQL, exports, and scripts.

### Code surfaces to update (when we implement)
- SQLAlchemy schema: rename `CurationDecision.__tablename__`.
- Schema bootstrapping/migrations: ensure new table name and data migration or alias.
- Scripts in `scripts/curation/`:
  - `scripts/curation/export_manual_queue.py` (SQL query)
  - `scripts/curation/merge/init_curation_db.py` (table list)
- Docs mentioning curation_decisions:
  - `doc/experience_curation.md`
  - `doc/plan/2_skill_curation.md` (decision log naming)

### Migration plan
- No migration support needed. The rename impacts only temporary curation tables (intermediate outputs).

## 2) Category expansion (prepare a comprehensive taxonomy)
We should stop allowing ad-hoc category creation (single-user convenience) because it becomes a governance risk for team curation and import/export. The taxonomy should be broad enough that most skills and experiences have a reasonable home without creating new categories.

### Proposed category set (software development coverage)
(Keep existing 12; add the following to reach a smaller but more complete baseline.)

### Existing 12 categories (current baseline)
Format: Code | Name | Description
- FPD | figma_page_design | Figma page layout and UI composition
- DSD | database_schema_design | Relational schema and data modeling
- PGS | page_specification | UI spec writing and requirements for pages
- TMG | ticket_management | Ticket writing, triage, and workflow
- ADG | architecture_design | System architecture planning and diagrams
- MGC | migration_code | Code migration planning and execution
- FTH | frontend_html | HTML/CSS implementation details
- LPW | laravel_php_web | Laravel/PHP web development patterns
- PGT | python_agent | Python agent design and orchestration
- PPT | playwright_page_test | Playwright-based UI testing
- EET | e2e_test | End-to-end testing workflows
- PRQ | pull_request | PR creation, review, and merge practices

**Product & planning**
- REQ | requirements_specification | Functional/non-functional requirements capture
- RMP | roadmap_planning | Milestones, sequencing, release planning

**UX & design**
- DSS | design_systems | Components, tokens, and style guides
- ACC | accessibility | WCAG, keyboard nav, screen readers
- FGD | figma_design | Figma workflows, prototyping, handoff

**Frontend engineering**
- FRA | frontend_architecture | State, routing, build structure
- WPF | web_performance | Core Web Vitals, perf budgets

**Backend & data**
- API | api_design | REST/GraphQL design and versioning
- BEA | backend_architecture | Service boundaries and patterns
- DBM | database_modeling | Schema, indexing, query design

**Infrastructure & operations**
- DEP | deployment_release | CI/CD, rollout, rollback
- OBS | observability | Logging, metrics, tracing
- SRE | reliability_sre | SLOs, incident response
- SEC | security_review | Threat modeling and review

**Testing & quality**
- TST | testing | Unit/integration/QA/test infra
- Keep existing EET and PPT for end-to-end coverage.

**Engineering process**
- CRV | code_review | PR workflow, feedback practice
- DOC | documentation | READMEs, runbooks, knowledge base
- RFG | refactoring | Tech debt and code cleanup
- TRS | technical_research | Spikes, POCs, vendor eval

**AI/agent workflows**
- PRM | prompting_workflows | Prompting patterns and best practices
- LLM | llm_tooling | RAG, fine-tuning, eval tooling
- AEV | agent_evaluation | Agent testing and metrics

**External tool workflows**
- TKT | ticket_edit | Jira/Linear/GitHub issue edits
- GHF | github_flow | Branch/PR flow and GitHub Actions

### Notes
- Keep codes 3–4 chars, uppercase. Avoid overlaps with existing codes.
- Some existing categories overlap (e.g., `frontend_html`, `figma_page_design`, `page_specification`). We can keep them but avoid adding near-duplicates.
- No UNCAT fallback; always map to a defined category.
- Category import/export should be removed entirely.

### Implementation approach (concise)
- Define canonical taxonomy in code (single source of truth), e.g. `src/common/config/categories.py`.
- Seed categories during setup from the canonical list.
- Import validates category codes against the canonical list; unknown codes block import with a clear remediation message.
- Export uses the canonical list (not DB contents) to ensure shared consistency.

Example structure for `categories.py`:
```python
from typing import TypedDict, List

class CategoryDefinition(TypedDict):
    code: str
    name: str
    description: str

CATEGORIES: List[CategoryDefinition] = [
    {"code": "PGS", "name": "page_specification", "description": "UI spec writing and requirements for pages"},
    {"code": "REQ", "name": "requirements_specification", "description": "Functional/non-functional requirements capture"},
]

def get_category_by_code(code: str) -> CategoryDefinition | None:
    return next((c for c in CATEGORIES if c["code"] == code), None)

def get_all_codes() -> List[str]:
    return [c["code"] for c in CATEGORIES]
```

## 3) Skill enable/disable flag (config/env)
### Requirement
- New config parameter: `skills_enabled` (default `true`).
- When disabled:
  - MCP should not expose skill read/write/update.
  - UI/API should hide/disable skill import/export actions.
  - Import/export should skip skill sheets (do not create or load skill data).

### Code surfaces to update (when we implement)
- Config/env:
  - `src/common/config/config.py` (new flag, defaults)
  - `.env.sample` + docs
- MCP:
  - `src/mcp/handlers_entries.py` (block skill read/write/update)
  - `src/mcp/core.py` + `src/mcp/server.py` (tool registry gating)
- API:
  - `src/api/routers/entries.py` (block skill create/update/search)
  - `src/api/routers/categories.py` (skill counts)
- Import/export:
  - `src/api/services/operations_service.py` (Sheets + Excel import/export paths)
  - `src/api/services/import_service.py` (skip skills + category validation)
  - `src/common/api_client/client.py` (export endpoints)
  - `src/api/routers/entries.py` (CSV export)
- UI:
  - `src/api/routers/ui_common.py` (settings fields)
  - `src/api/templates/*` (hide skills options, ops cards)
- Embeddings/indexing:
  - `src/api/gpu/embedding_service.py` (skip skill jobs)
  - `src/api/services/worker_control.py` (queue counts)

### Behavioral spec (concise)
- MCP: no skill tools registered; skill requests return clear “skills disabled” error.
- API: skill endpoints return 404 when disabled; category skill_count reports 0.
- Import/export: skip skill sheets (Sheets/Excel/CSV) when disabled.
- Embeddings/indexing: do not enqueue or process skill embeddings when disabled.
- UI: show skills as read-only with a warning; hide write/import/export actions.
- Data preservation: disabling skills does not delete existing skill data.
- Guidelines: provide a dedicated MCP `read_guidelines` tool that reads `generator.md`, `evaluator.md`, `evaluator_cpu.md` from the repo (no skills dependency).

## 4) Success criteria & testing
### Categories
- Canonical taxonomy defined in code and seeded on setup.
- Import blocks unknown category codes with clear remediation.
- Export includes full canonical taxonomy.

### Skills toggle
- `CHL_SKILLS_ENABLED` gates all skill read/write/import/export/indexing paths.
- Disable → re-enable restores functionality without data loss.
- UI and MCP clearly reflect availability.

### Integration checks
1. Fresh install with skills enabled/disabled.
2. Import/export with skills disabled (skills skipped).
3. Import with unknown categories (blocked with error).

## 5) Dependencies & risks (concise)
### Dependencies
- Plan 1 depends on Section 2 (taxonomy) and Section 3 (skills toggle).
- Skills toggle depends on MCP restart and a test environment.

### Risks
- Taxonomy incomplete → imports blocked. Mitigation: keep list broad and allow team updates via PR.
- Skills toggle leaks via unguarded path. Mitigation: audit surfaces + tests.

## Clarifications / Notes
- `skills_enabled` lives in `.env` only (no `scripts_config.yaml` override).
- UI should show skills as read-only with a clear warning; hide write/import/export actions.
- API should return 404 for skill endpoints when disabled; MCP should not expose skill tools.
- No migration support for `curation_decisions` → `experience_curation_decisions` (temporary tables only).
- When `skills_enabled=false`, disable skill embeddings/indexing jobs explicitly.
- Do not version categories; rely on code-defined taxonomy and guidance for team-shared updates.
