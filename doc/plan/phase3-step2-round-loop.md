# Phase 3 – Step 2: Round Loop & Convergence (Draft)

Purpose: orchestrate iterative, unattended LLM-driven curation over Phase‑2 communities until convergence.

## Inputs
- `data/curation/communities.json` (or rerank variant) from Phase 2
- `data/curation/similarity_graph.pkl` (for rebuilding/re-ranking if needed)
- `data/curation/neighbors.jsonl` (reuse to avoid FAISS re-query if needed)
- `data/curation/chl_curation.db` (pending/rejected state)
- LLM config from `scripts/scripts_config.yaml` (`curation_llm.*`)

## Core Loop
1) Load current communities.
2) Select top-priority communities (priority_score desc); batch size configurable (default: process all).
3) For each community:
   - Build prompt payload (members, edges/weights, category, round index).
   - Call LLM agent (OpenAI-compatible) for decision: `merge_all | merge_subset | keep_separate | manual_review`.
   - Apply decision:
     - `merge_all` / `merge_subset`: mark merges → set duplicates to `sync_status=2` and log to `curation_decisions` & `evaluation_log.csv`.
     - `keep_separate`: add note, leave as pending.
     - `manual_review`: add to manual queue; leave as pending.
4) After each round:
   - Rebuild graph/communities from DB using existing Phase‑2 routines.
   - Compute progress metrics: pending count delta, communities delta, edge stats.
   - Check convergence: stop if max rounds reached (default 10) or <5% improvement for 2 consecutive rounds.

## Convergence & Safety
- Hard cap: 10 rounds.
- Progress threshold: <5% improvement in remaining items **and** communities for 2 consecutive rounds.
- If no progress in a round, abort remaining rounds and write a warning in `morning_report.md`.
- “Manual_review” results stay pending; included in morning report.

## Logging & Outputs
- Mutations: `curation_decisions` table, `evaluation_log.csv`.
- Summary: `data/curation/morning_report.md` (round stats, merges, rejects, manual queue, LLM cost summary).
- Optional: `data/curation/tuning_report.txt` (edge distributions, borderline counts, rerank vs embed diffs).
- Dry-run mode: write `.dryrun` sidecars only; no DB changes.

## Flags / Config
- `--max-rounds` (default 10)
- `--improvement-threshold` (default 0.05)
- `--batch-size` (communities per round; default all)
- `--dry-run`
- `--two-pass` (if set, use rerank-weighted communities file)

## Reuse from Phase 2
- Graph rebuild + community detection (already implemented).
- Auto-dedup (≥0.98) can be run before round 1 (optional flag).

## Error Handling
- If LLM call fails: retry with backoff (default 2 retries), otherwise mark community as `manual_review`.
- If graph rebuild fails: abort loop, write error to morning report, keep DB consistent.

## Acceptance
- At least one round executed and logged.
- Morning report written.
- Convergence or max-round condition respected.
