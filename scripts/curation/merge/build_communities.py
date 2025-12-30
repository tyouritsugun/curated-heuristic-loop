#!/usr/bin/env python3
"""
Build sparse similarity graph (per-category) and detect communities.

Outputs:
  - data/curation/communities.json        (community list + metadata)
  - data/curation/similarity_graph.pkl    (NetworkX graph with weights)
  - data/curation/neighbors.jsonl         (cached top-K neighbors, reused by default)

Defaults come from scripts/scripts_config.yaml; CLI flags can override.
"""

from __future__ import annotations

import argparse
import json
import os
import pickle
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import networkx as nx
import numpy as np
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

try:
    from tqdm import tqdm
except Exception:  # pragma: no cover - optional
    tqdm = None

# Add project root to sys.path
REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from scripts._config_loader import load_scripts_config  # noqa: E402
from src.api.gpu.faiss_manager import FAISSIndexManager  # noqa: E402
from src.api.gpu.reranker_client import RerankerClient  # noqa: E402
from src.common.config.config import get_config  # noqa: E402
from src.common.storage.repository import EmbeddingRepository  # noqa: E402
from src.common.storage.schema import Embedding, Experience, FAISSMetadata  # noqa: E402


@dataclass(frozen=True)
class Item:
    id: str
    category: str
    title: str
    playbook: str
    context: Optional[str]
    sync_status: int


def parse_args() -> argparse.Namespace:
    try:
        cfg, _ = load_scripts_config()
        cur = cfg.get("curation", {})
        th = cur.get("thresholds", {})
        default_db = cur.get("curation_db_path", "data/curation/chl_curation.db")
        default_output = cur.get("community_data_file", "data/curation/communities.json")
        default_graph = cur.get("graph_file", "data/curation/similarity_graph.pkl")
        default_min_sim = th.get("edge_keep", cur.get("min_similarity_threshold", 0.72))
        default_top_k = cur.get("top_k_neighbors", 50)
        default_per_cat = cur.get("per_category", True)
        default_algorithm = cur.get("algorithm", "louvain")
        default_rerank_cache = cur.get("rerank_cache_dir", "data/curation/rerank_cache")
        default_min_comm = cur.get("min_community_size", 2)
        default_max_comm = cur.get("max_community_size", 50)
        # Default: rerank off (embed-only) unless explicitly requested
        default_use_rerank = False
    except Exception:
        default_db = "data/curation/chl_curation.db"
        default_output = "data/curation/communities.json"
        default_graph = "data/curation/similarity_graph.pkl"
        default_min_sim = 0.72
        default_top_k = 50
        default_per_cat = True
        default_algorithm = "louvain"
        default_rerank_cache = "data/curation/rerank_cache"
        default_min_comm = 2
        default_max_comm = 50
        default_use_rerank = False

    parser = argparse.ArgumentParser(description="Build sparse similarity graph and detect communities")
    parser.add_argument("--db-path", default=default_db, help="Path to curation SQLite DB")
    parser.add_argument("--output", default=default_output, help="Output JSON file for community data")
    parser.add_argument("--graph-file", default=default_graph, help="Output pickle file for NetworkX graph")
    parser.add_argument("--neighbors-file", default="data/curation/neighbors.jsonl", help="Neighbor cache file path (default used if valid)")
    parser.add_argument("--refresh-neighbors", action="store_true", help="Rebuild neighbors even if cache exists")
    parser.add_argument("--min-threshold", type=float, default=default_min_sim, help="Edge keep threshold (blended)")
    parser.add_argument("--top-k", type=int, default=default_top_k, help="Top-K neighbors to pull from FAISS")
    parser.add_argument("--algorithm", choices=["louvain", "leiden"], default=default_algorithm, help="Community detection algorithm")
    parser.add_argument("--summary-only", action="store_true", help="Print summary without writing files")
    parser.add_argument("--include-synced", action="store_true", help="Include SYNCED items (default: pending only)")
    parser.add_argument("--with-rerank", action="store_true", default=default_use_rerank, help="Enable rerank blend")
    parser.add_argument("--no-rerank", action="store_true", help="Disable rerank even if config enables it")
    parser.add_argument("--rerank-cache-dir", default=default_rerank_cache, help="Directory for rerank cache")
    parser.add_argument("--clear-cache", action="store_true", help="Clear rerank cache before running")
    parser.add_argument("--per-category", action="store_true", default=default_per_cat, help="Force per-category graphs (default true)")
    parser.add_argument("--allow-cross-category", action="store_true", help="Allow cross-category edges (overrides per-category)")
    parser.add_argument("--min-community-size", type=int, default=default_min_comm, help="Minimum community size to keep")
    parser.add_argument("--max-community-size", type=int, default=default_max_comm, help="Flag communities larger than this size")
    return parser.parse_args()


