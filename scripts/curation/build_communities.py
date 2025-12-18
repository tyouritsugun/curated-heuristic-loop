#!/usr/bin/env python3
"""
Phase 2: Build sparse similarity graph (per-category) and detect communities.

Outputs:
  - data/curation/communities.json   (community list + metadata)
  - data/curation/similarity_graph.pkl (NetworkX graph with weights)

Defaults are read from scripts/scripts_config.yaml; CLI flags can override.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import networkx as nx
import numpy as np
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Add project root to sys.path for package imports
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT.parent))

from scripts._config_loader import load_scripts_config  # noqa: E402
from src.api.gpu.faiss_manager import FAISSIndexManager  # noqa: E402
from src.api.gpu.reranker_client import (  # noqa: E402
    RerankerClient,
    RerankerClientError,
)
from src.common.config.config import get_config  # noqa: E402
from src.common.storage.repository import EmbeddingRepository  # noqa: E402
from src.common.storage.schema import Embedding, Experience, FAISSMetadata  # noqa: E402


@dataclass(frozen=True)
class Item:
    """Lightweight experience record for graph building."""

    id: str
    category: str
    title: str
    playbook: str
    context: Optional[str]
    sync_status: int


def parse_args() -> argparse.Namespace:
    try:
        cfg, _ = load_scripts_config()
        curation = cfg.get("curation", {})
        thresholds = curation.get("thresholds", {})
        default_db = curation.get("curation_db_path", "data/curation/chl_curation.db")
        default_output = curation.get("community_data_file", "data/curation/communities.json")
        default_graph = curation.get("graph_file", "data/curation/similarity_graph.pkl")
        default_min_sim = thresholds.get("edge_keep", curation.get("min_similarity_threshold", 0.72))
        default_top_k = curation.get("top_k_neighbors", 50)
        default_per_cat = curation.get("per_category", True)
        default_algorithm = curation.get("algorithm", "louvain")
        default_use_rerank = curation.get("use_rerank", True)
        default_rerank_cache = curation.get("rerank_cache_dir", "data/curation/rerank_cache")
    except Exception:
        # Safe fallbacks if config load fails
        default_db = "data/curation/chl_curation.db"
        default_output = "data/curation/communities.json"
        default_graph = "data/curation/similarity_graph.pkl"
        default_min_sim = 0.72
        default_top_k = 50
        default_per_cat = True
        default_algorithm = "louvain"
        default_use_rerank = True
        default_rerank_cache = "data/curation/rerank_cache"

    parser = argparse.ArgumentParser(description="Build sparse similarity graph and detect communities (Phase 2)")
    parser.add_argument("--db-path", default=default_db, help="Path to curation SQLite DB")
    parser.add_argument("--output", default=default_output, help="Output JSON file for community data")
    parser.add_argument("--graph-file", default=default_graph, help="Output pickle file for NetworkX graph")
    parser.add_argument("--min-threshold", type=float, default=default_min_sim, help="Edge keep threshold (blended)")
    parser.add_argument("--top-k", type=int, default=default_top_k, help="Top-K neighbors to pull from FAISS")
    parser.add_argument("--algorithm", choices=["louvain", "leiden"], default=default_algorithm, help="Community detection algorithm")
    parser.add_argument("--summary-only", action="store_true", help="Print summary without writing files")
    parser.add_argument("--include-synced", action="store_true", help="Include SYNCED items (default: pending only)")
    parser.add_argument("--no-rerank", action="store_true", help="Disable rerank even if configured")
    parser.add_argument("--rerank-cache-dir", default=default_rerank_cache, help="Directory for rerank cache")
    parser.add_argument("--per-category", action="store_true", default=default_per_cat, help="Force per-category graphs (default true)")
    parser.add_argument("--allow-cross-category", action="store_true", help="Override per-category and allow cross-category edges")
    return parser.parse_args()


def load_rerank_cache(cache_dir: Path, model_version: str) -> Dict[str, float]:
    cache_file = cache_dir / "rerank_cache.jsonl"
    scores: Dict[str, float] = {}
    if not cache_file.exists():
        return scores
    try:
        with cache_file.open("r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                    if rec.get("model") == model_version:
                        scores[rec["key"]] = float(rec["score"])
                except Exception:
                    continue
    except Exception:
        # Non-fatal; just start fresh
        return {}
    return scores


def append_rerank_cache(cache_dir: Path, model_version: str, key: str, score: float) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / "rerank_cache.jsonl"
    with cache_file.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"key": key, "score": score, "model": model_version}) + "\n")


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
        rerank_repo, rerank_quant = config.reranker_model.split(":") if ":" in config.reranker_model else (config.reranker_model, "fp32")
        return RerankerClient(model_repo=rerank_repo, quantization=rerank_quant)
    except Exception as exc:
        print(f"⚠️  Reranker unavailable ({exc}); falling back to embed-only scores.")
        return None


def load_items_and_embeddings(session, model_version: str, include_synced: bool) -> Tuple[List[Item], Dict[str, np.ndarray]]:
    """Load experiences and their embeddings for the given model version."""
    status_filter = [0] if not include_synced else [0, 1]
    experiences = (
        session.query(Experience)
        .filter(Experience.sync_status.in_(status_filter))
        .all()
    )

    emb_repo = EmbeddingRepository(session)
    embeddings = (
        session.query(Embedding)
        .filter(Embedding.model_version == model_version, Embedding.entity_type == "experience")
        .all()
    )
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


def build_graph(
    items: List[Item],
    vectors: Dict[str, np.ndarray],
    meta_by_internal: Dict[int, FAISSMetadata],
    faiss_manager: FAISSIndexManager,
    threshold: float,
    top_k: int,
    per_category: bool,
    reranker: Optional[RerankerClient],
    rerank_cache: Dict[str, float],
    rerank_cache_dir: Path,
    rerank_model: str,
    blend_weights: Tuple[float, float],
) -> nx.Graph:
    """Return NetworkX graph with edge weights = blended similarity."""
    id_to_item = {it.id: it for it in items}
    vectors_array = {k: v.astype(np.float32) for k, v in vectors.items()}
    G = nx.Graph()

    # Add nodes up-front
    for item in items:
        G.add_node(item.id, category=item.category, sync_status=item.sync_status)

    w_embed, w_rerank = blend_weights
    search_k = max(top_k * 2, top_k + 10)  # extra to survive category filtering

    def cache_key(a: str, b: str) -> str:
        return "||".join(sorted([a, b]))

    for item in items:
        vec = vectors_array[item.id]
        distances, indices = faiss_manager.search(vec.reshape(1, -1), search_k)
        if len(distances.shape) == 1:
            distances = distances.reshape(1, -1)
            indices = indices.reshape(1, -1)

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

            embed_score = float(dist)
            rerank_score = None
            if reranker:
                key = cache_key(item.id, anchor_id)
                if key in rerank_cache:
                    rerank_score = rerank_cache[key]
                else:
                    try:
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
                        append_rerank_cache(rerank_cache_dir, rerank_model, key, rerank_score)
                    except Exception as exc:
                        print(f"⚠️  Rerank failed for ({item.id}, {anchor_id}): {exc}")
                        rerank_score = None

            weight = blended_score(embed_score, rerank_score, w_embed, w_rerank)
            if weight < threshold:
                continue

            edge_key = tuple(sorted((item.id, anchor_id)))
            existing = G.get_edge_data(*edge_key, default=None)
            if existing is None or weight > existing.get("weight", 0):
                G.add_edge(
                    edge_key[0],
                    edge_key[1],
                    weight=weight,
                    embed_score=embed_score,
                    rerank_score=rerank_score,
                )

    # Drop isolated nodes with no edges above threshold
    isolated = list(nx.isolates(G))
    if isolated:
        G.remove_nodes_from(isolated)
    return G


def detect_communities(G: nx.Graph, algorithm: str, per_category: bool) -> Dict[str, List[List[str]]]:
    """Return mapping category -> list of communities (list of node ids)."""
    communities_by_cat: Dict[str, List[List[str]]] = defaultdict(list)
    categories = {data["category"] for _, data in G.nodes(data=True)}

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
                for comm in partition:
                    communities_by_cat[category].append([inv_mapping[idx] for idx in comm])
            except Exception as exc:
                print(f"⚠️  Leiden unavailable ({exc}); falling back to Louvain.")
                algorithm = "louvain"

        if algorithm == "louvain":
            try:
                import community as community_louvain  # type: ignore

                partition = community_louvain.best_partition(subgraph, weight="weight")
                comm_map: Dict[int, List[str]] = defaultdict(list)
                for node, cid in partition.items():
                    comm_map[cid].append(node)
                communities_by_cat[category].extend(comm_map.values())
            except Exception as exc:
                raise RuntimeError(f"Louvain community detection failed: {exc}") from exc

        if per_category:
            # Only first iteration per category; skip remaining logic
            continue
        else:
            break  # when not per-category, we processed full graph once

    return communities_by_cat


def score_community(G: nx.Graph, nodes: Iterable[str]) -> Tuple[float, float, float]:
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
) -> Dict[str, object]:
    communities = []
    counter = 1
    for category, comms in communities_by_cat.items():
        for nodes in comms:
            avg_sim, density, size = score_community(G, nodes)
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
                    "edges": edges_payload,
                }
            )

    payload = {
        "communities": communities,
        "metadata": metadata,
    }
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
    reranker = None
    use_rerank = (not args.no_rerank)
    rerank_model = config.reranker_model

    # Blend weights
    cfg_dict, _ = load_scripts_config()
    curation_cfg = cfg_dict.get("curation", {})
    blend_cfg = curation_cfg.get("blend_weights", {"embed": 0.7, "rerank": 0.3})
    w_embed = float(blend_cfg.get("embed", 0.7))
    w_rerank = float(blend_cfg.get("rerank", 0.3))

    # Prepare database session
    engine = create_engine(f"sqlite:///{db_path}")
    Session = sessionmaker(bind=engine)
    session = Session()

    try:
        items, vectors = load_items_and_embeddings(
            session=session,
            model_version=config.embedding_model,
            include_synced=args.include_synced,
        )
        if not items:
            print("No experiences with embeddings found for the selected scope.")
            sys.exit(0)

        # Prepare FAISS
        index_dir = Path(curation_cfg.get("faiss_index_dir", db_path.parent / "faiss_index"))
        if not index_dir.exists():
            print(f"❌ FAISS index directory not found: {index_dir}")
            print("   Run scripts/curation/build_curation_index.py first.")
            sys.exit(1)

        sample_vec = next(iter(vectors.values()))
        faiss_manager = FAISSIndexManager(
            index_dir=str(index_dir),
            model_name=config.embedding_model,
            dimension=sample_vec.shape[0],
            session=session,
        )

        meta_by_internal = load_faiss_meta(session)

        # Reranker (optional)
        rerank_cache_dir = Path(args.rerank_cache_dir)
        rerank_cache = load_rerank_cache(rerank_cache_dir, rerank_model)
        reranker = get_reranker(config, use_rerank)

        per_category = args.per_category and not args.allow_cross_category

        print(f"Items: {len(items)} | Per-category: {per_category} | Algorithm: {args.algorithm}")
        print(f"Top-K: {args.top_k} | Threshold: {args.min_threshold}")
        print(f"Blend weights: embed={w_embed}, rerank={w_rerank} | Rerank enabled: {bool(reranker)}")

        G = build_graph(
            items=items,
            vectors=vectors,
            meta_by_internal=meta_by_internal,
            faiss_manager=faiss_manager,
            threshold=args.min_threshold,
            top_k=args.top_k,
            per_category=per_category,
            reranker=reranker,
            rerank_cache=rerank_cache,
            rerank_cache_dir=rerank_cache_dir,
            rerank_model=rerank_model,
            blend_weights=(w_embed, w_rerank),
        )

        if G.number_of_nodes() == 0:
            print("No edges met the threshold; graph is empty.")
            sys.exit(0)

        communities_by_cat = detect_communities(G, algorithm=args.algorithm, per_category=per_category)

        total_comms = sum(len(v) for v in communities_by_cat.values())
        print(f"✓ Graph built: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")
        print(f"✓ Communities detected: {total_comms}")

        metadata = {
            "total_items": G.number_of_nodes(),
            "total_communities": total_comms,
            "graph_edges": G.number_of_edges(),
            "min_threshold": args.min_threshold,
            "top_k": args.top_k,
            "algorithm": args.algorithm,
            "per_category": per_category,
            "blend_weights": {"embed": w_embed, "rerank": w_rerank},
            "rerank_enabled": bool(reranker),
            "edge_thresholds": curation_cfg.get("thresholds", {}),
        }

        if args.summary_only:
            print(json.dumps(metadata, indent=2))
            return

        # Persist graph and communities
        Path(args.graph_file).parent.mkdir(parents=True, exist_ok=True)
        nx.write_gpickle(G, args.graph_file)
        payload = export_communities(
            G=G,
            communities_by_cat=communities_by_cat,
            output_path=Path(args.output),
            metadata=metadata,
        )
        print(f"✓ Community data written to {args.output}")
        print(f"✓ Graph pickle written to {args.graph_file}")
        print(f"Top priority communities (first 3):")
        top3 = sorted(payload["communities"], key=lambda c: c["priority_score"], reverse=True)[:3]
        for comm in top3:
            print(
                f"  {comm['id']} | cat={comm['category']} | size={comm['size']} "
                f"| avg_sim={comm['avg_similarity']} | priority={comm['priority_score']}"
            )
    finally:
        session.close()


if __name__ == "__main__":
    main()
