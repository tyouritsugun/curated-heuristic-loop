# Phase 0 — Test Data & Dual-Sheet Harness

Practical plan to build the Phase 0 test corpus and two parallel spreadsheets (Team A and Team B) for the semi-auto curation pipeline.

---

## Goals
- Create a realistic, reusable dataset to exercise merge, dedup, similarity, drift, and schema guards.
- Two near-overlapping sheets to mimic two teammates: Team A sheet and Team B sheet.
- Target volume: ~10 manuals + **100–150 experiences** total (per combined set) so sparsification/top-k and community detection have enough density.
- Include labeled ground truth so we can score precision/recall for Phase 0 runs.

---

## Test Scenarios (required coverage)
- 5 drift triads (A≈B≈0.88, B≈C≈0.87, A≈C≈0.65) to validate drift guards.
- 8 high-similarity pairs (≥0.92) that should merge.
- 10 medium pairs (0.75–0.92) that are related but **keep separate**.
- 6 borderline pairs (0.55–0.70) for the review queue.
- Borderline coverage: include cases where embedding is high/LLM low, embedding low/LLM high, and both mid to stress the blend logic.
- 3 deliberate ID collisions (same `id`, different `author` across A/B sheets) to test suffixing.
- 2 cross-section conflicts (same title, different section useful/harmful/contextual).
- 2 near-duplicate manuals (≥0.85) to force a decision on manual similarity.
- 2–3 regression pairs (solution B breaks assumption in A) and 2–3 extension pairs (B widens A's scope) with explicit `expected_action` labels.
- High-similarity clusters: at least 2 clusters with ≥4 items each where merging is obvious.
- Expected clustering outcome: ~8–12 meaningful clusters (15–30 items each) plus ~30–40 singletons/outliers to validate community detection and noise handling.

---

## Source Sites (12 total; 2 shared, 10 unique each)
- **Shared (2)**: docs.github.com, git-scm.com
- **Team A unique (10)**: nodejs.org/docs, npmjs.com/docs, classic.yarnpkg.com/en/docs, pip.pypa.io, docs.docker.com, code.visualstudio.com/docs, developers.cloudflare.com, kubernetes.io/docs, postgresql.org/docs, stackoverflow.com (paraphrased)
- **Team B unique (10)**: python.org/dev/peps, pypi.org/help, pnpm.io, go.dev/doc, podman.io/docs, jetbrains.com/help, curl.se/docs, redis.io/docs, eslint.org/docs, nextjs.org/docs

Guidelines:
- Paraphrase; avoid long verbatim snippets. Keep each experience <300 words.
- Paraphrase intentionally: keep the problem signature but vary parameter names, versions, paths, OS, and commands; swap success/failure modes to generate controlled near-duplicates.
- Prefer official docs/blogs; use Stack Overflow only for common error phrasings, paraphrased.

---

## Dataset Shape
- **Category**: `DEV_TOOLING` (Developer Tooling – Common Errors & Fixes).
- **Manuals (~10)**: SOP/policy style (branching, SSH keys, node version policy, Docker build hygiene, lint/format standards, secrets handling, release checklist, incident triage, venv rules, IDE workspace setup). Include 2 near-duplicates (≥0.85 similarity) to force a manual dedup decision.
- **Experiences (100–150)**: atomic issue → fix. Mix Git/npm/yarn/pnpm/pip/Docker/Podman/VS Code/JetBrains/HTTP 4xx/5xx/K8s/DB connection errors. Ensure at least 20 seed experiences that branch into variants to satisfy the test scenarios above.
- Fields: `id`, `category_code`, `section (useful/harmful/contextual)`, `title`, `playbook`, `context (OS/tool/version)`, `source`, `author`, `sync_status` (int), `created_at/updated_at`, `expected_action` (ground truth: `merge_with:<id>`, `keep_separate`, `needs_review`, `reject`).
- Keep IDs stable within variant families; deliberately create 3 cross-team ID collisions to test suffixing.

### Timestamp strategy (for `created_at`/`updated_at`)
- 70% “recent” entries dated within the last 7 days.
- 20% “medium-aged” entries dated 1–4 weeks ago.
- 10% “old” entries dated >1 month ago.
- Purpose: exercise `--recent-days` filters and ordering in review queues.

---

## Split Strategy (Team A vs Team B)
- Shared items: include both shared sites’ content (a few manuals/experiences) in **both** sheets.
- Unique items: draw from each team’s unique site list. Aim ~60–70% overlap in themes, but different wording/details to trigger near-duplicates and drift.
- Variants: for 10+ experiences, create 2–3 paraphrased/parameter variants (package name/version/path/OS) and split them across sheets to create A≈B, B≈C cases (drift triads) plus obvious merges.
- Cross-section conflicts: duplicate a title with different `section` values (useful vs harmful) across sheets.
- ID collisions: reuse 3 IDs across authors (Team A vs Team B) to exercise collision handling.

---

## Extraction & Curation Steps
1) For each site, pick 1 manual-style item and 3–5 experience items (as available).
2) Paraphrase and normalize into schema; add minimal context (OS, tool versions). Create controlled near-duplicates by varying package names, versions, paths, OS, and success/failure mode while keeping the core problem signature.
3) Assign `sync_status` integers (use 0=PENDING for all test inserts). Populate `expected_action` per test scenario, including `merge_with:<id>` targets for high/medium pairs and `keep_separate` for drift/borderline cases.
4) Load into two Google Sheets (3 worksheets each: Categories, Experiences, Manuals) to simulate Team A and Team B exports. Also create one **deliberate schema-mismatch CSV** (wrong/extra column) to test preflight rejection.
5) Export structure: `data/curation/members/team_a/` and `data/curation/members/team_b/`, each containing `categories.csv`, `experiences.csv`, `manuals.csv`.
6) Scenario-to-site mapping (guide, adjust as needed): Git/GitHub/npm/yarn/pnpm → drift triads & high-sim merges; Docker/Podman → cross-section conflicts; VS Code/JetBrains → borderline pairs; HTTP/K8s/DB → regression/extension cases; manuals from policy-style sources for manual near-dupes.

