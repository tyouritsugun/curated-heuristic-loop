# Phase 1 — Plumbing, Safety Rails, and Duplicate Surfacing

Practical plan for the first shippable curation pipeline that real teammates can run end‑to‑end. Focus: schema safety, merge/import flow, embedding/index rebuild, and an initial duplicate finder with buckets + resume.

---

## Goals
- Enable a curator to take multiple member exports, merge them, embed them on GPU, surface likely duplicates, and emit an approved dataset ready for publish to the canonical sheet.
- Fail fast on schema drift, ID collisions, and category code conflicts; always leave an audit trail.
- Provide resumable, dry‑run friendly CLI entrypoints that match the high‑level plan in `doc/plan/semi-auto-curation.md`.

---

## Scope (Phase 1) vs Later Phases
- In scope: merge/import/init/build-index scripts (done), duplicate finder with bucketed similarity, interactive review loop, resume state, dry‑run flags, audit logs, publish/approve export.
- Out of scope until Phase 2+: sparse similarity graph, clustering/communities, agent suggestions, advanced drift guards, score_atomicity.
- Manuals: remain imported and embedded; duplicate detection for manuals stays manual/optional (only experiences participate in similarity buckets unless explicitly requested).

---

## Preconditions
- GPU backend configured (`config.backend != "cpu"`); embedding model reachable locally.
- Service account + canonical Google Sheet ID available for publish/import.
- Member exports placed in `data/curation/members/{user}/` with the 3 CSVs (`categories.csv`, `experiences.csv`, `manuals.csv`).
- Category codes are unique and aligned team-wide; merge will abort on conflicts.
- Schema columns present (see “Schema & Preflight”).

---

## End-to-End Workflow (team curator)
1) Merge member exports  
`python scripts/curation/merge_exports.py --inputs data/curation/members/alice data/curation/members/bob --output data/curation/merged`
2) Initialize curation DB (fresh, isolated)  
`python scripts/curation/init_curation_db.py --db-path data/curation/chl_curation.db [--force]`
3) Import merged CSVs into curation DB  
`python scripts/curation/import_to_curation_db.py --input data/curation/merged --db-path data/curation/chl_curation.db`
4) Build embeddings + FAISS index on curation DB (GPU only)  
`python scripts/curation/build_curation_index.py --db-path data/curation/chl_curation.db`
5) Find duplicates (Phase 1 tool to implement)
`python scripts/curation/find_pending_dups.py --db-path data/curation/chl_curation.db --compare-pending --format table --bucket high`
   - Flags: `--bucket {high|medium|all}`, `--limit`, `--dry-run`, `--state-file data/curation/.curation_state.json` (default), `--reset-state`, `--compare-pending`
   - Iterative workflow: process high first, then medium, with recomputation after each phase to account for merged items
   - **Note**: Uses `--compare-pending` by default to compare all imported items against each other (since original sync_status values are not trustworthy from individual exports)
6) Interactive review loop (same script, `--interactive`) to mark merge/keep/reject/update; writes decisions to `evaluation_log.csv`.
7) Export approved set from curation DB  
`python scripts/curation/export_curated.py --db-path data/curation/chl_curation.db --output data/curation/approved`
8) Publish to canonical sheet (Phase 1 CLI)  
`python scripts/curation/publish_to_canonical.py --input data/curation/approved --sheet-id <PUBLISHED_SHEET_ID> --dry-run`
9) Team re-imports canonical baseline (outside Phase 1 scope but documented)  
`python scripts/import_from_sheets.py --sheet-id <PUBLISHED_SHEET_ID>` then rebuild index.

Solo mode: same flow but usually only one member directory; duplicate finder uses `--compare-pending` to compare items against each other for self-deduplication.

---

## Schema & Preflight (must implement)
- Required columns  
  - Categories: `code,name,description,created_at`
  - Experiences: `id,category_code,section,title,playbook,context,source,sync_status,author,embedding_status,created_at,updated_at,synced_at,exported_at`
  - Manuals: `id,category_code,title,content,summary,source,sync_status,author,embedding_status,created_at,updated_at,synced_at,exported_at`
- Fail fast if any required column missing or extra unknown columns appear (except whitelisted optional ones). Print per-file column diff.
- Enforce category code uniqueness and identical name/description across members; abort on conflict.
- ID collisions: experiences/manuals keep the original ID, append `_{author}` on collision, log to `merge_audit.csv`.
- Curation-only columns (to add in curation DB schema or via a `curation_decisions` sidecar table; production schema unchanged): `merge_with` (string, nullable) and `curation_notes` (text, nullable) to store merge decisions; required before `find_pending_dups` mutates data.
- All imported rows set `embedding_status=pending`; timestamps normalized to UTC.

---