def text_for_item(item: Item) -> str:
    parts = [item.title or ""]
    if item.playbook:
        parts.append(item.playbook)
    if item.context:
        parts.append(str(item.context))
    return "\n".join(parts).strip()


def get_reranker(config, use_rerank: bool) -> Optional[RerankerClient]:
    if not use_rerank:
        return None
    try:
        repo, quant = config.reranker_model.split(":") if ":" in config.reranker_model else (config.reranker_model, "fp32")
        return RerankerClient(model_repo=repo, quantization=quant)
    except Exception as exc:
        print(f"⚠️  Reranker unavailable ({exc}); falling back to embed-only scores.")
        return None


def load_items_and_embeddings(session, model_version: str, include_synced: bool) -> Tuple[List[Item], Dict[str, np.ndarray]]:
    status_filter = [0] if not include_synced else [0, 1]
    experiences = session.query(Experience).filter(Experience.sync_status.in_(status_filter)).all()
    emb_repo = EmbeddingRepository(session)
    embeddings = session.query(Embedding).filter(
        Embedding.model_version == model_version, Embedding.entity_type == "experience"
    ).all()
    vectors = {emb.entity_id: emb_repo.to_numpy(emb) for emb in embeddings}

    items: List[Item] = []
    missing = 0
    for exp in experiences:
        vec = vectors.get(exp.id)
        if vec is None:
            missing += 1
            continue
        items.append(
            Item(
                id=exp.id,
                category=exp.category_code,
                title=exp.title or "",
                playbook=exp.playbook or "",
                context=exp.context,
                sync_status=exp.sync_status,
            )
        )
    if missing:
        print(f"⚠️  Skipped {missing} experiences without embeddings for model {model_version}")
    return items, vectors


def load_faiss_meta(session) -> Dict[int, FAISSMetadata]:
    rows = session.query(FAISSMetadata).filter(FAISSMetadata.deleted == False).all()  # noqa: E712
    return {row.internal_id: row for row in rows}


def blended_score(embed_score: float, rerank_score: Optional[float], w_embed: float, w_rerank: float) -> float:
    if rerank_score is None:
        return embed_score
    return (w_embed * embed_score) + (w_rerank * rerank_score)


def build_neighbors(
    items: List[Item],
    vectors: Dict[str, np.ndarray],
    meta_by_internal: Dict[int, FAISSMetadata],
    faiss_manager: FAISSIndexManager,
    top_k: int,
    per_category: bool,
) -> List[dict]:
    id_to_item = {it.id: it for it in items}
    vectors_array = {k: v.astype(np.float32) for k, v in vectors.items()}
    search_k = max(top_k * 2, top_k + 10)
    neighbors: List[dict] = []

    iterator = items if tqdm is None else tqdm(items, desc="Querying FAISS", unit="item")
    for item in iterator:
        vec = vectors_array[item.id]
        distances, indices = faiss_manager.search(vec.reshape(1, -1), search_k)
        distances = distances.reshape(1, -1) if len(distances.shape) == 1 else distances
        indices = indices.reshape(1, -1) if len(indices.shape) == 1 else indices

        candidates: List[dict] = []
        for dist, idx in zip(distances[0], indices[0]):
            internal_id = int(idx)
            if internal_id == -1:
                continue
            meta = meta_by_internal.get(internal_id)
            if not meta or meta.deleted or meta.entity_type != "experience":
                continue
            anchor_id = meta.entity_id
            if anchor_id == item.id:
                continue
            anchor_item = id_to_item.get(anchor_id)
            if anchor_item is None:
                continue
            if per_category and anchor_item.category != item.category:
                continue
            candidates.append(
                {
                    "src": item.id,
                    "dst": anchor_id,
                    "embed_score": float(dist),
                    "src_category": item.category,
                    "dst_category": anchor_item.category,
                }
            )
        candidates.sort(key=lambda x: x["embed_score"], reverse=True)
        neighbors.extend(candidates[:top_k])
    return neighbors


