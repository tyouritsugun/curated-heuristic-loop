# CHL Toolset Refinements Proposal

## Overview

This document proposes concrete, incremental improvements to the CHL toolset to make it a more effective knowledge system for product, design, and engineering. It focuses on workflow/governance, structure and validation, discovery, in‑context integration, and practical quick wins that can be rolled out with low risk.

Terminology
- CHL Entry: A single knowledge record (e.g., manual, experience).
- Category: A shelf grouping entries (e.g., PGS). Category may define its own schema.
- Frontmatter: Required metadata at the top of an entry used for governance and search.

## Objectives

- Keep knowledge current and trusted (review cycles, auditability).
- Improve findability (search, facets, related content, synonyms).
- Reduce friction to contribute (authoring UX, templates, validation).
- Bring knowledge into the workflow (PRs, failures, file context).
- Measure usage and identify gaps (feedback, metrics, digests).

## Quick Wins (Do First)

1) Required frontmatter on every entry
- Fields: `title`, `owner`, `status`, `review_date`, `tags[]`, `links[]` (optional), `related_ids[]` (optional)
- Enforce in write/update API and pre‑commit hooks; block publish without required fields.

2) Stale docs reminder
- Nightly job that flags entries with `review_date < today` and notifies `owner` via Slack/Teams.
- Expose “stale” badge in list and read views.

3) CI/link checker
- Validate referenced CHL IDs exist; check file paths resolve; warn on broken external links.
- Fail CI for new/updated entries with broken references.

4) Related entries by tags
- On read view, show “Related” based on tag overlap and simple TF‑IDF over title/summary.

5) Status badge and last‑reviewed
- Display `status` and `review_date` at the top of each read view.

6) Spec skeleton generator
- Command/palette action to instantiate a page spec skeleton from a chosen template (e.g., PGS list/detail/edit). Inserts numbered section placeholders and standard headers (Overview, Navigation, Access Control, Screen Components, Business Rules, Open Questions).

7) .ai-docs linter (images/numbering/links)
- Validate that numbered sections match annotated images in `.ai-docs/screen_spec/**/images/*`.
- Check cross-links to other specs and embedded image paths. Fail CI on mismatches; provide a fix suggestion.

8) Spec completeness gate
- Pre-commit/CI rule for page specs that enforces: FIGMA link present, image(s) referenced, numbering sections exist, and Access Control subsection included when relevant.

9) Enum/options auto-extractor
- Parse DB-change docs (e.g., status enums in 1101) and expose them for spec authors (CLI insert or editor quick-pick) to prefill dropdown option lists.

10) Cross‑doc coherence checks (field/guard/flow)
- Verify that field names used in related specs (e.g., 1101 DB change vs 1102/1103/1104 pages) match exactly.
- Check that stated guards (e.g., split/merge partner assignment constraints) are consistent across specs.
- Validate that navigation and redirect flows align (e.g., 1104 redirects back to 1103 with created id).

11) Open Questions nudge
- Lint for an “Open Questions” section on page specs; warn if missing and offer to insert a short scaffold.

12) Anchor and bilingual heading linter
- Enforce stable anchors for headings in specs (predictable slug rules) to keep cross-links reliable.
- Require bilingual headings in screen specs where the convention applies (EN (JA)); auto‑suggest translations from glossary.

13) Auto‑link suggestions (local paths and CHL IDs)
- When plain text matches a repo path or CHL ID, suggest converting to a verified link; validate on save/CI.

## Workflow & Governance

Statuses
- `draft`: Work in progress, visible to contributors.
- `in_review`: Awaiting approval by designated approvers.
- `published`: Default, visible to all.
- `deprecated`: Replaced or out‑of‑date; retained for historical context.
- `archived`: Hidden from search by default; still accessible via direct link.

Transitions
- draft → in_review → published
- published → deprecated → archived
- published → draft (hotfix), then back to in_review/published
- Enforce transitions in the API; keep an audit log (who, when, from→to, comment).

Approvals
- Per‑category approvers list (emails or group names).
- Require 1+ approval for `in_review → published` (configurable threshold per category).

Review cadence
- Required `review_date`; nightly job pings `owner` 7 and 0 days before.
- Optional `review_interval_days` set at category level to auto‑compute next review.

Deprecation policy
- Deprecated entries must state a `superseded_by` CHL ID; show a redirect banner in the read view.

## Schema & Templates

Category‑level schema
- Each category can define a JSON Schema for its entries (required fields, enums, references).
- The write/update API validates entries against the category schema and returns helpful errors.

