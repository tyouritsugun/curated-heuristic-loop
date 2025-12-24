# Phase 3 – Step 2: Round Loop & Convergence (Draft, gap-closed)

Purpose: orchestrate iterative, unattended LLM-driven curation over Phase‑2 communities until convergence, with clear resume semantics and reporting.

## Inputs
- `data/curation/communities.json` (embed-only default) **or** `data/curation/communities_rerank.json` when `--two-pass`.
- `data/curation/similarity_graph.pkl` (Phase 2 graph structure).
- `data/curation/neighbors.jsonl` (cached neighbor sets; used to avoid FAISS re-query).
- `data/curation/chl_curation.db` (pending/rejected state).
- LLM config: `curation_llm` section in `scripts/scripts_config.yaml` (`model`, `api_base`, optional `api_key`), overridable by env `LLM_MODEL`, `LLM_API_BASE`, `LLM_API_KEY`.

## State & Resume (Phase 3)
- State file: `data/curation/.phase3_state.json` (config: `curation.phase3_state_file`; keeps schema separate from Phase 1 `.curation_state.json`).
- Schema (extends Phase 1 format):
  - `run_id`, `db_path`, `input_checksum`, `user`, `version`, `timestamp`
  - `current_round` (int), `max_rounds` (int)
  - `progress_history` (list of dicts per round): `[{"round": 1, "items_delta_pct": 0.05, "comms_delta_pct": 0.03}, ...]`
  - `communities_resolved` (list of community ids processed)
  - `last_community_index` (int; resume within a round)
  - `last_bucket` (string, optional for parity with Phase 1)
  - `decisions` (append-only log for integrity)

## Core Loop
1) Load current communities file (embed or rerank variant).
2) Community selection per round:
   - Filter out communities where all members have `sync_status=2`.
   - Default skip `oversized=true` (>50 members); enable via `--process-oversized`.
   - Sort by `priority_score` desc; take top `--batch-size` (default: all remaining).
3) For each selected community:
   - Build prompt payload (members, edges/weights, category, round index).
   - Call LLM agent (OpenAI-compatible) for decision: `merge_all | merge_subset | keep_separate | manual_review`.
   - Apply decision:
     - `merge_all` / `merge_subset`: mark merges → set duplicates to `sync_status=2`; log to `curation_decisions` & `evaluation_log.csv`.
     - `keep_separate`: add note, leave as pending.
     - `manual_review`: add to manual queue; leave as pending.
4) After each round:
   - Graph/communities rebuild (Option C): load cached `neighbors.jsonl`, filter out `sync_status=2`, rebuild graph topology and communities **without** re-querying FAISS or re-running rerank; reuse stored weights.
   - Compute progress metrics: pending count delta, communities delta, edge stats.
   - Convergence check (see below).

## LLM Response Contract
- Required JSON schema: `{ "decision": "merge_all|merge_subset|keep_separate|manual_review", "merges": [[src_id, dst_id], ...]? , "notes": str? }`
- `merges` required when `decision` is `merge_all` or `merge_subset`; ignored otherwise.
- Agent must abstain with `manual_review` if uncertain or budget exceeded.
- Validation rules:
  - Invalid `decision` → treat as error and retry (counts toward retry budget).
  - Missing `merges` for merge decisions → retry.
  - Empty `merges` for merge decisions → downgrade to `keep_separate`, log warning.
  - Invalid member IDs in `merges` → drop invalid pairs, proceed with valid; warn in report.

## Convergence & Safety
- Hard cap: `--max-rounds` (default 10).
- Progress threshold formula (relative):  
  `improvement_items = (prev_items - curr_items) / prev_items`  
  `improvement_comms = (prev_comms - curr_comms) / prev_comms`
- Stop when **both** metrics fall below `--improvement-threshold` (default 0.05) for 2 consecutive rounds.
- If a round yields zero progress, abort remaining rounds and note in `morning_report.md`.
- Manual-review items stay pending; always listed in the morning report.

## Outputs
- Mutations: `curation_decisions` table, `evaluation_log.csv`.
- Community files: `data/curation/communities.json` (embed) and `data/curation/communities_rerank.json` (two-pass result; kept separate).
- Morning report: `data/curation/morning_report.md` with sections:
  - Summary (initial/final counts, reduction %, rounds run, convergence reason)
  - Round details table (round, communities, items, merges, manual reviews, progress %)
  - Actions (auto-dedup merges, LLM merges, rejected, manual queue size)
  - Manual review queue (community IDs or pairs)
  - Cost summary (tokens, estimated $, model)
- Optional: `data/curation/tuning_report.txt` (edge distributions, borderline counts, rerank vs embed diffs).
- Dry-run sidecars (no DB writes): append `.dryrun` to each planned output  
  - `evaluation_log.csv.dryrun` (planned decisions)  
  - `chl_curation.db.dryrun.sql` (SQL mutations)  
  - `morning_report.md.dryrun` (as-if report)  
  - `communities.json.dryrun` / `communities_rerank.json.dryrun` (if regenerated)

## Flags / Config
- `--max-rounds` (default 10)
- `--improvement-threshold` (default 0.05, relative, AND condition)
- `--batch-size` (communities per round; default all; recommendation: 50–100 on datasets >200 communities)
- `--dry-run`
- `--two-pass` (write/read `communities_rerank.json`)
- `--process-oversized` (opt-in to send oversized communities)

## Reuse from Phase 2
- Graph rebuild + community detection (already implemented) using cached neighbors (no fresh FAISS).
- Auto-dedup (≥0.98) runs **once before round 1**; later rounds rely on the agent.

## Error Handling
- LLM call policy: timeout 120s; max 2 retries with exponential backoff (e.g., 5s, 15s). Failures = HTTP error, timeout, invalid JSON/schema. On final failure, mark community `manual_review` and log in morning report.
- Graph rebuild failure: abort loop, record error in morning report, leave DB unchanged for that round.
- All failures contribute to a "Warnings" subsection in the morning report.
- Config validation preflight: refuse to start if `curation_llm.model` missing or config YAML is malformed; surface the path used in the error message.
 - LLM retry tunables read from `curation_llm` in `scripts_config.yaml` (`timeout`, `max_retries`, `retry_backoff`, `retry_delays`).

## Acceptance
- At least one round executed and logged.
- Morning report written with the structure above.
- Convergence or max-round condition respected; state file updated for resume.

## Implementation Staging (recommended)
1) **Single-community prompt harness**  
   - Build the LLM payload and response validation for one community (no DB writes).  
   - Provide a CLI switch (e.g., `--one-community <id>` or separate `test_prompt` helper) to exercise the prompt with a known fixture.  
   - Use stub/mocked LLM responses to verify schema validation, bad-decision handling, retries, and sidecar generation in isolation.
   - Concrete harness (added): `python -m scripts.curation.agents.prompt_harness --community-id COMM-001 --mock-response '{"decision":"keep_separate"}'`  
   - To call a real model: `python -m scripts.curation.agents.prompt_harness --community-id COMM-001 --call-llm --save-prompt /tmp/prompt.txt` (reads defaults from `scripts_config.yaml`; no DB writes)
2) **Dry-run round loop**  
   - Integrate the single-community executor into the round loop with `--dry-run` and `--batch-size 1` to smoke-test logging, state, and report generation without mutations.  
   - Confirm progress metrics and morning report render correctly.
3) **Full run**  
   - Enable DB mutations, normal batch size, and optional `--two-pass`.  
   - Monitor morning report, state file, and evaluation log for consistency.
