#!/usr/bin/env python3
"""Build candidate pairs for skill curation using embeddings (outline-based rerank optional)."""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

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
from src.common.storage.schema import CategorySkill, Embedding, FAISSMetadata  # noqa: E402


@dataclass(frozen=True)
class Item:
    id: str
    category: str
    name: str
    description: str
    content: str
    metadata_json: Optional[str]
    sync_status: int


def parse_args() -> argparse.Namespace:
    try:
        cfg, _ = load_scripts_config()
        cur = cfg.get("curation", {})
        th = cur.get("thresholds", {})
        default_db = cur.get("curation_db_path", "data/curation/chl_curation.db")
        default_top_k = cur.get("top_k_neighbors", 50)
        default_min_sim = th.get("edge_keep", cur.get("min_similarity_threshold", 0.72))
        default_per_cat = cur.get("per_category", True)
        default_rerank_cache = cur.get("rerank_cache_dir", "data/curation/rerank_cache")
    except Exception:
        default_db = "data/curation/chl_curation.db"
        default_top_k = 50
        default_min_sim = 0.72
        default_per_cat = True
        default_rerank_cache = "data/curation/rerank_cache"

    parser = argparse.ArgumentParser(description="Build candidate pairs for skill curation")
    parser.add_argument("--db-path", default=default_db, help="Path to curation SQLite DB")
    parser.add_argument("--neighbors-file", default="data/curation/skill_neighbors.jsonl", help="Neighbor cache file")
    parser.add_argument("--output", default="data/curation/skill_candidates.jsonl", help="Output JSONL file")
    parser.add_argument("--refresh-neighbors", action="store_true", help="Rebuild neighbors even if cache exists")
    parser.add_argument("--top-k", type=int, default=default_top_k, help="Top-K neighbors to pull from FAISS")
    parser.add_argument("--min-threshold", type=float, default=default_min_sim, help="Keep candidate if blended score >= threshold")
    parser.add_argument("--per-category", action="store_true", default=default_per_cat, help="Restrict neighbors within category")
    parser.add_argument("--allow-cross-category", action="store_true", help="Allow cross-category edges for all")
    parser.add_argument("--cross-category-threshold", type=float, default=0.70, help="Category confidence threshold for fallback")
    parser.add_argument("--cross-category-cap", type=float, default=0.10, help="Max fraction of skills to apply fallback")
    parser.add_argument("--with-rerank", action="store_true", help="Enable rerank blend")
    parser.add_argument("--no-rerank", action="store_true", help="Disable rerank even if config enables it")
    parser.add_argument("--rerank-cache-dir", default=default_rerank_cache, help="Directory for rerank cache")
    parser.add_argument("--clear-cache", action="store_true", help="Clear rerank cache before running")
    parser.add_argument("--include-synced", action="store_true", help="Include SYNCED items (default: pending only)")
    return parser.parse_args()


def parse_metadata(raw: Optional[str]) -> dict:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def outline_for(item: Item) -> str:
    meta = parse_metadata(item.metadata_json)
    outline = meta.get("chl.outline")
    if isinstance(outline, str) and outline.strip():
        return outline.strip()
    return f"{item.name}\n\n{item.description}\n\n{item.content}".strip()