Standard frontmatter (suggested)
- `title: string`
- `owner: string` (email or team slug)
- `status: enum(draft|in_review|published|deprecated|archived)`
- `review_date: string(YYYY-MM-DD)`
- `tags: string[]`
- `links: { label: string, url: string }[]`
- `related_ids: string[]` (CHL IDs)
- `superseded_by?: string` (CHL ID)
 - `module?: string` (e.g., `repair_work`, `tasks`)
 - `area?: string` (e.g., `tokyo_west`, `tokyo_east`, `kanagawa`)

Example entry frontmatter (YAML)
```
title: Checklist the spec handoff
owner: kit-team@company
status: published
review_date: 2025-03-01
tags: [handoff, design, spec]
 module: repair_work
 area: tokyo_west
links:
  - { label: FIGMA, url: https://example.com/figma/file }
related_ids: [PGS:EXP-123, PGS:MNL-999]
```

Example minimal JSON Schema (per category)
```
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "required": ["title", "owner", "status", "review_date"],
  "properties": {
    "title": { "type": "string", "minLength": 3 },
    "owner": { "type": "string" },
    "status": { "type": "string", "enum": ["draft", "in_review", "published", "deprecated", "archived"] },
    "review_date": { "type": "string", "pattern": "^\\d{4}-\\d{2}-\\d{2}$" },
    "tags": { "type": "array", "items": { "type": "string" } },
    "links": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["label", "url"],
        "properties": { "label": { "type": "string" }, "url": { "type": "string", "format": "uri" } }
      }
    },
    "related_ids": { "type": "array", "items": { "type": "string" } },
    "superseded_by": { "type": "string" }
  }
}
```

Link/reference checks
- Validate `related_ids` exist; flag circular references.
- For `links`, verify reachable or allow “skip validation” flag per link.

## Search & Discovery

Faceted search
- Facets: `category`, `status`, `tags`, `owner`, `updated_at` bucketized, `review_status` (stale/ok), `feature_path` (derived from repo path), `category_tag` (subcategory labels per category).
- Sort options: relevance, most viewed, recently updated, about to expire.

Hybrid search
- Blend BM25 keyword search with vector semantic search over title + summary snippet.
- Synonym map (e.g., “CI” ~ “pipeline”, “build”), and fuzzy matching for common typos.
- Boosting: prefer more recent entries, owner/team proximity, and entries referenced by current repo path.

Ranking signals
- Path proximity (e.g., prioritize `.ai-docs/screen_spec/**` for screen-spec queries).
- Same‑module match (`frontmatter.module`) and same area.
- Recency decay and explicit view/click‑through signals.
- Inbound links count from other CHL entries and README/specs.
- Author/team affinity based on repository history.

Surfacing
- Pin/feature entries per category; show “Related” on read view.
- Inline badges: `stale`, `deprecated`, `archived` (hidden by default in lists unless included via filter).

## In‑Context Integration

Editor/CLI
- When editing files, suggest relevant CHL entries based on file path, repo, and diff content.
- CLI command to insert CHL links by ID and validate references.
- Insert-from-template: command to pick a spec template and insert a skeleton with numbered sections at the cursor, including FIGMA/link placeholders.
- Autofill options: quick action to pull enum/options from DB-change docs into the current spec (e.g., a status dropdown list).

Linking helpers
- LinkWizard: palette to search CHL entries or repo paths and insert a validated link with readable text and stable anchors.
- One‑click “Insert into doc” buttons on read views to copy canonical snippets (e.g., Access Control pattern, enum tables).
- Apply‑from‑existing: scaffold a new spec from a similar spec (e.g., clone 0901 structure into 1102) with auto‑renumbering.

Enhancements
- Top‑3 suggestions: surface up to three high‑confidence entries with a short “why” explanation (matching terms, same directory, recent usage).
- Favorites and recents: let authors pin favorite playbooks and quickly access recently used entries.
- Inline snippet preview: show a compact preview of the entry’s key steps or checklist before insertion.
- Apply Experience: one‑click insert of selected experience sections (e.g., “Numbering Consistency” checklist) into the current file.
- Comparison Table helper: quick action to insert a 3‑column comparison table when a spec references a “similar page”.
- Graceful fallback: when MCP is unavailable, provide cached last‑used entries and a note to retry syncing.
- Design tokens awareness: when specs reference UI states (e.g., “warning button”), suggest the canonical token/class name from the design system catalog.

PR bot
- Comments on pull requests with suggested CHL links based on changed paths and error strings in CI logs.
- Flags missing references (e.g., spec lacks CHL ID to source guideline).
 - Adds warnings for mismatched image numbering or missing FIGMA link when a page spec is touched.
