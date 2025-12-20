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
- Abstraction: `scripts/curation/agents/agent_factory.py` returns an `AssistantAgent` wired to one of:
  - **OpenAI‑compatible**: `OpenAIChatCompletionClient`
  - **Claude via MCP**: `MCPChatCompletionClient` targeting `claude mcp serve`
  - **Local**: Ollama/LM Studio via their chat endpoint (OpenAI‑compatible)
- Config surface (env or YAML):
  - `LLM_PROVIDER` = `openai|anthropic_mcp|ollama|lmstudio`
  - `LLM_MODEL` (e.g., `gpt-4.1-mini`, `claude-3.5`, `qwen2.5:14b`)
  - `LLM_API_BASE`, `LLM_API_KEY` (ignored for MCP if using local auth)
  - `LLM_MAX_TOKENS`, `LLM_TEMPERATURE`
  - `MCP_CMD` (e.g., `claude mcp serve --stdio`)
- When `--with-rerank` was used in Phase 2, edge weights are rerank-only; agent relies on those scores as “strength” signals.

## Wrapper Script Plan (`scripts/curation/run_phase3.py`)
Inputs
- `--db-path` (default `data/curation/chl_curation.db`)
- `--communities` (default `data/curation/communities.json`)
- `--graph` (default `data/curation/similarity_graph.pkl`)
- `--llm-provider`, `--model`, `--api-base`, `--api-key`, `--mcp-cmd`
- `--max-rounds` (default 10), `--improvement-threshold` (default 0.05)
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
- How to start Claude MCP: `claude mcp serve --stdio` (or via MCP launch file); set `MCP_CMD` env.
- How to point to OpenAI‑compatible endpoints (LM Studio/Ollama): set `LLM_PROVIDER=openai`, `LLM_API_BASE`, `LLM_MODEL`, `LLM_API_KEY` (dummy if not required).
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
1) **Agent factory layer**
   - Add `scripts/curation/agents/agent_factory.py` that builds an `AssistantAgent` with pluggable providers (OpenAI-compatible, Claude MCP, Ollama/LM Studio).
   - Read provider/model/base/key/temperature from env or YAML; support `MCP_CMD` for Claude.
   - Add a tiny smoke-test prompt file (e.g., `prompts/hello.yaml`) that asks the model to reply “hello world”; run it once to validate connectivity for: OpenAI-compatible (ChatGPT/Gemini), Claude MCP, and local LM Studio.
   - Selection UX (for Carlos): set `curation.llm.provider` in `scripts/scripts_config.yaml` to `openai`, `anthropic_mcp`, or `local` (LM Studio/Ollama); no CLI override needed.
2) **Decision tool contract**
   - Define the minimal schema `{decision, merges?, notes?}` and validation.
   - Implement helpers to apply decisions to the curation DB and log to `evaluation_log.csv`.
3) **Auto‑dedup module**
   - Reuse Phase‑2 edge weights; merge pairs ≥ `auto_dedup`; update DB + decisions log.
   - Rebuild FAISS index if needed after merges.
4) **Rerank refinement (optional `--two-pass`)**
   - Rerank intra‑community pairs; cache scores; rebuild graph/communities with rerank weights.
   - Write `communities_rerank.json` and switch active community file when enabled.
5) **Round loop + convergence**
   - Iterate communities by priority, call agent, apply decisions.
   - Rebuild graph/communities each round; track improvement (<5% for 2 rounds → stop) or max rounds (default 10).
6) **Wrapper script `scripts/curation/run_phase3.py`**
   - Wire CLI flags (`--two-pass`, rerank settings, provider/model, dry-run, thresholds, max rounds).
   - Preflight checks (files exist, embeddings ready); resume/reset state support.
   - Emit artifacts: updated DB, `evaluation_log.csv`, `morning_report.md`, optional `tuning_report.txt`, `.dryrun` sidecars.
7) **Docs + examples**
   - Add usage examples for OpenAI, Claude MCP, and local endpoints.
   - Document cost controls (token caps, pair caps) and troubleshooting.
