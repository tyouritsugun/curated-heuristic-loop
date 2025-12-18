# Phase 2 — Sparse Graph & Community Detection

**Status:** Draft for Implementation
**Prerequisites:** Phase 1 complete (merge, import, FAISS index, basic duplicate detection)
**Purpose:** Build sparse similarity graph and detect non-overlapping communities for Phase 3 LLM processing

---

## Overview

### Goals

Phase 2 prepares data structures for LLM-powered curation (Phase 3) by:

1. **Sparse graph construction** - Build similarity graph with configurable threshold
2. **Community detection** - Identify non-overlapping clusters using Louvain or Leiden
3. **Community ranking** - Prioritize communities by similarity and density
4. **Data output** - Provide structured community data for LLM agent input

---

## Implementation Status

### ✅ Already Implemented (Phase 1)

| Component | Location | Notes |
|-----------|----------|-------|
| Embedding generation | `src/api/gpu/embedding_client.py` | GPU-accelerated |
| FAISS indexing | `src/api/gpu/faiss_manager.py` | IndexFlatIP with metadata |
| Similarity search | `duplicate_finder.py` | Top-k neighbor search |
| Bucket thresholds | `duplicate_finder.py` | high/medium/low classification |
| Resume state | `state_manager.py` | Checkpoint support |
| Interactive review | `interactive_reviewer.py` | Manual curation UI |
| **LLM Reranker** | `src/api/gpu/reranker_client.py` | ⚠️ Exists but not integrated! |

### 🔨 To Implement (Phase 2)

| Component | Priority | Complexity |
|-----------|----------|------------|
| Sparse graph builder | **Critical** | Medium |
| Community detection | **Critical** | Medium |
| Community ranking | **Critical** | Low |
| Community data export | High | Low |

---

## Workflow

### High-Level Process

Phase 2 builds the graph structure that Phase 3 will process:

1. **Sparse Graph Construction:** Query FAISS for top-k neighbors, filter by min similarity threshold (neighbors cached to jsonl for reuse)
2. **Community Detection:** Apply Louvain or Leiden algorithm to find non-overlapping clusters
3. **Community Ranking:** Score communities by similarity, density, and size
4. **Export Community Data:** Output structured community information for Phase 3 LLM agent

### Community Ranking

Communities are ranked by priority score:
- **Primary factor:** Average pairwise similarity (60% weight)
- **Secondary factor:** Graph density - connectivity ratio (30% weight)
- **Tertiary factor:** Size score - prefer moderate sizes 3-10 items (10% weight)

### Output Format

Phase 2 produces community data files containing:
- Community membership (which items belong to each community)
- Pairwise similarity scores within each community
- Community metadata (size, average similarity, density)
- Priority ranking for processing order

---

## Configuration

### Graph Construction & Threshold Parameters

```yaml
# scripts/scripts_config.yaml
curation:
  # Sparse graph settings
  min_similarity_threshold: 0.72  # Ignore edges below this
  top_k_neighbors: 50             # Keep top-k neighbors per item (also persisted to neighbors cache)
  per_category: true              # Build graphs within each category only

  # Community detection
  algorithm: "louvain"  # Options: "louvain" or "leiden"

  # Community filtering
  min_community_size: 2   # Ignore singleton communities
  max_community_size: 50  # Flag large communities for special handling

  # Similarity signals (Phase 2 -> Phase 3 continuity)
  use_rerank: true
  blend_weights: {embed: 0.7, rerank: 0.3}  # weighted blend; rerank cached to disk
  rerank_cache_dir: "data/curation/rerank_cache"

  # Threshold table (user-tunable)
  thresholds:
    edge_keep: 0.72        # keep edge in sparse graph
    community_detect: 0.72 # same as edge_keep unless auto-tuned
    auto_dedup: 0.98       # Phase 3 merge-without-review
    high_bucket: 0.92      # Phase 1/interactive high bucket
    medium_bucket: 0.75    # Phase 1/interactive medium bucket

  # Output paths
  community_data_file: "data/curation/communities.json"
  graph_file: "data/curation/similarity_graph.pkl"
```

### Rationale

- **0.72 min similarity:** Optimal density for Louvain/Leiden community detection
- **Top-k neighbors:** Balance between graph sparsity and capturing relevant relationships
- **Max 50 items/community:** Prevents overwhelming LLM in Phase 3

---

## Technical Specifications

### 1. Sparse Graph Construction

**Process:**
1. For each category independently, query FAISS for top-k neighbors per item (k=50); persist neighbor list to `data/curation/neighbors.jsonl` with metadata (model, top_k, threshold, index mtime)
2. Filter edges below min_similarity_threshold (0.72)
3. Symmetrize using max of bidirectional scores
4. Blend scores: `w_embed * embed + w_rerank * rerank` when rerank available; fall back to embed-only when rerank missing; cache rerank scores in `rerank_cache_dir`
5. Build NetworkX graph with weighted edges from cached neighbors (default) or fresh FAISS when cache invalidated