def confidence_for(item: Item) -> Optional[float]:
    meta = parse_metadata(item.metadata_json)
    val = meta.get("chl.category_confidence")
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


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
    skills = session.query(CategorySkill).filter(CategorySkill.sync_status.in_(status_filter)).all()
    emb_repo = EmbeddingRepository(session)
    embeddings = session.query(Embedding).filter(
        Embedding.model_version == model_version, Embedding.entity_type == "skill"
    ).all()
    vectors = {emb.entity_id: emb_repo.to_numpy(emb) for emb in embeddings}

    items: List[Item] = []
    missing = 0
    for skill in skills:
        vec = vectors.get(skill.id)
        if vec is None:
            missing += 1
            continue
        items.append(
            Item(
                id=skill.id,
                category=skill.category_code,
                name=skill.name or "",
                description=skill.description or "",
                content=skill.content or "",
                metadata_json=skill.metadata_json,
                sync_status=skill.sync_status,
            )
        )
    if missing:
        print(f"⚠️  Skipped {missing} skills without embeddings for model {model_version}")
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
    allow_cross_category: bool,
    include_ids: Optional[set[str]] = None,
    cross_category_only: bool = False,
) -> List[dict]:
    id_to_item = {it.id: it for it in items}
    vectors_array = {k: v.astype(np.float32) for k, v in vectors.items()}
    search_k = max(top_k * 2, top_k + 10)
    neighbors: List[dict] = []

    filtered_items = items
    if include_ids is not None:
        filtered_items = [it for it in items if it.id in include_ids]

    iterator = filtered_items if tqdm is None else tqdm(filtered_items, desc="Querying FAISS", unit="skill")
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
            if not meta or meta.deleted or meta.entity_type != "skill":
                continue
            anchor_id = meta.entity_id
            if anchor_id == item.id:
                continue
            anchor_item = id_to_item.get(anchor_id)
            if anchor_item is None:
                continue
            if per_category and not allow_cross_category and anchor_item.category != item.category:
                continue
            if cross_category_only and anchor_item.category == item.category:
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