def save_neighbors(file_path: Path, records: List[dict], meta: dict) -> None:
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with file_path.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps({"type": "meta", **meta}) + "\n")
        for rec in records:
            fh.write(json.dumps(rec) + "\n")


def load_neighbors(file_path: Path, expected_model: str, expected_top_k: int, expected_threshold: float, index_mtime: float) -> Optional[List[dict]]:
    if not file_path.exists():
        return None
    try:
        with file_path.open("r", encoding="utf-8") as fh:
            first = fh.readline()
            meta = json.loads(first)
            if meta.get("type") != "meta":
                return None
            if meta.get("model_version") != expected_model:
                return None
            if int(meta.get("top_k", -1)) != int(expected_top_k):
                return None
            if float(meta.get("min_threshold", -1)) != float(expected_threshold):
                return None
            if float(meta.get("index_mtime", -1)) != float(index_mtime):
                return None
            records = [json.loads(line) for line in fh]
        return records
    except Exception:
        return None


def build_graph(
    items: List[Item],
    neighbor_records: List[dict],
    threshold: float,
    per_category: bool,
    reranker: Optional[RerankerClient],
    rerank_cache: Dict[str, float],
    rerank_cache_dir: Path,
    rerank_model: str,
    blend_weights: Tuple[float, float],
) -> Tuple[nx.Graph, Dict[str, int]]:
    id_to_item = {it.id: it for it in items}
    G = nx.Graph()

    for item in items:
        G.add_node(item.id, category=item.category, sync_status=item.sync_status)

    w_embed, w_rerank = blend_weights
    stats = {"rerank_cache_hits": 0, "rerank_calls": 0}

    def cache_key(a: str, b: str) -> str:
        return "||".join(sorted([a, b]))

    iter_edges = neighbor_records if tqdm is None else tqdm(neighbor_records, desc="Building graph", unit="edge")
    for rec in iter_edges:
        src = rec["src"]
        dst = rec["dst"]
        embed_score = float(rec["embed_score"])
        item = id_to_item.get(src)
        anchor_item = id_to_item.get(dst)
        if not item or not anchor_item:
            continue
        if per_category and item.category != anchor_item.category:
            continue

        rerank_score = None
        if reranker:
            key = cache_key(item.id, anchor_item.id)
            if key in rerank_cache:
                rerank_score = rerank_cache[key]
                stats["rerank_cache_hits"] += 1
            else:
                try:
                    stats["rerank_calls"] += 1
                    a_text = text_for_item(item)
                    b_text = text_for_item(anchor_item)
                    score_ab = reranker.rerank(
                        query={"search": item.title, "task": item.playbook},
                        documents=[b_text],
                    )[0]
                    score_ba = reranker.rerank(
                        query={"search": anchor_item.title, "task": anchor_item.playbook},
                        documents=[a_text],
                    )[0]
                    rerank_score = max(score_ab, score_ba)
                    rerank_cache[key] = rerank_score
                    rerank_cache_dir.mkdir(parents=True, exist_ok=True)
                    with (rerank_cache_dir / "rerank_cache.jsonl").open("a", encoding="utf-8") as fh:
                        fh.write(json.dumps({"key": key, "score": rerank_score, "model": rerank_model}) + "\n")
                except Exception as exc:
                    print(f"⚠️  Rerank failed for ({item.id}, {anchor_item.id}): {exc}")
                    rerank_score = None

        weight = blended_score(embed_score, rerank_score, w_embed, w_rerank)
        if weight < threshold:
            continue

        edge_key = tuple(sorted((item.id, anchor_item.id)))
        existing = G.get_edge_data(*edge_key, default=None)
        if existing is None or weight > existing.get("weight", 0):
            G.add_edge(
                edge_key[0],
                edge_key[1],
                weight=weight,
                embed_score=embed_score,
                rerank_score=rerank_score,
            )

    isolated = list(nx.isolates(G))
    if isolated:
        G.remove_nodes_from(isolated)
    return G, stats