- Provide rationale and confidence for suggestions; group by file and changed section.
- Detect cross‑doc inconsistencies touched by the PR (field name drifts, guard differences, redirect targets) and link to the relevant CHL entries.

## Validation & Linters

- Numbering/image alignment: ensure section counts match annotated images; catch duplicates or gaps.
- Link checker: validate CHL IDs and file paths; warn on external link failures.
- Section presence: overview, navigation, access control (if multi‑role), related specs, DB reference, open questions.
- Cross‑doc coherence: field names and enums consistent across DB‑change and page specs.
- Bilingual headings: enforce EN (JA) style where applicable; surface translation suggestions from glossary.
- Anchor stability: generate and verify predictable anchors used by cross‑links; fail on duplicates.
- Image rules: require images to exist, match naming pattern, and stay under size limits; warn when dimensions/aspect deviate from convention.

Failure linkage
- On common runtime or test errors, surface a short list of likely CHL entries.
 - For spec lint failures, link directly to the relevant guideline or template.
 - For design‑token violations (e.g., wrong color names), point to the token catalog entry.

## Automation & Metrics

Feedback
- Thumbs up/down + optional 140‑char comment; store per entry with timestamps.
- Use feedback to boost relevance and to suggest cleanups.

Digest
- Weekly Slack/Teams digest: new, updated, stale, most‑viewed, most‑helpful.
 - Include a “Spec Lint” summary: top missing FIGMA links, numbering mismatches, broken cross-links.
 - Include a “Coherence” summary: common cross‑doc mismatches found (field names, guards, redirects) with links to affected specs.

Gap detector
- Track frequent queries with poor clicks/reads; suggest creating or updating entries.
 - Evaluator helper: run a spec‑completeness check and propose concrete TODOs (e.g., “Add Access Control section”, “Add images for elements 10–13”).
 - Coherence helper: when related docs contradict (e.g., 1104 vs 1103 guard rules), open a task suggestion with proposed wording to reconcile.

## Authoring UX

Attachments and previews
- Paste images and attach files; show inline previews; store in a managed bucket or repo folder.

Diffs and history
- Side‑by‑side diffs, summarizing changes (added/removed sections, metadata changes).
- Full history with restore (soft versioning per entry).

Bulk operations
- Bulk import/export in Markdown + frontmatter; dry‑run mode with validation report.

Offline sync
- Option to mirror a category to a repo folder via CI; changes sync back via a bot account with review.

Bilingual assistance
- Optional JP/EN phrasing hints for common sections (Overview, Access Control, Validation). Insert suggested bilingual headings/phrases to keep tone consistent across specs.
 - “Plain language” rewrites for key warnings and guards with project‑preferred terms.

## Security & Permissions

Role‑based access
- Per‑category roles for read/write/approve/archive.
- Sensitive tags that restrict read access (e.g., `confidential`, `customer`).

Redaction
- Optional redacted public view for entries with sensitive sections.

Audit export
- Export history (CSV/JSON) for compliance audits (who/when/what changed).

## MCP Server Technical Improvements

- Config‑driven search sizes
  - Use `CHL_TOPK_RETRIEVE` and `CHL_TOPK_RERANK` when constructing the vector provider instead of hardcoded values. Cap per‑request `limit` by provider `top_k`.

- Optional text‑only fallback mode
  - Env flag `CHL_TEXT_FALLBACK=1` starts the server with SQLite text search when ML deps/models are unavailable. Handshake clearly states provider = `sqlite_text` and warns about reduced recall/precision. Logs emit a one‑line warning with remediation (“uv sync --extra ml; scripts/setup.py --download-models”).

- Safer session usage
  - SearchService accepts a session factory; each tool call uses a short‑lived session (no long‑lived shared session). Keep FAISS operations serialized (mutex) inside `FAISSIndexManager` to avoid race conditions during add/update/save.

- Schema constraints and indexes
  - Embeddings: add `UNIQUE(entity_id, entity_type, model_name)` to prevent duplicates across re‑embeds. Upserts reuse the row.
  - FAISS metadata: add `UNIQUE(entity_id, entity_type)` (keep `faiss_internal_id` unique). Update the mapping on re‑adds.
  - Add helpful indexes: `experiences(category_code, updated_at)`, `category_manuals(category_code, updated_at)` for common filters/sorts.

- Duplicate detection on updates
  - For `update_entry` (experiences), surface duplicate suggestions using `duplicate_threshold_update` (default 0.85) with `exclude_id`. Keep experiences atomic; suggest refactors instead of merging.