**Output:** NetworkX graph object saved to disk for Phase 3

### 2. Community Detection

**Algorithm:** Louvain or Leiden (non-overlapping partition), executed per-category

**Why This Works:**
- Naturally produces **non-overlapping partitions**
- Each node belongs to exactly one community
- Fast: O(n log n) for sparse graphs
- Modularity optimization ensures cohesive clusters

**Libraries:**
- `python-louvain` for Louvain algorithm (simpler)
- `leidenalg` + `igraph` for Leiden algorithm (better quality)

### 3. Community Data Structure

**Output JSON format:**
```json
{
  "communities": [
    {
      "id": "COMM-001",
      "members": ["EXP-DVT-001", "EXP-DVT-002", "EXP-DVT-005"],
      "avg_similarity": 0.87,
      "density": 0.92,
      "size": 3,
      "priority_score": 0.885,
      "edges": [
        ["EXP-DVT-001", "EXP-DVT-002", 0.89],
        ["EXP-DVT-001", "EXP-DVT-005", 0.85],
        ["EXP-DVT-002", "EXP-DVT-005", 0.87]
      ]
    }
  ],
  "metadata": {
    "total_items": 100,
    "total_communities": 15,
    "graph_edges": 847,
    "min_threshold": 0.72
  }
}
```

---

## Testing & Validation

### Test Datasets

1. **Clear Communities** - 100 items with 10 distinct clusters (high intra-cluster similarity)
2. **Overlapping Boundaries** - Items with cross-cluster edges to test partition quality
3. **Sparse Graph** - Test with varying thresholds (0.65, 0.72, 0.80)
4. **Large Scale** - 1000 items to validate performance

### Validation Checklist

- [ ] Communities are non-overlapping (each item in exactly one community)
- [ ] Communities are per-category; no cross-category members
- [ ] Priority ranking produces sensible ordering (high similarity communities first)
- [ ] Graph construction filters edges correctly by threshold
- [ ] Community data JSON exports correctly
- [ ] NetworkX graph can be loaded in Phase 3
- [ ] Rerank cache is optional; pipeline falls back gracefully when absent

---

## CLI Interface

```bash
# Build sparse graph and detect communities (uses neighbors cache by default)
python scripts/curation/build_communities.py \
  --db-path data/curation/chl_curation.db \
  --output data/curation/communities.json

# Force rebuild neighbors from FAISS
python scripts/curation/build_communities.py --refresh-neighbors

# Enable rerank blend (off by default for speed)
python scripts/curation/build_communities.py --with-rerank

# View community summary only
python scripts/curation/build_communities.py --summary-only

# Adjust threshold for experimentation
python scripts/curation/build_communities.py --min-threshold 0.75 \
  --output data/curation/communities_075.json
```

---

## Dependencies

### Python Packages

```bash
# Community detection (choose one)
pip install python-louvain      # Louvain (simpler)
pip install leidenalg igraph    # Leiden (better quality)

# Already installed: scipy, numpy, faiss, networkx, sqlalchemy
```
Notes:
- Leiden requires igraph system libs; if unavailable, default to Louvain and warn.
- Rerank cache directory is optional; if empty, edges fall back to embed-only scores.

---

## Implementation Roadmap

### Phase 2 Tasks
- [ ] Create `build_communities.py` script
- [ ] Implement sparse graph construction from FAISS index
- [ ] Integrate community detection algorithm (Louvain or Leiden)
- [ ] Implement community ranking function
- [ ] Create community data JSON export
- [ ] Add summary/statistics output
- [ ] Testing on synthetic datasets
- [ ] Documentation and usage examples

---

## Success Criteria

Phase 2 is complete when:

- ✅ Sparse graph is constructed from FAISS similarity scores
- ✅ Communities are detected and non-overlapping
- ✅ Communities are ranked by priority score
- ✅ Community data JSON exports correctly
- ✅ NetworkX graph is saved for Phase 3 reuse
- ✅ Script produces clear summary statistics

---

## Open Questions
- **Community Algorithm:** default Louvain; allow Leiden when igraph available.
- **Threshold Tuning:** keep manual for now; thresholds live in `scripts_config.yaml` and are user-adjustable.
- **Graph Persistence:** use NetworkX pickle (`graph_file`) plus JSON export for Phase 3; revisit if size grows.

---

## Related Documents

- [Phase 1 Plumbing & Safety](./phase1-plumbing-safety.md)
- [Semi-Auto Curation Overview](./semi-auto-curation.md)
- [Curation Sample Walkthrough](../curation_sample.md)