def detect_communities(G: nx.Graph, algorithm: str, per_category: bool) -> Dict[str, List[List[str]]]:
    communities_by_cat: Dict[str, List[List[str]]] = {}
    categories = {d["category"] for _, d in G.nodes(data=True)}

    for category in categories:
        sub_nodes = [n for n, d in G.nodes(data=True) if d.get("category") == category] if per_category else list(G.nodes())
        subgraph = G.subgraph(sub_nodes).copy()
        if subgraph.number_of_nodes() == 0:
            continue

        if algorithm == "leiden":
            try:
                import igraph as ig  # type: ignore
                import leidenalg  # type: ignore

                mapping = {node: i for i, node in enumerate(subgraph.nodes())}
                edges = [(mapping[u], mapping[v], data.get("weight", 1.0)) for u, v, data in subgraph.edges(data=True)]
                g = ig.Graph()
                g.add_vertices(len(mapping))
                if edges:
                    g.add_edges([(u, v) for u, v, _ in edges])
                    g.es["weight"] = [w for _, _, w in edges]
                partition = leidenalg.find_partition(g, leidenalg.ModularityVertexPartition, weights="weight")
                inv_mapping = {i: node for node, i in mapping.items()}
                comms = [[inv_mapping[idx] for idx in comm] for comm in partition]
                communities_by_cat[category] = comms
                continue
            except Exception as exc:
                print(f"⚠️  Leiden unavailable ({exc}); falling back to Louvain.")
                algorithm = "louvain"

        try:
            import community as community_louvain  # type: ignore

            partition = community_louvain.best_partition(subgraph, weight="weight")
            comm_map: Dict[int, List[str]] = {}
            for node, cid in partition.items():
                comm_map.setdefault(cid, []).append(node)
            communities_by_cat[category] = list(comm_map.values())
        except Exception as exc:
            raise RuntimeError(f"Louvain community detection failed: {exc}") from exc

        if not per_category:
            break

    return communities_by_cat


def score_community(G: nx.Graph, nodes: List[str]) -> Tuple[float, float, int]:
    sub = G.subgraph(nodes)
    size = sub.number_of_nodes()
    if size <= 1:
        return 0.0, 0.0, size
    weights = [data.get("weight", 0.0) for _, _, data in sub.edges(data=True)]
    avg_sim = float(np.mean(weights)) if weights else 0.0
    density = nx.density(sub) if size > 1 else 0.0
    return avg_sim, density, size


def size_score(size: int) -> float:
    if size < 3:
        return size / 3.0
    if size <= 10:
        return 1.0
    return max(0.0, min(1.0, 10.0 / float(size)))


def priority_score(avg_sim: float, density: float, size: int) -> float:
    return (0.6 * avg_sim) + (0.3 * density) + (0.1 * size_score(size))


