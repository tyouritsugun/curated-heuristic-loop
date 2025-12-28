# Phase 3 – LLM Agent Orchestration (Draft)

Goal: provide a single overnight wrapper that loads Phase‑2 artifacts, uses an LLM agent to resolve communities, and emits a morning report. After Alice/Bob exports are merged by Carlos, he should be able to run one command and go to bed.

---

## Scope
- Works on existing Phase‑2 outputs: `data/curation/communities.json`, `similarity_graph.pkl`, `neighbors.jsonl`.
- Agent decides per community: `merge_all`, `merge_subset`, `keep_separate`, `manual_review`.
- Auto‑dedup edges ≥ `auto_dedup` (default 0.98) happen before any LLM calls.
- Iterates rounds until convergence (max 10 rounds or <5% reduction in items/communities).
- Writes decisions to `curation_decisions` table and `evaluation_log.csv`; writes a `morning_report.md`.

## High‑Level Steps (overnight wrapper)
1) **Load inputs**: validate the latest `chl_curation.db`, `communities.json`, `similarity_graph.pkl`; bail if missing.
2) **Auto‑dedup (embed weights)**: merge pairs with weight ≥ `auto_dedup`; rebuild graph and communities incrementally.
3) **Optional Pass 2: rerank refinement (`--two-pass`)**
   - Rerank only intra‑community pairs using a cross‑encoder (e.g., Qwen3-Reranker).
   - Rebuild a rerank-weighted graph/communities (`communities_rerank.json`) with a slightly higher keep threshold (e.g., 0.80).
   - Agent consumes this refined file for subsequent rounds; embed-only file is kept for comparison.
4) **Agent loop** (round-based):
   - Pick top‑priority communities (by `priority_score`) from the active community file (embed or rerank pass).
   - For each community, send members + edges + metadata to the agent.
   - Apply agent decision (merge subset/all, keep separate, or flag manual review).
   - After each round, rebuild graph/communities and recompute priority.
5) **Convergence check**: stop when max rounds reached or improvement <5% in remaining items/communities for 2 consecutive rounds.
6) **Outputs**:
   - Updated `chl_curation.db` + `evaluation_log.csv`
   - `morning_report.md` (summary, actions, manual-review queue)
   - Optional `tuning_report.txt` (edge distributions, drift, costs)
7) **Dry‑run mode**: writes `.dryrun` sidecars only; no DB mutation.

## Agent Layer Design
- Single agent is sufficient; no multi-agent chat needed.
- Uses `scripts/curation/agents/autogen_openai_completion_agent.py` with AutoGen's `AssistantAgent`
- Supports any OpenAI-compatible endpoint:
  - **OpenAI API**: GPT-4, GPT-4o-mini, etc.
  - **Google Gemini**: via OpenAI-compatible endpoint
  - **Local LLMs**: Ollama/LM Studio (free, offline, recommended for cost control)
- Config surface (env or YAML via `scripts/scripts_config.yaml`):
  - YAML: `curation_llm.model`, `curation_llm.api_base`, optional `curation_llm.api_key`
  - Env overrides: `LLM_MODEL`, `LLM_API_BASE`, `LLM_API_KEY` (preferred) or provider-specific keys (OPENAI/GEMINI/GOOGLE/ANTHROPIC)
  - Examples: `gpt-4o-mini`, `gemini-2.0-flash`, `qwen2.5:14b` (Ollama)
- When `--with-rerank` was used in Phase 2, edge weights are rerank-only; agent relies on those scores as "strength" signals.

## Wrapper Script Plan (`scripts/curation/run_phase3.py`)
Inputs
- `--db-path` (default `data/curation/chl_curation.db`)
- `--communities` (default `data/curation/communities.json`)
- `--graph` (default `data/curation/similarity_graph.pkl`)
- `--config` (default `scripts/scripts_config.yaml` for LLM settings)
- `--max-rounds` (default 3), `--improvement-threshold` (default 0.05)
- `--dry-run`
- `--two-pass` (enable rerank refinement)
- `--rerank-model` (e.g., `Qwen3-Reranker-0.6B`)
- `--rerank-keep-threshold` (default 0.80 when two-pass)
- `--rerank-max-pairs-per-community` (cap cost; skip if exceeded)