---

## Test Harness Flow
- Use the two sheets as stand-ins for two teammate exports (Team A and Team B).
- Verify: merge audit logs, resume state file, dedup accuracy, drift triads surfaced.
- Run: `export_from_sheets.py` (to `data/curation/members/team_a/` and `team_b/`) → `merge_exports.py` (to `data/curation/merged/`) → duplicate pass → clustering → review queue.
- Compute metrics against `expected_action`: precision/recall for merge suggestions, count of detected drift triads, number of conflicts (regression/extension, section mismatch) surfaced.

---

## Validation Criteria (Phase 0 runs)
- Preflight: schema mismatch file must fail fast with clear error; valid files load.
- Drift triads: ≥5 detected and surfaced to reviewer.
- High-sim pairs: all 8 flagged as merge candidates (recall ≥0.95 on ground truth merges).
- Borderline queue: all 6 borderline pairs routed to review; none auto-merged.
- ID collisions: all 3 collisions get suffix appended and logged in `merge_audit.csv`.
- Manuals: near-duplicate manual pair is either merged or explicitly kept separate with rationale captured.
- Metrics: report precision/recall against `expected_action` plus counts of regression/extension/section conflicts.
- Regression/extension: at least 2–3 labeled regression pairs and 2–3 labeled extension pairs are surfaced with correct `expected_action` handling.

---

## Open Items
- Confirm final sync_status mapping against live data (default=1 in schema, planned mapping 0/1/2).
- Decide if manuals participate in similarity in Phase 1 (current stance: exclude; Phase 0 still tests the near-duplicate pair).
- GPU is required for production flow; CPU fallback (text-overlap) is allowed **only for Phase 0 harness smoke tests** when GPU is unavailable.
