# Curation Overnight Overview

This is the short “why/how” for Carlos (or anyone running the overnight agent).

## What it does
- Uses community artifacts (`communities.json`, `neighbors.jsonl`) to ask an LLM which items should merge.
- Applies merges to the curation DB, rebuilds communities, and repeats for a few rounds.
- Stops early if progress is small to avoid wasting time/money.
- Writes a `morning_report.md` for review.

## One‑command overnight run
```bash
python scripts/curation/overnight/run_curation_overnight.py
```
Defaults are read from:
- `scripts/scripts_config.yaml`
- `scripts/curation/agents/prompts/curation_prompt.yaml`

The overnight wrapper defaults to a **DB copy**:
- `data/curation-copy/chl_curation.db`
- `data/curation-copy/.curation_state_loop.json`

## Core loop (high level)
1) Auto‑dedup high‑confidence pairs (>= `auto_dedup` threshold).
2) LLM decides per community: `merge_all`, `merge_subset`, `keep_separate`, `manual_review` (human curator review).
3) Apply decisions → rebuild communities (no FAISS re‑query).
4) Stop when progress is low or max rounds reached.

## Key defaults (in config)
- `curation_llm.llm_response_timeout`
- `curation_llm.max_retries`, `curation_llm.retry_backoff`, `curation_llm.retry_delays`
- `curation.thresholds.auto_dedup`
- `curation.curation_state_file` (curation loop state file)

## Outputs
- `data/curation/morning_report.md`
- `data/curation/evaluation_log.csv`
- `data/curation/communities.json`

The rest of this doc includes the step‑by‑step workflow.
# Team Curation Walkthrough (Concise)

Short sample flow for Alice, Bob, and a curator (Carlos) using the semi-auto curation pipeline.

## Prereqs
- GPU backend required for curation (Apple Silicon or NVIDIA). CPU-only users can export only.
- Activate the matching virtualenv: `.venv-apple` or `.venv-nvidia` (CPU export: `.venv-cpu`).
- Category codes must match across members (same code/name/description) or merge will fail.

## 1) Member Export (Alice/Bob)
- Start CHL, open Operations, click "Export CSV".
- Each sends `{user}.export.zip` to the curator.

## 2) Curator Merge + Prep (one command)
After unzipping member exports into `data/curation/members/`, run:
```bash
python scripts/curation/merge/run_merge_pipeline.py --force-db
```
This wraps steps 2–5 (merge → init DB → import → build embeddings/FAISS → auto-merge → rebuild → communities).

## 3) Run Overnight Curation (one command)
- Defaults are in `scripts/scripts_config.yaml` and the prompt in `scripts/curation/agents/prompts/curation_prompt.yaml`.
- If you need to override behavior, edit those files instead of CLI flags.
```bash
python scripts/curation/overnight/run_curation_overnight.py
```
Note: the overnight wrapper uses `data/curation-copy/.curation_state_loop.json` by default (safe for reruns).
Key knobs (in `scripts/scripts_config.yaml`):
- `curation_llm.llm_response_timeout` (seconds per LLM call)
- `curation.thresholds.auto_dedup` (pre-merge threshold)
Note: LLM errors fail fast (no retries) so API key / local server issues surface immediately.

After the run, export the curated experiences to TSV for review:
```bash
python scripts/curation/export_curated.py --db-path data/curation/chl_curation.db --output data/curation/approved
```
Use `data/curation/approved/experiences.tsv` for review.

<details>
<summary>Optional: Dry-run Overnight Loop</summary>

- What a round means: one pass over selected communities (LLM decisions → merges → rebuild communities).
  - More rounds are not always better: round 1 typically yields most merges, round 2 catches newly formed clusters,
    and later rounds often have diminishing returns. The loop stops early if improvement is small.
- Dry-run first to inspect sidecars and the morning report:
```bash
python scripts/curation/overnight/run_curation_loop.py \
  --max-rounds 3 \
  --improvement-threshold 0.05 \
  --dry-run
```
- Real run (uses `curation_llm` config or `.env` `LLM_MODEL`/`LLM_API_BASE`/`LLM_API_KEY`):
```bash
python scripts/curation/overnight/run_curation_loop.py --max-rounds 3
```
- Two-pass rerank variant (uses `communities_rerank.json` if it already exists):
```bash
python scripts/curation/overnight/run_curation_loop.py --two-pass
```
- Check outputs after the run:
  - `data/curation/morning_report.md`
  - `data/curation/communities.json` (and `communities_rerank.json` if two-pass)
  - `data/curation/evaluation_log.csv`

</details>

## Rerank note (`--with-rerank`)
- `--with-rerank` applies only when building communities (merge pipeline / `build_communities.py`).
- It changes how neighbor scores are computed (reranker-only scoring), which affects the community graph files used by the overnight loop.
- Once communities are built, the overnight rounds do **not** rerank again; they just consume the existing communities JSON.

## 4) Review and Publish (Spreadsheet)
- Copy `data/curation/approved/experiences.tsv` to Excel or Google Sheets for a quick review.
- If satisfied, publish to the team (Alice and Bob) via the UI or CLI.
  - Note: Importing via the UI/Excel always resets `embedding_status` to `pending` on the server and rebuilds embeddings; any `embedded` values in the TSV are ignored.
UI: Operations → Import from Google Sheet  
CLI:
```bash
python scripts/import_from_sheets.py --sheet-id <SHEET_ID>
python scripts/ops/rebuild_index.py
```

## Key Outputs
```
data/curation/
  members/                 # member exports
  merged/                  # merged CSVs
  approved/                # curated CSVs
  chl_curation.db          # curation DB (includes curation_decisions)
  faiss_index/             # embeddings index
  merge_audit.csv          # merge audit log
  evaluation_log.csv       # interactive decisions log
  .curation_state.json     # resume state
  .curation_state_loop.json # curation loop resume state
  neighbors.jsonl          # neighbors cache
  similarity_graph.pkl     # similarity graph
  communities.json         # communities
```

## Notes
- `sync_status`: `0=PENDING`, `1=SYNCED`, `2=REJECTED`.
- Communities are per-category; skills stay out of the loop by default.