- Token‑aware truncation for manuals
  - Replace length≈token heuristic with real token counting for embeddings. Add `CHL_EMBED_MAX_TOKENS` (default 8000). Truncate by tokens, not characters; log truncation count.

- Configuration hygiene
  - Ensure server honors all related env vars already present in `Config`: thresholds, timeouts, top‑k, paths, log level. Validate and document defaults in README.

- Minimal test suite (no‑ML CI)
  - Unit tests for: schema uniqueness, repositories CRUD, `normalize_context`, config validation, SQLite text provider search, and duplicate detection (text provider). Mark ML tests with `pytest -m ml` for optional runs.

## API Additions (Building on Existing)

Search
- `GET /search?q=...&facets=...&status=...` returns results + facet counts.

Batch operations
- `POST /batch/validate` for pre‑flight schema/link checks.
- `POST /batch/import` and `GET /batch/export` with dry‑run and report.
 - `POST /batch/lint-spec` to validate numbering/images/links for `.ai-docs` specs.

Versioning
- `GET /entries/{id}/history`, `POST /entries/{id}/restore/{version}`.

Governance
- `POST /entries/{id}/transition` with `{ to_status, comment }`; enforce rules.

Skeleton and enums
- `POST /templates/{template_id}/instantiate` → returns Markdown skeleton with placeholders and numbered sections.
- `GET /introspect/db-docs/enums?path=...` → returns parsed enumerations/options from DB-change docs.

Evaluator endpoints
- `POST /evaluate/spec-completeness` → returns checklist status (FIGMA link, images referenced, numbering, access control presence) + actionable suggestions.
 - `POST /evaluate/compare-specs` → returns a comparison table between two specs (features, permissions, filters, navigation) to support “similar page” sections.

## Rollout Plan

Phase 1 (1–2 weeks)
- Implement frontmatter validation, CI link checker, stale doc job, status badge, related entries.
- Add spec skeleton generator and `.ai-docs` linter (numbering/images/links); enable CI checks for page specs.
- Server: honor `CHL_TOPK_*`, add schema uniqueness + indexes, duplicate detection on updates, token‑aware truncation flag (scaffold), and basic unit tests.

Phase 2 (2–4 weeks)
- Add governance transitions, approvers, audit log, basic facets in search.
 - Ship enum/options auto-extractor and CLI autofill; add spec‑completeness gate to PR bot comments.
- Server: optional `CHL_TEXT_FALLBACK` mode, per‑call sessions, and FAISS update mutex; docs + handshake messaging for provider state.

Phase 3 (4–8 weeks)
- Hybrid search, PR bot suggestions, editor/CLI integration, feedback metrics, weekly digest.
- Add bilingual assistance hints and advanced ranking boosts tied to repo paths and recency.
 - Ship LinkWizard and one‑click “Insert into doc” snippets.
 - Add spec comparison generator and anchor/bilingual linters.

## Acceptance Criteria (Initial)

- Entries without required frontmatter cannot be published.
- Stale entries are flagged and owners are notified automatically.
- CI fails when CHL references are broken in changed entries.
- Spec skeletons can be generated from templates via API/CLI.
- `.ai-docs` linter detects mismatched image numbering and broken cross-links; CI blocks merges until fixed.
- Spec completeness check flags missing FIGMA link, missing Access Control, and absent images; surfaced in PR comments.
- Read views show status, review date, and related entries.
- Basic facet filters work (category, status, tags, owner).
- Server starts in text‑only mode when `CHL_TEXT_FALLBACK=1` and surfaces provider state in handshake.
- Vector provider uses `CHL_TOPK_RETRIEVE`/`CHL_TOPK_RERANK`; per‑request limits are respected.
- DB uniqueness: one embedding row per (entity_id, entity_type, model_name) and one FAISS mapping per (entity_id, entity_type).
- Update flow surfaces duplicate suggestions for experiences using the higher update threshold.
- Token‑aware manual embedding truncation is configurable; logs truncation events.
- Core unit tests pass in CI without ML extras.

## Risks & Mitigations

- Over‑strict validation blocking contributions → Allow draft saves with warnings; block only on publish.
- Noise from reminders → Batch notifications; allow snooze; show dashboard of upcoming reviews.
- Search quality confidence → Start with facets + BM25; add vectors after usage signals collected.

## Next Steps

- Align with design on badges and facets.
- Define initial category schemas (start with 1–2 categories).
- Stand up CI checks and nightly stale job.
- Instrument feedback and usage events for ranking.
- Implement server improvements per above; open issues for: fallback handshake text, session factory wiring, FAISS mutex, and schema migrations.
