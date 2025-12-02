# Phase 0 — Test Data & Dual-Sheet Harness

Practical plan to build the Phase 0 test corpus and two parallel spreadsheets (Team A and Team B) for the semi-auto curation pipeline.

---

## Goals
- Create a realistic, reusable dataset to exercise merge, dedup, similarity, and drift handling.
- Two near-overlapping sheets to mimic two teammates: Team A sheet and Team B sheet.
- Target volume: ~10 manuals + ~50 experiences total (per combined set). Inflate later if needed.

---

## Source Sites (12 total; 2 shared, 10 unique each)
- **Shared (2)**: docs.github.com, git-scm.com
- **Team A unique (10)**: nodejs.org/docs, npmjs.com/docs, classic.yarnpkg.com/en/docs, pip.pypa.io, docs.docker.com, code.visualstudio.com/docs, developers.cloudflare.com, kubernetes.io/docs, postgresql.org/docs, stackoverflow.com (paraphrased)
- **Team B unique (10)**: python.org/dev/peps, pypi.org/help, pnpm.io, go.dev/doc, podman.io/docs, jetbrains.com/help, curl.se/docs, redis.io/docs, eslint.org/docs, nextjs.org/docs

Guidelines:
- Paraphrase; avoid long verbatim snippets. Keep each experience <300 words.
- Prefer official docs/blogs; use Stack Overflow only for common error phrasings, paraphrased.

---

## Dataset Shape
- **Category**: `DEV_TOOLING` (Developer Tooling – Common Errors & Fixes).
- **Manuals (~10)**: SOP/policy style (branching, SSH keys, node version policy, Docker build hygiene, lint/format standards, secrets handling, release checklist, incident triage, venv rules, IDE workspace setup).
- **Experiences (~50)**: atomic issue → fix. Mix Git/npm/yarn/pnpm/pip/Docker/Podman/VS Code/JetBrains/HTTP 4xx/5xx/K8s/DB connection errors.
- Fields: `id`, `category_code`, `section (useful/harmful/contextual)`, `title`, `playbook`, `context (OS/tool/version)`, `source`, `author`, `sync_status` (int), `created_at/updated_at`.
- Keep IDs stable within variant families to test collision handling.

---

## Split Strategy (Team A vs Team B)
- Shared items: include both shared sites’ content (a few manuals/experiences) in **both** sheets.
- Unique items: draw from each team’s unique site list. Aim ~60–70% overlap in themes, but different wording/details to trigger near-duplicates and drift.
- Variants: for 8–10 experiences, create 2–3 paraphrased/parameter variants (package name/version/path/OS) and split them across sheets to create A≈B, B≈C cases.

---

## Extraction & Curation Steps
1) For each site, pick 1 manual-style item and 3–5 experience items (as available).
2) Paraphrase and normalize into schema; add minimal context (OS, tool versions).
3) Assign `sync_status` integers (use 0=PENDING for all test inserts).
4) Save two CSVs: `team_a_export.csv`, `team_b_export.csv` (same columns as import service expects).
5) Load into two Google Sheets (or local CSVs) to be used by merge/dedup harness.

---

## Test Harness Flow
- Use the two sheets as stand-ins for two teammate exports.
- Run: export (if needed) → merge → duplicate pass → clustering → review queue.
- Verify: merge audit logs, resume state file, dedup accuracy, drift triads surfaced.

---

## Open Items
- Confirm final sync_status mapping against live data (default=1 in schema, planned mapping 0/1/2).
- Decide if manuals participate in similarity in Phase 1 (current stance: exclude).
- If GPU unavailable, define CPU fallback (text overlap) for embeddings in this harness.
