# Team Curation Walkthrough (Concise)

Short sample flow for Alice, Bob, and a curator (Carlos) using the semi-auto curation pipeline.

## Prereqs
- GPU backend required for curation (Apple Silicon or NVIDIA). CPU-only users can export only.
- Activate the matching virtualenv: `.venv-apple` or `.venv-nvidia` (CPU export: `.venv-cpu`).
- Category codes must match across members (same code/name/description) or merge will fail.

## 1) Member Export (Alice/Bob)
- Start CHL, open Operations, click "Export CSV".
- Each sends `{user}.export.zip` to the curator.

## 2) Curator Merge
```bash
cd ~/your/project/curated-heuristic-loop
mkdir -p data/curation/members
cd data/curation/members
unzip alice.export.zip
unzip bob.export.zip

cd ~/your/project/curated-heuristic-loop
python scripts/curation/merge_exports.py
```
Output: `data/curation/merged/` + `data/curation/merge_audit.csv`.

## 3) Init + Import Curation DB
```bash
python scripts/curation/init_curation_db.py --force
python scripts/curation/import_to_curation_db.py
```
All entries import as `sync_status=0` and `embedding_status=pending`.

## 4) Build Embeddings + FAISS
```bash
python scripts/curation/build_curation_index.py
```
GPU only; stop the API server before running.

## 5) Remove Identical Items (prep for Phase 2/3)
Run the duplicate pass to auto-merge obvious duplicates (high similarity only). This step removes identical or near-identical items and refreshes all Phase‑2 artifacts so Phase 3 can start immediately.
```bash
# auto-merge very high sim pairs (>=auto_dedup threshold)
python scripts/curation/find_pending_dups.py

# rebuild embeddings + FAISS after the auto-merges
python scripts/curation/build_curation_index.py

# build sparse graph + communities (Phase 2 output expected by Phase 3 step 6)
# Default is embed-only; add --with-rerank if you want rerank-only scoring.
python scripts/curation/build_communities.py --refresh-neighbors
# (optional) python scripts/curation/build_communities.py --refresh-neighbors --with-rerank
```
If you want a preview without DB changes on the first command, add `--dry-run` to `find_pending_dups.py`.

## 6) TBD

## 7) TBD

## 8) Team Sync
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
  neighbors.jsonl          # Phase 2 cache
  similarity_graph.pkl     # Phase 2 graph
  communities.json         # Phase 2 communities
```

## Notes
- `sync_status`: `0=PENDING`, `1=SYNCED`, `2=REJECTED`.
- Communities are per-category; manuals stay out of Phase 2/3 by default.