## Duplicate Finder (Phase 1 deliverable)
- Inputs: curation DB, embeddings already present. Use FAISS similarity search per category.
- Candidate generation: for each pending experience, fetch top‑K neighbors (default 50) from all other pending experiences (self‑comparison). Original sync_status values are unreliable from individual exports, so all items are compared against each other.
- Scoring: use cosine similarity from embeddings; optional rerank hook (LLM) is configurable but may be stubbed in Phase 1.
- Buckets (default thresholds): high ≥0.92, medium 0.75–0.92 (processed iteratively). Process high first, then medium, with recomputation after each phase. Allow CLI overrides.
- Output formats: `table` (stdout), `json`, `csv`. Include columns: `pending_id, anchor_id, score, category, title_snippet, section_mismatch, id_collision_flag`.
- Interactive mode commands (persist to `evaluation_log.csv` and state file):
  - `merge <pending_id> <anchor_id>`: mark pending as duplicate of anchor (sets pending entry `sync_status=2` and records `merge_with=anchor_id` plus optional `curation_notes`; anchor keeps its current status).
  - `keep <pending_id>`: keep separate; record note.
  - `reject <pending_id> <reason>`: set `sync_status=2`.
  - `update <pending_id>`: open editor or prompt for title/playbook/context edits; mark `embedding_status=pending` for re-embed later.
  - `diff <pending_id> <anchor_id>`: show unified diff of titles/playbooks.
  - `skip/quit`: save state and exit.
- Resume: `.curation_state.json` stored at `data/curation/.curation_state.json` (fields: run_id, db_path, last_bucket, last_offset, decisions[], input_checksum, user, version, timestamp). `--reset-state` discards it; auto-reset if checksum mismatches.

---

## Safety & Audit
- `merge_audit.csv`: already produced by merge; ensure fields include collisions and warnings.
- `evaluation_log.csv`: append-only decisions log with `{timestamp,user,entry_id,action,target_id,notes}`.
- Dry‑run mode on any mutating script writes `*.dryrun` sidecars and never touches DB/Sheets.
- Import/init warnings: remind users that curation DB is isolated; main DB untouched. If a destructive import to main DB is ever added, require `--force` + interactive yes/no prompt unless `--yes`.
- FAISS rebuild: always clear `faiss_index/` and `faiss_metadata` before rebuild (already implemented).
- GPU guard: `build_curation_index.py` already exits if backend == cpu; keep GPU as a hard precondition.

---

## Outputs & Exit Criteria (Phase 1)
- `data/curation/merged/` exists and passed preflight.
- `chl_curation.db` populated; all pending items embedded; FAISS index built.
- Duplicate finder run completes with logs; decisions captured in `evaluation_log.csv` and state file.
- `data/curation/approved/` produced, ready to publish.
- Dry‑run publish produces zero schema or collision errors against the canonical sheet.

---

## Test Checklist for Phase 1
- Happy path: two member exports, no category conflicts, one ID collision per entity type, run full flow through export_curated.
- Schema failure: remove a required column → preflight must abort with clear message.
- Category conflict: same code, different name → merge aborts.
- ID collision handling: verify suffixing and audit log entries.
- Duplicate finder: confirm high/medium bucket counts match thresholds; verify iterative workflow (process high, then medium, with recomputation); resume after quit; dry‑run writes sidecars only.
- GPU guard: running build/index on CPU backend must exit with error message.

---

## Open Questions / Decisions to lock before code freeze
- `sync_status` mapping (locked for Phase 1): `0=PENDING` (local/not yet published), `1=SYNCED` (from canonical sheet or previously approved), `2=REJECTED` (curator decision). Curation imports preserve the source `sync_status` from member exports; curator marks rejected duplicates as 2. Export filters out `sync_status=2` by default.
- Manuals in Phase 1 duplicate detection: keep out by default; add `--include-manuals` later (Phase 2) after we validate experience flow.
- Rerank hook: do we wire the existing LLM reranker now, or leave a stub with a feature flag?
- Canonical sheet ID: use `PUBLISHED_SPREADSHEET_ID` from `.env.sample` (distinct from `IMPORT_SPREADSHEET_ID`).

---

## Next Steps (implementation order)
1) Add `merge_with` and `curation_notes` columns (or a `curation_decisions` sidecar table) to curation DB schema; keep production schema unchanged.
2) Finish preflight validator shared by merge/import (columns + category conflict checks).
3) Implement `find_pending_dups.py` with bucketed search, resume, interactive loop, and state file (self‑comparison default).
4) Add `export_curated.py` to emit approved CSVs (and optional JSON) from curation DB; exclude `sync_status=2` by default.
5) Add `publish_to_canonical.py` with dry‑run and schema checks; integrate Google Sheets client.
6) Wire CI smoke: run merge → init → import → build index on a small fixture to ensure plumbing stays green.
