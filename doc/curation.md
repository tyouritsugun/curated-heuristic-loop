[Return to README.md](../README.md)
# Curation Overnight Overview

## Why curation and what it does
- Standardizes team knowledge (experiences + skills) through merge/split and review.
- Produces approved TSVs for publishing back to the team.
- See [Why](curation_spec.md#why-do-we-need-team-curation).
- See [Curation Spec flow diagram](curation_spec.md#flow-diagram)

## Architecture (see spec)
- Overnight entrypoints, defaults, core loop, and outputs are documented in
  [Curation Spec architecture](curation_spec.md#architecture).

# Team Curation Walkthrough

Short sample flow for team members Alice, Bob, and a curator (Carlos) using the semi-auto curation pipeline.

## Roles
- **Alice/Bob** export their local data.
- **Carlos** runs merge + curation + publish.

## Prereqs
- GPU backend required for curation (Apple Silicon or NVIDIA). CPU-only users can export only.
- Activate the matching virtualenv: `.venv-apple` or `.venv-nvidia` (CPU export: `.venv-cpu`).
- Category codes must be valid per `src/common/config/categories.py` (canonical taxonomy). Member CSV categories are ignored.
- Carlos needs an LLM endpoint: either a cost‑effective commercial model (e.g., Gemini Flash) or a local model (e.g., ChatGPT OSS). Alice/Bob do not necessarily need these.
- Stop the API server before running curation scripts to avoid database locks/errors.

## LLM Access (Optional)
- Only needed if you run the overnight curation. Choose one path:
  1) **Commercial OpenAI-compatible API** (ChatGPT/Gemini): set the appropriate API key in `.env` (e.g., `OPENAI_API_KEY`, `GEMINI_API_KEY`, `GOOGLE_API_KEY`). Use cheaper mini tiers; e.g., prefer `gpt-5-mini-2025-08-07` over `gpt-5.2-2025-12-11`, or prefer `gemini-3-flash-preview` over heavier Gemini models.
  2) **Local endpoint (zero API cost, requires your GPU)**: LM Studio or Ollama on an OpenAI-compatible endpoint; set `api_base` in `scripts/scripts_config.yaml` to your local server (e.g., `http://localhost:11434/v1`), and set `LLM_API_KEY` to any placeholder if your local server ignores it. `gpt-oss-20b` is recommended.
- Keep API keys in `.env` (see `.env.sample`); do not commit them.
- Dependencies: `requirements_apple.txt` and `requirements_nvidia.txt` already include `autogen` + `autogen-ext[openai]` for the agent.

## 1) Member Export (Alice/Bob)
- Start CHL(`./start-chl.sh`), open `http://127.0.0.1:8000/settings#configuration`,  click "Export CSV".
- Each sends `{user}.export.zip` to the curator.
- **When CHL_SKILLS_ENABLED=true**: Export CSV pulls both experiences + skills from CHL DB.
- **When CHL_SKILLS_ENABLED=false**: Export CSV uses a modal to choose the external skills source.
  - Experiences still export from CHL DB.
  - Skills export from the selected external source (Claude/Codex), written to `skills.csv` inside the ZIP.
Sample sheets (for warm‑up/testing):
- Alice: `1XCa6P2_JL-exUJvaW1F9aHMYRs5yJPzMMlbZQdcDbTk`
- Bob: `1PAGzcYCJSTjXl6r6KwUB7Ju5vvNT3e8WwT-SV6Wonxo`

## 2) Curator Merge + Import (wrapped) — Carlos
After unzipping member exports into `data/curation/members/`, the file structure should be:
```
data/curation/members/
  alice/
    experiences.csv
    skills.csv
  bob/
    experiences.csv
    skills.csv
```
Then run:
```bash
python scripts/curation/common/merge_all.py
```
If you only need experiences, you can still use:
```bash
python scripts/curation/experience/merge/merge2db.py
```
This includes a quick LLM health check plus merge + import.

## 3) Run Overnight Curation (wrapped) — Carlos
Note: Curation can take a while. For ~200 rows, start it before you leave (or overnight) and check results in the morning.
- Defaults are in `scripts/scripts_config.yaml` and the prompt in `scripts/curation/agents/prompts/curation_prompt.yaml`.
- If you need to override behavior, edit those files instead of CLI flags.
```bash
python scripts/curation/common/overnight_all.py
```
[Incoming data is already atomic?](./curation_spec.md#is-it-possible-to-skip-the-atomicity-pre-pass-if-i-am-sure-the-experiences-and-skills-are-already-atomic).
Operational defaults and knobs: see [Curation Spec architecture](curation_spec.md#architecture).

## Community building (see spec) — See [Curation Spec community building](curation_spec.md#community-building)

## 4) Review and Publish (Spreadsheet) — Carlos
- Copy `data/curation/approved/experiences.tsv` and `data/curation/approved/skills.tsv` to Excel or Google Sheets for a quick review.
- If satisfied, publish to the team (Alice and Bob) via the UI.
  - Export behavior:
    - **CHL_SKILLS_ENABLED=true**: Skills and experiences export from CHL DB.
    - **CHL_SKILLS_ENABLED=false**: UI prompts for external skills source (Claude/ChatGPT/None) to populate skills output.
UI: Operations → Import from Google Sheet  

Import behavior:
- **CHL_SKILLS_ENABLED=true**: Experiences + skills import into CHL DB.
- **CHL_SKILLS_ENABLED=false**: Experiences import into CHL DB; skills route to external targets (Claude/Codex) and are written to SKILLS.md files.

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

Approved outputs:
- `data/curation/approved/experiences.tsv`
- `data/curation/approved/skills.tsv`
- `data/curation/approved/curation_summary.md`
