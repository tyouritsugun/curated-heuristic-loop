# 07_web_refine Plan – Review (2025-11-09)

## Findings

### 1. `.env` migration has not started (blocking)
- Plan expectation: first-time setup hinges on a `.env` workflow, python-dotenv, and `GOOGLE_*` IDs loaded before the server/UI ever start. `scripts/setup.py` is supposed to clone credentials into `data/credentials`, chmod them, and validate Sheets via env vars (`doc/plan/07_web_refine.md:22-45` and `194-220`).
- Reality: the live onboarding flow still revolves around `scripts/scripts_config.yaml`, and none of the runtime surfaces load `.env`. The README directs operators to copy/edit the YAML and keep credentials there (`README.md:28-45`). `src/config.py` only reads `CHL_*` environment variables and never calls `load_dotenv` (`src/config.py:1-120`). `pyproject.toml` does not declare python-dotenv anywhere (`pyproject.toml:1-38`). No `.env.sample` exists in the repo root.
- Risk: The doc over-promises a secrets workflow that isn’t actually implemented, so following it would leave new users without any way to configure credentials or sheet IDs. The plan should either be reframed as future work or gated behind clear “not implemented” notes.

### 2. Settings dashboard is still interactive, not diagnostic-only (blocking)
- Plan expectation: `/settings` becomes a read-only status screen, with credential uploads, sheet IDs, onboarding copy, and model selection all removed (`doc/plan/07_web_refine.md:48-77`, `222-232`).
- Reality: the current template keeps the five-step onboarding checklist plus forms for loading `scripts_config.yaml` and editing model bundles (`src/web/templates/partials/settings_onboarding.html:9-25`, `src/web/templates/partials/sheets_card.html:5-35`, `src/web/templates/partials/models_card.html:1-32`). Nothing reads from `.env`, and users are still expected to paste file paths into the UI.
- Risk: Implementers may assume Settings work is finished, yet we have zero code to back the proposed simplification. The plan needs either (a) a phase outline tied to issues/PRs or (b) explicit acknowledgement that the UI still handles first-time setup.

### 3. Operations still expose manual index rebuilds; embeddings are not automatic (high)
- Plan expectation: Imports auto-generate embeddings, model changes kick off re-indexing, and the “Rebuild Index” button disappears (`doc/plan/07_web_refine.md:82-134`, `233-242`, `307-316`).
- Reality: `/operations` still renders explicit Import/Export/Rebuild buttons, with the index action wired to `/ui/operations/run/index` (`src/web/templates/partials/ops_operations_card.html:1-48`). The import script wipes embeddings, marks everything as `embedding_status="pending"`, and tells operators to rebuild FAISS manually later (`scripts/import.py:350-428`). No background worker is invoked to finish embeddings or to refresh FAISS automatically.
- Risk: The plan’s “automatic” language is misleading. Anyone following it would expect a push-button import that leaves search immediately usable, which is not true today.

### 4. `scripts/setup.py` does not enforce the described credential workflow (medium)
- Plan expectation: setup loads `.env`, copies credentials into `data/credentials/service-account.json`, locks permissions, and validates Google Sheets access (`doc/plan/07_web_refine.md:31-44`, `207-214`).
- Reality: the script’s docstring and code focus on creating the data directory, initializing SQLite, seeding sample rows, and optionally downloading GGUF models; it never touches credentials or Sheets APIs (`scripts/setup.py:1-120`, `225-352`). There is no place where `.env` is read.
- Risk: The “run setup.py to finish first-time config” guidance is incorrect today and can’t succeed without the supporting code paths.

### 5. Model management remains passive metadata, not an operations workflow (medium)
- Plan expectation: Operations should gain a “Model Management” card with download + re-embed flows, while Settings loses the model picker; selecting a new model should immediately schedule re-embedding (`doc/plan/07_web_refine.md:94-105`, `233-241`, `307-315`).
- Reality: the model picker still lives on `/settings` and only stores preferences via `SettingsService.update_models`, which simply upserts JSON metadata with no downstream jobs (`src/web/templates/partials/models_card.html:1-32`, `src/services/settings_service.py:308-334`). The `/operations` template renders only the existing controls/index/workers/queue/jobs cards (`src/web/templates/operations.html:27-43`).
- Risk: Operators who change models through the UI receive no prompt to re-embed, nor is an automatic job queued, so FAISS can silently mismatch the embeddings the UI claims are active.

## Recommendations
1. Call out in the plan that Phases 1–5 are NOT implemented yet, or link to execution issues/PRs so readers know the status.
2. Treat the `.env` migration and Settings/Operations restructuring as explicit backlog items with acceptance criteria before advertising the new UX in docs.
3. Until automation exists, keep documenting the current manual steps (scripts_config.yaml onboarding and Rebuild Index button) to prevent operators from being blocked.