def main() -> int:
    args = parse_args()
    db_path = Path(args.db_path)
    if not db_path.exists():
        print(f"❌ Database not found: {db_path}")
        return 1

    config = get_config()
    if not bool(getattr(config, "skills_enabled", True)):
        print("Skills are disabled; skipping skill candidate grouping.")
        return 0
    cfg_dict, _ = load_scripts_config()
    cur_cfg = cfg_dict.get("curation", {})
    blend_cfg = cur_cfg.get("blend_weights", {"embed": 0.7, "rerank": 0.3})
    w_embed = float(blend_cfg.get("embed", 0.7))
    w_rerank = float(blend_cfg.get("rerank", 0.3))

    engine = create_engine(f"sqlite:///{db_path}")
    Session = sessionmaker(bind=engine)
    session = Session()

    try:
        items, vectors = load_items_and_embeddings(session, config.embedding_model, args.include_synced)
        if not items:
            print("No skills with embeddings found for the selected scope.")
            return 0

        meta_by_internal = load_faiss_meta(session)
        if not meta_by_internal:
            print("❌ FAISS metadata is empty. Build the curation index first.")
            return 1

        dimension = len(next(iter(vectors.values())))
        faiss_dir = Path(cur_cfg.get("faiss_index_dir", "data/curation/faiss_index"))
        faiss_manager = FAISSIndexManager(
            index_dir=str(faiss_dir),
            model_name=config.embedding_model,
            dimension=dimension,
            session=session,
        )

        # Cross-category fallback selection
        low_conf = []
        for item in items:
            conf = confidence_for(item)
            if conf is not None and conf < args.cross_category_threshold:
                low_conf.append((conf, item.id))
        low_conf.sort(key=lambda x: x[0])
        cap = max(0, int(len(items) * args.cross_category_cap))
        fallback_ids = {item_id for _, item_id in low_conf[:cap]} if cap else set()

        # Build neighbors
        neighbors = build_neighbors(
            items=items,
            vectors=vectors,
            meta_by_internal=meta_by_internal,
            faiss_manager=faiss_manager,
            top_k=args.top_k,
            per_category=args.per_category,
            allow_cross_category=args.allow_cross_category,
        )
        # Cross-category fallback pass (top-K, cross-category only)
        if fallback_ids:
            cross_neighbors = build_neighbors(
                items=items,
                vectors=vectors,
                meta_by_internal=meta_by_internal,
                faiss_manager=faiss_manager,
                top_k=min(args.top_k, 10),
                per_category=False,
                allow_cross_category=True,
                include_ids=fallback_ids,
                cross_category_only=True,
            )
            for rec in cross_neighbors:
                rec["cross_category"] = True
            neighbors.extend(cross_neighbors)

        neighbors_meta = {
            "model_version": config.embedding_model,
            "top_k": args.top_k,
            "min_threshold": args.min_threshold,
            "per_category": args.per_category,
        }
        save_neighbors(Path(args.neighbors_file), neighbors, neighbors_meta)

        # Rerank + filter
        rerank_cache_dir = Path(args.rerank_cache_dir)
        if args.clear_cache and rerank_cache_dir.exists():
            cache_file = rerank_cache_dir / "rerank_cache_skills.jsonl"
            if cache_file.exists():
                cache_file.unlink()
        rerank_cache: Dict[str, float] = {}
        cache_file = rerank_cache_dir / "rerank_cache_skills.jsonl"
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
        if use_rerank:
            w_embed, w_rerank = 0.0, 1.0

        id_to_item = {it.id: it for it in items}
        candidates = []
        stats = {"rerank_calls": 0, "rerank_cache_hits": 0}

        def cache_key(a: str, b: str) -> str:
            return "skill||" + "||".join(sorted([a, b]))

        iterator = neighbors if tqdm is None else tqdm(neighbors, desc="Reranking", unit="pair")
        for rec in iterator:
            src = rec["src"]
            dst = rec["dst"]
            embed_score = float(rec["embed_score"])
            src_item = id_to_item.get(src)
            dst_item = id_to_item.get(dst)
            if not src_item or not dst_item:
                continue

            rerank_score = None
            if reranker:
                key = cache_key(src, dst)
                if key in rerank_cache:
                    rerank_score = rerank_cache[key]
                    stats["rerank_cache_hits"] += 1
                else:
                    try:
                        stats["rerank_calls"] += 1
                        src_text = outline_for(src_item)
                        dst_text = outline_for(dst_item)
                        score_ab = reranker.rerank(
                            query={"search": src_text, "task": "Find skills with the same purpose"},
                            documents=[dst_text],
                        )[0]
                        score_ba = reranker.rerank(
                            query={"search": dst_text, "task": "Find skills with the same purpose"},
                            documents=[src_text],
                        )[0]
                        rerank_score = max(score_ab, score_ba)
                        rerank_cache[key] = rerank_score
                        rerank_cache_dir.mkdir(parents=True, exist_ok=True)
                        with cache_file.open("a", encoding="utf-8") as fh:
                            fh.write(json.dumps({"key": key, "score": rerank_score, "model": config.reranker_model}) + "\n")
                    except Exception as exc:
                        print(f"⚠️  Rerank failed for ({src}, {dst}): {exc}")
                        rerank_score = None

            weight = blended_score(embed_score, rerank_score, w_embed, w_rerank)
            if weight < args.min_threshold:
                continue
            candidates.append(
                {
                    "src": src,
                    "dst": dst,
                    "src_category": rec.get("src_category"),
                    "dst_category": rec.get("dst_category"),
                    "embed_score": embed_score,
                    "rerank_score": rerank_score,
                    "weight": weight,
                    "cross_category": bool(rec.get("cross_category")),
                }
            )

        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8") as fh:
            for rec in candidates:
                fh.write(json.dumps(rec) + "\n")

        print(f"✓ Neighbor cache: {args.neighbors_file} ({len(neighbors)} records)")
        print(f"✓ Candidates: {output_path} ({len(candidates)} records)")
        if reranker:
            print(
                f"Rerank: cache_hits={stats.get('rerank_cache_hits',0)} "
                f"calls={stats.get('rerank_calls',0)}"
            )
        print(f"Blend weights: embed={w_embed}, rerank={w_rerank} | Rerank enabled: {bool(reranker)}")

    finally:
        session.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
