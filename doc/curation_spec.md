# Curation Spec

## QA

### Why do we need team curation?

**A:** It keeps the shared knowledge base consistent and non-duplicative across members, so everyone imports the same validated skills/experiences and avoids drift or conflicting guidance. As skills and experiences accumulate, team efficiency increases because sharing with AI is a simple copy/paste import rather than a slow human handoff.

### Is it possible to skip the atomicity pre-pass if I am sure the experiences and skills are already atomic?

**A:** Yes. Run the overnight command with `--skip-atomicity-pre-pass` to skip atomicity for both experiences and skills:
```bash
python scripts/curation/common/overnight_all.py --skip-atomicity-pre-pass
```

## Flow Diagram

```mermaid
flowchart TD
  A[Member export<br/>experiences.csv + skills.csv] --> B[Merge into curation DB]
  B --> C1[Experience atomicity pre-pass]
  B --> C2[Skill atomicity pre-pass]
  C1 --> D[Build embeddings + FAISS index]
  C2 --> D
  D --> E1[Experience communities + overnight loop]
  D --> E2[Skill candidate grouping]
  E2 --> F2[Skill relationship analysis + auto-apply]
  E1 --> G[Export approved TSVs]
  F2 --> G
  G --> H[Publish to team (Sheets/Excel)]
  H --> I[Team import to local DB or external SKILL.md]
```

## Architecture

### Entrypoints
- Experiences + skills:
  ```bash
  python scripts/curation/common/overnight_all.py
  ```
- Use `--no-skills` to skip skill curation for a run.

### Defaults and config
- Defaults are read from:
  - `scripts/scripts_config.yaml`
  - `scripts/curation/agents/prompts/curation_prompt.yaml`
- Overnight wrapper defaults to a **DB copy**:
  - `data/curation-copy/chl_curation.db`
  - `data/curation-copy/.curation_state_loop.json`
  - Use this for safe experimentation or retries; for production curation runs, use the main curation DB.

### Core loop (high level)
1) Auto-dedup high-confidence pairs (>= `auto_dedup` threshold).
2) LLM decides per community: `merge_all`, `merge_subset`, `keep_separate`, `manual_review` (human curator review).
3) Apply decisions → rebuild communities (no FAISS re-query).
4) Stop when progress is low or max rounds reached.

### Community building
Why: Communities group nearby experiences so the LLM reviews related items together instead of scanning the whole dataset. Groups are separated by category to reduce candidate volume and review load.

How: Build a sparse similarity graph from top‑K embedding neighbors, then run community detection (Louvain or Leiden) per category; results are cached in `communities.json`.
Pros/cons:
- Embeddings: fast, scalable, good for broad semantic grouping; weaker at fine‑grained distinctions.
- Rerank: improves precision on close candidates; higher cost and slower at scale.

Rerank (`--with-rerank`):
- Use rerank when embedding neighbors feel too noisy or semantically weak.
- Rerank changes neighbor scores before communities are formed; it does not rerun during the overnight loop.

Algorithm choice:
- Default: **[Louvain method](https://en.wikipedia.org/wiki/Louvain_method)** (`curation.algorithm: louvain` in `scripts/scripts_config.yaml`).
- Switch to **[Leiden algorithm](https://en.wikipedia.org/wiki/Leiden_algorithm)** by setting `curation.algorithm: leiden` or by passing `--algorithm leiden` to `scripts/curation/experience/merge/build_communities.py`.

### Curation
The curation loop uses LLMs at a few targeted points:
- **Normalization**: assign missing skill categories (LLM category mapping) and generate outlines when needed.
- **Atomicity**: verify skills/experiences are single‑purpose; split if they are not.
For experiences:
  - The atomicity pre-pass reviews the full experience content (title/playbook/context) and splits only when multiple distinct experiences are bundled together.
  - The LLM returns `atomic` vs `split` (no numeric threshold), and split items are re‑outlined before the community loop.
For skills:
  - The atomicity pre-pass uses the skill outline (from `metadata["chl.outline"]`) plus full content in the prompt.
  - There is no numeric threshold; the LLM decides `atomic` vs `split` directly.
  - If an outline is missing, it should be generated earlier during import/normalization.
- **Merge decisions**: decide relationships (merge/keep/split/flag) and generate merged content when needed.
- **Iteration**: rebuild communities and repeat until the stop thresholds in `scripts/scripts_config.yaml` are met.

Carlos can tune thresholds and behavior in `scripts/scripts_config.yaml` to fit team needs.

LLM choice:
- Use a cost‑effective model (e.g., Gemini Flash or local ChatGPT OSS) for curation.
- Expensive commercial LLMs are usually over‑spec for this task.

### Outputs
- `data/curation/morning_report.md`
- `data/curation/evaluation_log.csv`
- `data/curation/communities.json`