def export_communities(
    G: nx.Graph,
    communities_by_cat: Dict[str, List[List[str]]],
    output_path: Path,
    metadata: Dict[str, object],
    min_size: int,
    max_size: int,
) -> Dict[str, object]:
    communities = []
    counter = 1
    skipped_small = 0
    oversized_count = 0
    for category, comms in communities_by_cat.items():
        for nodes in comms:
            if len(nodes) < min_size:
                skipped_small += 1
                continue
            avg_sim, density, size = score_community(G, nodes)
            oversized = size > max_size
            if oversized:
                oversized_count += 1
            pid = f"COMM-{counter:03d}"
            counter += 1
            sub = G.subgraph(nodes)
            edges_payload = [[u, v, float(d.get("weight", 0.0))] for u, v, d in sub.edges(data=True)]
            communities.append(
                {
                    "id": pid,
                    "category": category,
                    "members": list(nodes),
                    "avg_similarity": round(avg_sim, 4),
                    "density": round(density, 4),
                    "size": size,
                    "priority_score": round(priority_score(avg_sim, density, size), 4),
                    "oversized": oversized,
                    "edges": edges_payload,
                }
            )

    metadata["skipped_small_communities"] = skipped_small
    metadata["oversized_communities"] = oversized_count
    metadata["total_communities"] = len(communities)
    payload = {"communities": communities, "metadata": metadata}
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
    return payload