Core flow
- Preflight: ensure files exist; ensure `sync_status`/embeddings present; refuse to run if >0 pending embeddings.
- Auto‑dedup pass using `auto_dedup` threshold.
- If `--two-pass`: rerank intra‑community pairs, rebuild graph/communities with rerank weights, then proceed.
- For each round:
  - Rebuild graph/communities from current DB and neighbor cache (skip rerank recompute; reuse weights).
  - Select communities by priority; skip those already resolved.
  - Call agent; apply decisions; log to DB and `evaluation_log.csv`.
  - Track progress metrics.
- After loop: write `morning_report.md` with counts, merges, rejected items, manual-review list, cost/LLM stats.

Outputs
- `data/curation/morning_report.md`
- Updated `curation_decisions` & `evaluation_log.csv`
- Optional `data/curation/tuning_report.txt`

## Prompts & Decisions (minimum contract)
- Input to agent: community members (id, title, playbook, context), edge list with weights, category, current round.
- Required output schema: `{decision: merge_all|merge_subset|keep_separate|manual_review, merges?: [[src,dst],...], notes?: str}`
- Safety rails: max community size already limited (default 50). Agent must abstain (`manual_review`) if uncertainty is high.

## What to document for developers
- How to configure LLM provider in `scripts/scripts_config.yaml`:
  - For OpenAI: set `provider: openai`, `model: gpt-4o-mini`, and `OPENAI_API_KEY` env var
  - For Gemini: set `provider: openai`, `model: gemini-pro`, `api_base: <gemini-endpoint>`, and `GEMINI_API_KEY` env var
  - For local LLM (Ollama): set `provider: local`, `model: qwen2.5:14b`, `api_base: http://localhost:11434/v1`, `api_key: dummy`
  - For local LLM (LM Studio): set `provider: local`, `model: <model-name>`, `api_base: http://localhost:1234/v1`, `api_key: dummy`
- Cost controls: max tokens per call, per‑run cost ceiling, and community budget (skip/abstain if exceeded).
- Resume: the wrapper reads `.curation_state.json`‑like file for phase‑3 state; `--reset-state` to start over.

## Acceptance for Phase 3
- End-to-end run completes without manual intervention.
- Auto‑dedup applied for ≥0.98 edges.
- At least one full agent round executes and logs to `evaluation_log.csv`.
- `morning_report.md` summarizes actions and pending manual reviews.
- Dry‑run leaves DB untouched and emits `.dryrun` artifacts.

---

## Implementation Steps (developer checklist)
> Phases 0–2 already delivered the decision contract, auto‑dedup, and rerank modules; Phase 3 just reuses them and adds the agent/wrapper.

1) **Agent configuration** (new)
   - Use `scripts/curation/agents/autogen_openai_completion_agent.py` with AutoGen's `AssistantAgent`.
   - Read model/api_base/api_key from `scripts/scripts_config.yaml` via `settings_util.py`.
   - Smoke-test with `scripts/curation/agents/prompts/curation_prompt.yaml` for OpenAI/Gemini/local (Ollama/LM Studio).
2) **Round loop + convergence** (new)
   - Iterate communities by priority, call agent, apply decisions; stop on max rounds or <5% improvement twice.
3) **Wrapper script `scripts/curation/run_phase3.py`** (new glue)
   - Wire CLI flags (`--two-pass`, rerank settings, model/api_base/api_key, dry-run, thresholds, max rounds).
   - Preflight (files exist, embeddings ready); resume/reset state support.
   - Emit artifacts: updated DB, `evaluation_log.csv`, `morning_report.md`, optional `tuning_report.txt`, `.dryrun` sidecars, and both `communities.json` and `communities_rerank.json` when `--two-pass`.
4) **Docs + examples** (update)
   - Usage examples for OpenAI and local endpoints.
   - Cost controls (token caps, pair caps) and troubleshooting.
5) **(Reused) Decision contract / Auto‑dedup / Rerank**
   - Already implemented in Phases 0–2; wrapper simply calls these modules.
