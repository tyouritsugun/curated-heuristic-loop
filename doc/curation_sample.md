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

## 5) Remove Identical Items
Run the duplicate review prompt and mark only obvious duplicates. This step removes identical or near-identical items before Phase 3.
```bash
python scripts/curation/find_pending_dups.py
```
This opens an interactive prompt where you mark duplicates. If unsure, keep the items for Phase 3.

After finishing, rebuild embeddings + index:
```bash
python scripts/curation/build_curation_index.py
```

## 6) (Optional) Build Communities for Phase 3
```bash
python scripts/curation/build_communities.py \
  --db-path data/curation/chl_curation.db \
  --output data/curation/communities.json
```
Outputs `neighbors.jsonl`, `similarity_graph.pkl`, and `communities.json`.

## 7) Export + Publish
```bash
python scripts/curation/export_curated.py --output data/curation/approved
python scripts/curation/publish_to_canonical.py --input data/curation/approved --sheet-id <SHEET_ID>
```

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
