# CHL Manual

This manual covers operational tasks, scripts, and workflows for the Curated Heuristic Loop system.
For a copy-paste end-to-end setup, see Quickstart in README.md.
For MCP client configuration (Cursor, Codex CLI), see README.md.

Preconditions
- Install [uv](https://docs.astral.sh/uv/) and ensure Python 3.10/3.11 is available (see README).
- *(Optional)* Set experience root: `export CHL_EXPERIENCE_ROOT="$(pwd)/data"` (defaults to `<project_root>/data` if not set)

## Initial Setup

### First-Time Setup
```bash
cd /your/project/root
uv run python scripts/setup.py
```

> **Tip:** All script examples in this manual assume you're invoking them with the project’s managed environment. Prefix commands with `uv run` (e.g., `uv run python scripts/export.py`) if you rely on uv to resolve dependencies.

**Note:** `CHL_EXPERIENCE_ROOT` now defaults to `<project_root>/data` if not set. The data directory will be auto-created if it doesn't exist.

**Purpose:** One-time initialization for new installations

**When to use:**
- First time cloning the repository
- After deleting `data/` directory
- After changing embedding model selection (`CHL_EMBEDDING_REPO` / `CHL_EMBEDDING_QUANT`)
- To re-download corrupted models

**What it does:**
1. Creates `data/` directory structure
2. Initializes SQLite database (creates tables)
3. Downloads embedding & reranker models (if not already cached)
4. Creates FAISS index directory
5. Validates setup completeness

**Options:**
```bash
# Automatic setup (recommended) - uses smallest models (0.6B)
uv run python scripts/setup.py

# Interactive model selection - choose larger models (4B, 8B)
uv run python scripts/setup.py --download-models

# Force re-download models (if models are corrupted or you want to update)
uv run python scripts/setup.py --force-models
```

**Interactive Model Selection:**
When using `--download-models`, you'll see a menu to choose:
- **Embedding models**: 0.6B (~524 MB), 4B (~4 GB), 8B (~8 GB)
- **Reranker models**: 0.6B (~612 MB), 4B (~4 GB), 8B (~8 GB)

Larger models provide better quality but require more RAM/VRAM. For recommended model selection based on tested hardware, see the main [README](../README.md#prerequisites).

### Seed starter content (recommended)

Run this after setup to load the default CHL categories and sample entries and sync the guidelines **inside uv's environment**:

```bash
# Full seed: categories, sample entries, and guidelines (default)
uv run python scripts/seed_default_content.py

# Sync guidelines only (skip starter content)
uv run python scripts/seed_default_content.py --skip-seed

# Seed content only (skip guideline sync)
uv run python scripts/seed_default_content.py --skip-guidelines
```

This command is idempotent; rerun it to restore starter data or refresh guidelines.

The guideline sync keeps the `GLN` category aligned with the Markdown sources. It re-imports `generator.md` and `evaluator.md`, deletes stale manuals, and removes a guide if its source file is missing. After syncing, you can retrieve the manuals via MCP:

```bash
codex-cli mcp tool call chl get_guidelines --params guide_type=generator
codex-cli mcp tool call chl get_guidelines --params guide_type=evaluator
```

**Output (first run):**
```
============================================================
  CHL MCP Server - First-Time Setup
============================================================

✓ Data directory: data/
✓ FAISS index directory: data/faiss_index/
✓ Database initialized: data/chl.db
  - 0 categories
  - 0 experiences
  - 0 manuals

Downloading models (this may take 5-10 minutes)...

  [1/2] Qwen/Qwen3-Embedding-0.6B
        ✓ Downloaded (dimension: 1024)

  [2/2] Qwen/Qwen3-Reranker-0.6B
        ✓ Downloaded

✓ Models ready
✓ Setup validation passed
```

**Output (subsequent runs - models cached):**
```
✓ Models already cached
  - Embedding: Qwen/Qwen3-Embedding-0.6B
  - Reranker: Qwen/Qwen3-Reranker-0.6B
  (Use --force-models to re-download)

============================================================
  Setup Complete!
============================================================

Next steps:

  1. Generate embeddings:
     python scripts/sync_embeddings.py

  2. Start MCP server:
     python src/server.py
```

**Preconditions:**
- Python 3.10 or 3.11 (install once with `uv python install 3.11`)
- Install dependencies with the supported runtime (includes llama-cpp, FAISS, gspread): `uv sync --python 3.11 --extra ml`
- `CHL_EXPERIENCE_ROOT` environment variable set (defaults to `<project_root>/data` if omitted)
- Internet connection (for model download)

**Troubleshooting:**
- "ML dependencies not found": Install ML extras with `uv sync --python 3.11 --extra ml`
- "Model download failed": Check internet connection, try `python scripts/setup.py --force-models`
- "Permission denied": Use `chmod +x scripts/setup.py`
- Models already downloaded but showing as missing: Try `python scripts/setup.py --force-models`

---

## 1: Search & Embeddings

### Rebuild Search Index
```bash
python scripts/rebuild_index.py
```

**Purpose:** Regenerate embeddings and FAISS index from scratch.

**When to use:**
- After changing embedding model selection (`CHL_EMBEDDING_REPO` / `CHL_EMBEDDING_QUANT`)
- After bulk data imports
- When FAISS index becomes corrupted
- When embedding_status shows many 'failed' entries

**Process:**
1. Deletes existing embeddings and FAISS metadata
2. Generates embeddings for all experiences and manuals
3. Builds new FAISS index
4. Updates embedding_status to 'embedded'

**Environment variables (optional overrides):**
- `CHL_EXPERIENCE_ROOT` - path to data directory
- `CHL_DATABASE_PATH` - path to SQLite database
- `CHL_EMBEDDING_REPO` / `CHL_EMBEDDING_QUANT` - override the embedding GGUF selection (defaults recorded by `scripts/setup.py`)
- `CHL_RERANKER_REPO` / `CHL_RERANKER_QUANT` - override reranker selection (defaults recorded by `scripts/setup.py`)

**Output:**
- Progress bar showing embedding generation
- Final statistics: entities processed, time taken, errors
- New FAISS index files in `data/faiss_index/`

---

### Sync Embeddings (Incremental)
```bash
python scripts/sync_embeddings.py
```

**Purpose:** Fix embedding inconsistencies without full rebuild.

**When to use:**
- Some experiences have embedding_status='pending' or 'failed'
- After server crash during embedding generation
- Periodic maintenance (weekly/monthly)

**Process:**
1. Find experiences/manuals with status='pending' or 'failed'
2. Retry embedding generation
3. Find embeddings not in FAISS index
4. Add missing vectors to index

**Output:**
- Entities fixed: pending → embedded
- FAISS entries added
- Remaining failures (if any)

Important
- If the MCP server is running when you execute this script, restart the server afterward so it loads the updated FAISS index. The server reads the index on startup and does not hot‑reload changes.

---

### Search Health Check
```bash
python scripts/search_health.py
```

**Purpose:** Inspect search index health and statistics.

**When to use:**
- Diagnosing search performance issues
- Monitoring embedding status
- Validating index integrity

**Output:**
```json
{
  "totals": {"experiences": 150, "manuals": 25},
  "embedding_status": {"pending": 5, "embedded": 168, "failed": 2},
  "faiss": {
    "available": true,
    "model": "Qwen/Qwen3-Embedding-0.6B",
    "dimension": 1024,
    "vectors": 168,
    "by_type": {"experience": 145, "manual": 23},
    "index_path": "data/faiss_index/unified_qwen_qwen3-embedding-06b.index",
    "last_updated": "2025-10-26T15:30:00Z"
  },
  "warnings": [
    "2 entities have failed embeddings",
    "5 entities have pending embeddings"
  ]
}
```

---

## 2: Export & Import

The export/import scripts read their settings from `scripts/scripts_config.yaml`.
Populate that file once and re-run the scripts with a single command. A minimal
example:

```yaml
# scripts/scripts_config.yaml
data_path: ../data
google_credentials_path: ../credentials/service_account.json

export:
  # sheet_id: your-review-sheet-id
  experiences_sheet:
    # id: your-review-sheet-id
    worksheet: Experiences
  manuals_sheet:
    # id: your-review-sheet-id
    worksheet: Manuals

import:
  # sheet_id: your-published-sheet-id
  experiences_sheet:
    # id: your-published-sheet-id
    worksheet: Experiences
  manuals_sheet:
    # id: your-published-sheet-id
    worksheet: Manuals
```

Uncomment and replace the `sheet_id`/`id` placeholders with your actual Google
Sheet identifiers. Adjust paths as needed; relative values are resolved relative
to the config file.

### Check Export Status (optional)
```bash
```
Use this to review pending entries or sync metadata before exporting.

### Export to Google Sheets
```bash
uv run python scripts/export.py
```
- Writes **all** experiences and manuals from SQLite to the configured
  worksheets.
- Add `--dry-run` to preview counts without touching Google Sheets.

### Import from Google Sheets (destructive)
```bash
uv run python scripts/import.py --yes
```
- Replaces the local experiences/manual tables with the worksheet contents.
- Automatically regenerates embeddings and the FAISS index (skip with `--skip-embeddings`).
- If you skip or the sync is skipped due to missing ML dependencies, rerun `python scripts/sync_embeddings.py --retry-failed`
  afterwards and restart the MCP server.
- Omit `--yes` to receive an interactive confirmation prompt.

---

## Complete Workflow Summary (MVP)

This section describes the end-to-end workflow for team curation using Google Sheets.

### Setup (One-Time)

1. **Create Google Sheets**
   - Review Sheet: For curator review of pending entries
   - Published Sheet: Curated, approved entries for team sync

2. **Set up Service Account**
   - Create service account in Google Cloud Console
   - Download credentials JSON
   - Share both sheets with service account email
   - (Optional) Set `CHL_GOOGLE_CREDENTIALS_PATH` environment variable if you prefer managing credentials outside `scripts/scripts_config.yaml`

3. **Configure script settings**
   - Edit `scripts/scripts_config.yaml` and populate the `export` and `import`
     sections with your Google Sheet IDs, worksheet names, credentials path,
     and any custom data directory overrides.

### Regular Workflow

#### 1. Export to Review Sheet
```bash
uv run python scripts/export.py
```
Write the full SQLite dataset—categories, experiences, and manuals—to the configured Google Sheets worksheets.
You can also create `scripts/scripts_config.yaml` to store defaults (credentials, sheet IDs, data path, etc.) and simply run `uv run python scripts/export.py`; CLI flags still override the YAML values.

#### 2. Manual Curation (Google Sheets)
Curators work directly in the exported worksheets:
- Edit `title`, `playbook`, `content`, or `summary` fields to capture the
  curated guidance.
- Adjust `section` when recategorising experiences; ensure values remain one of
  `useful`, `harmful`, or `contextual`.
- Remove rows that should not be published or add new ones inline if needed.
- Leave the `id` and `category_code` columns unchanged so imports remain stable.

Sheet columns (exported as plain values):
- Categories: `code`, `name`, `description`, `created_at`.
- Experiences: `id`, `category_code`, `section`, `title`, `playbook`, `context`,
  `source`, `sync_status`, `author`, `embedding_status`, `created_at`,
  `updated_at`, `synced_at`, `exported_at`.
- Manuals: `id`, `category_code`, `title`, `content`, `summary`, `source`,
  `sync_status`, `author`, `embedding_status`, `created_at`, `updated_at`,
  `synced_at`, `exported_at`.

Keep the identifier and category columns stable—downstream imports expect them
unchanged. Provide ISO 8601 strings when editing timestamp columns.

### Workflow Checklist

1. **Export** – `uv run python scripts/export.py` writes the latest SQLite entries
   to Google Sheets using the configuration in `scripts/scripts_config.yaml`.
2. **Curate in Sheets** – reviewers edit the exported category/experience/manual worksheets, remove rows, or adjust values as needed. Ensure IDs stay unique and category codes remain three-letter uppercase values.
3. **Import** – `uv run python scripts/import.py --yes` overwrites the local
   categories, experiences, and manuals with the curated sheet content and regenerates embeddings/FAISS metadata automatically (skip with `--skip-embeddings`).
4. **Regenerate embeddings (only if skipped/failing)** – Run `python scripts/sync_embeddings.py --retry-failed`
   when you opt out or when the automatic step is skipped. Restart the MCP server to
   pick up the refreshed vectors in either case.

Tips:
- Keep a backup of `data/chl.db` if you want the option to roll back imports.
- The import script treats empty strings as NULL/None. Provide ISO 8601 strings
  for timestamp fields when editing directly in Sheets.

---

---

## Script Development Guidelines

When adding new scripts:

1. **Location:** Place in `scripts/` directory
2. **Naming:** Use snake_case, descriptive names
3. **Structure:**
   ```python
   #!/usr/bin/env python3
   """One-line description of what this script does."""
   import sys
   from pathlib import Path

   # Add project root to path
   sys.path.insert(0, str(Path(__file__).parent.parent))

   from src.config import get_config
   # ... other imports

   def main():
       # Script logic here
       pass

   if __name__ == "__main__":
       main()
   ```

4. **Documentation:** Add entry to this file with:
   - Command to run
   - Purpose
   - When to use
   - Expected output

5. **Error Handling:**
   - Print clear error messages
   - Exit with non-zero code on failure
   - Log to console (not just files)

6. **Environment:**
   - Use `get_config()` for configuration
   - Respect environment variables
   - Provide sensible defaults

---

## Troubleshooting

### Script won't run
```bash
# Make sure you're in project root
cd /path/to/curated-heuristic-loop

# Check Python path
python --version  # Should be 3.10+ (uv can pin 3.11 via `uv python install 3.11`)

# Run with uv-managed environment
```

### Import errors
```bash
# Sync dependencies (includes ML extras)
uv sync --python 3.11 --extra ml
```

### Permission denied
```bash
# Make script executable (if needed)
chmod +x scripts/rebuild_index.py
```

---

## Environment Variable Reference

Scripts respect these environment variables (legacy support).
When using `scripts/export.py` and `scripts/import.py`, prefer configuring
`scripts/scripts_config.yaml` instead of environment variables.

### Core Settings
| Variable | Default | Purpose |
|----------|---------|---------|
| `CHL_EXPERIENCE_ROOT` | `<project_root>/data` | Path to data directory |
| `CHL_DATABASE_PATH` | `<experience_root>/chl.db` | SQLite database path (relative values resolve under `<experience_root>`) |
| `CHL_DATABASE_ECHO` | `false` | Log SQL queries |

### Search & ML
| Variable | Default | Purpose |
|----------|---------|---------|
| `CHL_EMBEDDING_REPO` | Recorded in `data/model_selection.json` | Embedding GGUF repository (e.g., `Qwen/Qwen3-Embedding-0.6B-GGUF`) |
| `CHL_EMBEDDING_QUANT` | Recorded in `data/model_selection.json` | Embedding quantization (e.g., `Q8_0`) |
| `CHL_RERANKER_REPO` | Recorded in `data/model_selection.json` | Reranker GGUF repository |
| `CHL_RERANKER_QUANT` | Recorded in `data/model_selection.json` | Reranker quantization |
| `CHL_FAISS_INDEX_PATH` | `<experience_root>/faiss_index` | FAISS index directory (relative values resolve under `<experience_root>`) |

### Export & Sync
| Variable | Default | Purpose |
|----------|---------|---------|
| `CHL_REVIEW_SHEET_ID` | (none) | Google Sheets ID for Review Sheet |
| `CHL_PUBLISHED_SHEET_ID` | (none) | Google Sheets ID for Published Sheet |
| `CHL_EXPORT_BATCH_SIZE` | `1000` | Rows per API call |

These variables remain available for legacy tooling, but the preferred
configuration path is `scripts/scripts_config.yaml` for export/import scripts.