def main() -> None:
    args = parse_args()
    db_path = Path(args.db_path)
    if not db_path.exists():
        print(f"❌ Database not found: {db_path}")
        sys.exit(1)

    config = get_config()
    cfg_dict, _ = load_scripts_config()
    cur_cfg = cfg_dict.get("curation", {})
    blend_cfg = cur_cfg.get("blend_weights", {"embed": 0.7, "rerank": 0.3})
    w_embed = float(blend_cfg.get("embed", 0.7))
    w_rerank = float(blend_cfg.get("rerank", 0.3))

    # DB session
    engine = create_engine(f"sqlite:///{db_path}")
    Session = sessionmaker(bind=engine)
    session = Session()

    try:
        items, vectors = load_items_and_embeddings(session, config.embedding_model, args.include_synced)
        if not items:
            print("No experiences with embeddings found for the selected scope.")
            sys.exit(0)

        per_category = args.per_category and not args.allow_cross_category

        # FAISS and metadata
        index_dir = Path(cur_cfg.get("faiss_index_dir", db_path.parent / "faiss_index"))
        if not index_dir.exists():
            print(f"❌ FAISS index directory not found: {index_dir}")
            print("   Run scripts/curation/merge/build_curation_index.py first.")
            sys.exit(1)

        sample_vec = next(iter(vectors.values()))
        faiss_manager = FAISSIndexManager(
            index_dir=str(index_dir),
            model_name=config.embedding_model,
            dimension=sample_vec.shape[0],
            session=session,
        )
        index_mtime = os.path.getmtime(faiss_manager.index_path)
        meta_by_internal = load_faiss_meta(session)

        # Reranker + cache
        rerank_cache_dir = Path(args.rerank_cache_dir)
        if args.clear_cache and rerank_cache_dir.exists():
            cache_file = rerank_cache_dir / "rerank_cache.jsonl"
            if cache_file.exists():
                cache_file.unlink()
        rerank_cache = {}
        cache_file = rerank_cache_dir / "rerank_cache.jsonl"
        if cache_file.exists():
            try:
                with cache_file.open("r", encoding="utf-8") as fh:
                    for line in fh:
                        rec = json.loads(line)
                        if rec.get("model") == config.reranker_model:
                            rerank_cache[rec["key"]] = float(rec["score"])
            except Exception:
                rerank_cache = {}
        use_rerank = args.with_rerank and not args.no_rerank
        reranker = get_reranker(config, use_rerank)
        # If rerank is enabled, switch to rerank-only scoring unless user overrides weights in code
        if use_rerank:
            w_embed, w_rerank = 0.0, 1.0

        # Neighbor cache: default ON
        neighbors_file = Path(args.neighbors_file)
        neighbors: Optional[List[dict]] = None
        cache_hit = False
        if not args.refresh_neighbors:
            neighbors = load_neighbors(
                neighbors_file,
                expected_model=config.embedding_model,
                expected_top_k=args.top_k,
                expected_threshold=args.min_threshold,
                index_mtime=index_mtime,
            )
            cache_hit = neighbors is not None
        if neighbors is None:
            neighbors = build_neighbors(
                items=items,
                vectors=vectors,
                meta_by_internal=meta_by_internal,
                faiss_manager=faiss_manager,
                top_k=args.top_k,
                per_category=per_category,
            )
            save_neighbors(
                neighbors_file,
                neighbors,
                {
                    "type": "meta",
                    "model_version": config.embedding_model,
                    "top_k": args.top_k,
                    "min_threshold": args.min_threshold,
                    "index_mtime": index_mtime,
                },
            )

        print(f"Items: {len(items)} | Per-category: {per_category} | Algorithm: {args.algorithm}")
        print(f"Top-K: {args.top_k} | Threshold: {args.min_threshold}")
        print(f"Blend weights: embed={w_embed}, rerank={w_rerank} | Rerank enabled: {bool(reranker)}")
        print(f"Neighbors: {len(neighbors)} (cache {'hit' if cache_hit else 'miss'})")

        G, stats = build_graph(
            items=items,
            neighbor_records=neighbors,
            threshold=args.min_threshold,
            per_category=per_category,
            reranker=reranker,
            rerank_cache=rerank_cache,
            rerank_cache_dir=rerank_cache_dir,
            rerank_model=config.reranker_model,
            blend_weights=(w_embed, w_rerank),
        )

        if G.number_of_nodes() == 0:
            print("No edges met the threshold; graph is empty.")
            sys.exit(0)

        communities_by_cat = detect_communities(G, algorithm=args.algorithm, per_category=per_category)
        total_comms = sum(len(v) for v in communities_by_cat.values())
        total_kept = sum(len([nodes for nodes in v if len(nodes) >= args.min_community_size]) for v in communities_by_cat.values())

        print(f"✓ Graph built: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")
        print(f"✓ Communities detected: {total_comms} (kept: {total_kept})")
        print(
            f"Rerank: cache_hits={stats.get('rerank_cache_hits',0)} "
            f"calls={stats.get('rerank_calls',0)} "
            f"(pass --with-rerank for rerank-only scoring)"
        )

        metadata = {
            "total_items": G.number_of_nodes(),
            "total_communities": total_kept,
            "graph_edges": G.number_of_edges(),
            "min_threshold": args.min_threshold,
            "top_k": args.top_k,
            "algorithm": args.algorithm,
            "per_category": per_category,
            "blend_weights": {"embed": w_embed, "rerank": w_rerank},
            "rerank_enabled": bool(reranker),
            "min_community_size": args.min_community_size,
            "max_community_size": args.max_community_size,
            "edge_thresholds": cur_cfg.get("thresholds", {}),
        }

        if args.summary_only:
            print(json.dumps(metadata, indent=2))
            return

        Path(args.graph_file).parent.mkdir(parents=True, exist_ok=True)
        with open(args.graph_file, "wb") as fh:
            pickle.dump(G, fh, protocol=pickle.HIGHEST_PROTOCOL)

        payload = export_communities(
            G=G,
            communities_by_cat=communities_by_cat,
            output_path=Path(args.output),
            metadata=metadata,
            min_size=args.min_community_size,
            max_size=args.max_community_size,
        )

        print(f"✓ Community data written to {args.output}")
        print(f"✓ Graph pickle written to {args.graph_file}")
        top3 = sorted(payload["communities"], key=lambda c: c["priority_score"], reverse=True)[:3]
        print("Top priority communities (first 3):")
        for comm in top3:
            print(
                f"  {comm['id']} | cat={comm['category']} | size={comm['size']} "
                f"| avg_sim={comm['avg_similarity']} | priority={comm['priority_score']}"
            )
    finally:
        session.close()


if __name__ == "__main__":
    main()
