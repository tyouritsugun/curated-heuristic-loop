#!/usr/bin/env python3
"""
Stage 2: round-loop orchestration with convergence and dry-run support.

This script consumes community outputs and iterates LLM decisions
until convergence or a max-round cap.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import networkx as nx
import numpy as np
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Add project root to sys.path
REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from scripts._config_loader import load_scripts_config
from scripts.curation.agents.autogen_openai_completion_agent import build_llm_config
from scripts.curation.common.prompt_utils import (
    build_prompt_messages,
    fetch_member_records,
    validate_response,
)
from scripts.curation.common.decision_logging import write_evaluation_log
from scripts.curation.common.state_manager import StateManager
from src.common.storage.schema import CurationDecision, Experience


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
        default_db = cur.get("curation_db_path", "data/curation/chl_curation.db")
        default_communities = cur.get("community_data_file", "data/curation/communities.json")
        default_state = cur.get("curation_state_file", "data/curation/.curation_state_loop.json")
        default_eval = cur.get("evaluation_log_path", "data/curation/evaluation_log.csv")
        thresholds = cur.get("thresholds", {})
        default_threshold = thresholds.get("edge_keep", cur.get("min_similarity_threshold", 0.72))
        default_auto_dedup = thresholds.get("auto_dedup", 0.98)
        default_max_comm = cur.get("max_community_size", 50)
        default_algorithm = cur.get("algorithm", "louvain")
    except Exception:
        default_db = "data/curation/chl_curation.db"
        default_communities = "data/curation/communities.json"
        default_state = "data/curation/.curation_state_loop.json"
        default_eval = "data/curation/evaluation_log.csv"
        default_threshold = 0.72
        default_auto_dedup = 0.98
        default_max_comm = 50
        default_algorithm = "louvain"

    parser = argparse.ArgumentParser(description="Round loop (Stage 2)")
    parser.add_argument("--db-path", default=default_db, help="Path to curation SQLite DB")
    parser.add_argument(
        "--db-copy",
        default=None,
        help="Optional path to a duplicated DB used for reads/writes (copy of --db-path).",
    )
    parser.add_argument(
        "--refresh-db-copy",
        action="store_true",
        help="Overwrite --db-copy from --db-path before running.",
    )
    parser.add_argument("--communities", default=default_communities, help="Path to communities JSON")
    parser.add_argument("--neighbors-file", default="data/curation/neighbors.jsonl", help="Neighbor cache JSONL file")
    parser.add_argument("--state-file", default=default_state, help="Round-loop state file")
    parser.add_argument("--evaluation-log", default=default_eval, help="Evaluation log CSV output")
    parser.add_argument("--prompt", default=None, help="Optional prompt template YAML")
    parser.add_argument("--max-rounds", type=int, default=3)
    parser.add_argument("--improvement-threshold", type=float, default=0.05)
    parser.add_argument("--batch-size", type=int, default=0, help="Communities per round (0 = all)")
    parser.add_argument("--verbose", action="store_true", help="Verbose per-community logging")
    parser.add_argument(
        "--llm-timeout",
        type=int,
        default=None,
        help="Override per-call LLM timeout (seconds).",
    )
    parser.add_argument(
        "--max-runtime-seconds",
        type=int,
        default=None,
        help="Optional hard cap on total runtime (seconds).",
    )
    parser.add_argument(
        "--expected-llm-seconds",
        type=float,
        default=None,
        help="Expected seconds per LLM call (used to estimate --max-runtime-seconds if unset).",
    )
    parser.add_argument(
        "--runtime-multiplier",
        type=float,
        default=1.2,
        help="Safety multiplier for estimated runtime (default 1.2).",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--process-oversized", action="store_true")
    parser.add_argument("--max-community-size", type=int, default=default_max_comm)
    parser.add_argument("--algorithm", choices=["louvain", "leiden"], default=default_algorithm)
    parser.add_argument("--edge-threshold", type=float, default=default_threshold)
    parser.add_argument("--auto-dedup-threshold", type=float, default=default_auto_dedup)
    parser.add_argument("--two-pass", action="store_true", help="Use communities_rerank.json if present")
    parser.add_argument("--reset-state", action="store_true")
    parser.add_argument("--user", default="auto")
    return parser.parse_args()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_neighbors_records(path: Path) -> List[dict]:
    if not path.exists():
        raise FileNotFoundError(f"Neighbor cache not found: {path}")
    records: List[dict] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            rec = json.loads(line)
            if rec.get("type") == "meta":
                continue
            records.append(rec)
    return records


def load_items(session) -> List[Item]:
    rows = session.query(Experience).filter(Experience.sync_status != 2).all()
    return [
        Item(
            id=row.id,
            category=row.category_code,
            title=row.title or "",
            playbook=row.playbook or "",
            context=row.context,
            sync_status=row.sync_status,
        )
        for row in rows
    ]


def build_graph_from_neighbors(
    items: List[Item],
    neighbor_records: List[dict],
    threshold: float,
    per_category: bool,
) -> nx.Graph:
    id_to_item = {it.id: it for it in items}
    G = nx.Graph()

    for item in items:
        G.add_node(item.id, category=item.category, sync_status=item.sync_status)

    for rec in neighbor_records:
        src = rec.get("src")
        dst = rec.get("dst")
        if not src or not dst:
            continue
        item = id_to_item.get(src)
        anchor_item = id_to_item.get(dst)
        if not item or not anchor_item:
            continue
        if per_category and item.category != anchor_item.category:
            continue

        weight = rec.get("weight")
        if weight is None:
            weight = rec.get("embed_score", 0.0)
        weight = float(weight)
        if weight < threshold:
            continue

        edge_key = tuple(sorted((item.id, anchor_item.id)))
        existing = G.get_edge_data(*edge_key, default=None)
        if existing is None or weight > existing.get("weight", 0.0):
            G.add_edge(edge_key[0], edge_key[1], weight=weight)

    isolated = list(nx.isolates(G))
    if isolated:
        G.remove_nodes_from(isolated)
    return G


def detect_communities(G: nx.Graph, algorithm: str, per_category: bool) -> Dict[str, List[List[str]]]:
    communities_by_cat: Dict[str, List[List[str]]] = {}
    categories = {d.get("category") for _, d in G.nodes(data=True)}

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
    dry_run: bool,
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

    if dry_run:
        output_path = output_path.with_suffix(output_path.suffix + ".dryrun")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
    return payload


def compute_counts(session) -> Tuple[int, int]:
    pending = session.query(Experience).filter(Experience.sync_status == 0).count()
    total_active = session.query(Experience).filter(Experience.sync_status != 2).count()
    return pending, total_active


def write_eval_log(decisions: List[dict], output_path: Path, dry_run: bool) -> None:
    if dry_run:
        write_evaluation_log(decisions, output_path.with_suffix(output_path.suffix + ".dryrun"), dry_run=False)
    else:
        write_evaluation_log(decisions, output_path, dry_run=False)


def select_communities(
    communities: List[dict],
    status_by_id: Dict[str, int],
    process_oversized: bool,
    max_size: int,
    batch_size: int,
    already_done: Iterable[str],
) -> List[dict]:
    selected: List[dict] = []
    for comm in communities:
        cid = comm.get("id")
        if cid in already_done:
            continue
        members = comm.get("members", [])
        if not members:
            continue
        statuses = [status_by_id.get(mid, 0) for mid in members]
        if statuses and all(s == 2 for s in statuses):
            continue
        oversized = bool(comm.get("oversized")) or (comm.get("size", 0) > max_size)
        if oversized and not process_oversized:
            continue
        selected.append(comm)

    selected.sort(key=lambda c: c.get("priority_score", 0.0), reverse=True)
    if batch_size and batch_size > 0:
        selected = selected[:batch_size]
    return selected


def call_llm_with_retries(
    agent,
    messages: List[dict],
    allowed_ids: List[str],
    max_retries: int,
    retry_delays: List[int],
    retry_backoff: str,
) -> Tuple[bool, List[str], List[str], Dict[str, Any], Any]:
    def delay_for(attempt_index: int) -> float:
        if attempt_index <= len(retry_delays):
            return float(retry_delays[attempt_index - 1])
        base = 5.0
        if retry_backoff == "linear":
            return base * attempt_index
        return base * (2 ** (attempt_index - 1))

    ok = False
    errs: List[str] = []
    warnings: List[str] = []
    normalized: Dict[str, Any] = {}
    raw_reply: Any = ""

    for attempt in range(1, max_retries + 2):
        try:
            raw_reply = agent.generate_reply(messages=messages)
        except Exception as exc:
            errs = [f"LLM call failed on attempt {attempt}: {exc}"]
            if attempt <= max_retries:
                time.sleep(delay_for(attempt))
                continue
            break

        ok, errs, warnings, normalized = validate_response(raw_reply, allowed_ids)
        if ok:
            break
        if attempt <= max_retries:
            time.sleep(delay_for(attempt))

    return ok, errs, warnings, normalized, raw_reply


def apply_merges(
    session,
    merges: List[List[str]],
    user: str,
    dry_run: bool,
) -> Tuple[int, List[dict]]:
    decisions: List[dict] = []
    merged_count = 0
    for src, dst in merges:
        exp = session.query(Experience).filter(Experience.id == src).first()
        if not exp:
            continue
        if exp.sync_status == 2:
            continue
        decisions.append(
            {
                "timestamp": now_iso(),
                "user": user,
                "entry_id": src,
                "action": "merge",
                "target_id": dst,
                "was_correct": None,
                "notes": f"llm-merge {src} -> {dst}",
            }
        )
        merged_count += 1
        if not dry_run:
            exp.sync_status = 2
            session.add(
                CurationDecision(
                    entry_id=src,
                    action="merge",
                    target_id=dst,
                    notes="llm-merge",
                    user=user,
                )
            )
    return merged_count, decisions


def add_decision_note(
    session,
    entry_ids: List[str],
    action: str,
    user: str,
    notes: str,
    dry_run: bool,
) -> List[dict]:
    decisions: List[dict] = []
    for entry_id in entry_ids:
        decisions.append(
            {
                "timestamp": now_iso(),
                "user": user,
                "entry_id": entry_id,
                "action": action,
                "target_id": None,
                "was_correct": None,
                "notes": notes,
            }
        )
        if not dry_run:
            session.add(
                CurationDecision(
                    entry_id=entry_id,
                    action=action,
                    target_id=None,
                    notes=notes,
                    user=user,
                )
            )
    return decisions


def auto_dedup(
    communities: List[dict],
    threshold: float,
    session,
    user: str,
    dry_run: bool,
) -> Tuple[int, List[dict]]:
    pairs: List[Tuple[str, str]] = []
    for comm in communities:
        for edge in comm.get("edges", []):
            if len(edge) < 3:
                continue
            a, b, weight = edge[0], edge[1], float(edge[2])
            if weight >= threshold:
                pairs.append((a, b))

    if not pairs:
        return 0, []

    parent: Dict[str, str] = {}

    def find(x: str) -> str:
        while parent.get(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    nodes: set[str] = set()
    for a, b in pairs:
        nodes.add(a)
        nodes.add(b)
        parent.setdefault(a, a)
        parent.setdefault(b, b)
        union(a, b)

    groups: Dict[str, List[str]] = {}
    for node in nodes:
        root = find(node)
        groups.setdefault(root, []).append(node)

    decisions: List[dict] = []
    merged = 0
    for group in groups.values():
        group_sorted = sorted(group)
        anchor_id = None
        for candidate in group_sorted:
            exp = session.query(Experience).filter(Experience.id == candidate).first()
            if exp and exp.sync_status == 0:
                anchor_id = candidate
                break
        if not anchor_id:
            continue
        for member_id in group_sorted:
            if member_id == anchor_id:
                continue
            exp = session.query(Experience).filter(Experience.id == member_id).first()
            if not exp or exp.sync_status != 0:
                continue
            decisions.append(
                {
                    "timestamp": now_iso(),
                    "user": user,
                    "entry_id": member_id,
                    "action": "merge",
                    "target_id": anchor_id,
                    "was_correct": None,
                    "notes": f"auto-merge {member_id} -> {anchor_id}",
                }
            )
            merged += 1
            if not dry_run:
                exp.sync_status = 2
                session.add(
                    CurationDecision(
                        entry_id=member_id,
                        action="merge",
                        target_id=anchor_id,
                        notes="auto-merge high similarity",
                        user=user,
                    )
                )
    return merged, decisions


def load_communities(path: Path) -> Dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "communities" not in data:
        raise ValueError(f"Invalid communities file: {path}")
    return data


def make_state(
    db_path: Path,
    input_checksum: str,
    user: str,
    max_rounds: int,
) -> Dict[str, Any]:
    return {
        "run_id": f"overnight-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}",
        "db_path": str(db_path),
        "input_checksum": input_checksum,
        "user": user,
        "version": "1.0",
        "timestamp": now_iso(),
        "current_round": 1,
        "max_rounds": max_rounds,
        "progress_history": [],
        "communities_resolved": [],
        "last_community_index": 0,
        "last_bucket": None,
        "decisions": [],
    }


def write_morning_report(
    output_path: Path,
    summary: Dict[str, Any],
    rounds: List[Dict[str, Any]],
    manual_queue: List[str],
    warnings: List[str],
    dry_run: bool,
) -> None:
    lines = []
    lines.append("# Morning Report")
    lines.append("")
    lines.append("## Summary")
    lines.append(f"- Initial pending: {summary.get('initial_pending')}")
    lines.append(f"- Final pending: {summary.get('final_pending')}")
    lines.append(f"- Reduction: {summary.get('reduction_pct'):.2%}")
    lines.append(f"- Rounds run: {summary.get('rounds_run')}")
    lines.append(f"- Stop reason: {summary.get('stop_reason')}")
    if summary.get("estimated_max_runtime_seconds") is not None:
        lines.append(f"- Estimated max runtime (seconds): {summary.get('estimated_max_runtime_seconds')}")
        if summary.get("estimated_llm_calls") is not None:
            lines.append(f"- Estimated LLM calls: {summary.get('estimated_llm_calls')}")
        if summary.get("estimated_llm_seconds") is not None:
            lines.append(f"- Estimated seconds per call: {summary.get('estimated_llm_seconds')}")
        if summary.get("runtime_multiplier") is not None:
            lines.append(f"- Runtime multiplier: {summary.get('runtime_multiplier')}")
    lines.append("")
    lines.append("## Round Details")
    lines.append("| Round | Communities | Items | Merges | Manual Reviews | Progress Items | Progress Comms |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- |")
    for row in rounds:
        lines.append(
            f"| {row['round']} | {row['communities']} | {row['items']} | {row['merges']} | "
            f"{row['manual_reviews']} | {row['items_delta_pct']:.2%} | {row['comms_delta_pct']:.2%} |"
        )
    lines.append("")
    lines.append("## Manual Review Queue")
    if manual_queue:
        for cid in manual_queue:
            lines.append(f"- {cid}")
    else:
        lines.append("- (none)")
    if warnings:
        lines.append("")
        lines.append("## Warnings")
        for warning in warnings:
            lines.append(f"- {warning}")

    if dry_run:
        output_path = output_path.with_suffix(output_path.suffix + ".dryrun")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    source_db_path = Path(args.db_path)
    if not source_db_path.exists():
        print(f"❌ Database not found: {source_db_path}")
        return 1

    db_path = source_db_path
    if args.db_copy:
        db_copy_path = Path(args.db_copy)
        if args.refresh_db_copy or not db_copy_path.exists():
            db_copy_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_db_path, db_copy_path)
            print(f"✓ DB copied to {db_copy_path}")
        db_path = db_copy_path

    communities_path = Path(args.communities)
    if args.two_pass and communities_path.name == "communities.json":
        two_pass_path = communities_path.with_name("communities_rerank.json")
        if two_pass_path.exists():
            communities_path = two_pass_path

    neighbors_path = Path(args.neighbors_file)
    state_file = Path(args.state_file)
    eval_log_path = Path(args.evaluation_log)

    if args.reset_state and state_file.exists():
        state_file.unlink()
        print(f"✓ State file {state_file} removed")

    cfg, _ = load_scripts_config()
    cur_cfg = cfg.get("curation", {})
    per_category = bool(cur_cfg.get("per_category", True))
    min_comm_size = int(cur_cfg.get("min_community_size", 2))

    # Load or initialize state
    checksum = str(communities_path.stat().st_mtime)
    state = StateManager.load_state(state_file) or make_state(
        db_path=db_path,
        input_checksum=checksum,
        user=args.user,
        max_rounds=args.max_rounds,
    )

    llm_config, settings, cfg_path = build_llm_config()
    if args.llm_timeout is not None:
        llm_config["timeout"] = args.llm_timeout
        settings.timeout = args.llm_timeout

    estimated_seconds_per_call = None
    if args.max_runtime_seconds is None:
        estimate_seconds = None
        if args.expected_llm_seconds is not None:
            estimate_seconds = float(args.expected_llm_seconds)
        else:
            timeout_hint = args.llm_timeout or settings.timeout
            if timeout_hint is not None:
                estimate_seconds = float(timeout_hint)

        if estimate_seconds is not None:
            estimated_seconds_per_call = estimate_seconds
            initial_payload = load_communities(communities_path)
            comm_count = len(initial_payload.get("communities", []))
            per_round = args.batch_size if args.batch_size and args.batch_size > 0 else comm_count
            max_calls = args.max_rounds * max(1, per_round)
            est = int(max_calls * estimate_seconds * float(args.runtime_multiplier))
            args.max_runtime_seconds = max(1, est)
            args._estimated_llm_calls = max_calls
            print(f"✓ Estimated max runtime: {args.max_runtime_seconds}s ({max_calls} calls)")
    else:
        if args.expected_llm_seconds is not None:
            estimated_seconds_per_call = float(args.expected_llm_seconds)

    # Setup DB session
    engine = create_engine(f"sqlite:///{db_path}")
    Session = sessionmaker(bind=engine)
    try:
        from autogen import AssistantAgent
    except Exception as exc:  # pragma: no cover
        raise SystemExit(f"autogen is required to call LLM: {exc}")
    agent = AssistantAgent(name="curation_agent", llm_config=llm_config)

    warnings: List[str] = []
    errors: List[str] = []
    rounds_report: List[Dict[str, Any]] = []
    manual_queue: List[str] = []
    below_threshold_streak = 0
    start_time = time.time()

    session = Session()
    try:
        initial_pending, _ = compute_counts(session)

        # Auto-dedup before round 1
        communities_payload = load_communities(communities_path)
        merged_auto, auto_decisions = auto_dedup(
            communities_payload.get("communities", []),
            threshold=args.auto_dedup_threshold,
            session=session,
            user=args.user,
            dry_run=args.dry_run,
        )
        if auto_decisions:
            write_eval_log(auto_decisions, eval_log_path, dry_run=args.dry_run)
            if not args.dry_run:
                session.commit()
        if merged_auto:
            print(f"✓ Auto-dedup merged {merged_auto} entries")

        try:
            for round_index in range(state.get("current_round", 1), args.max_rounds + 1):
                communities_payload = load_communities(communities_path)
                communities = communities_payload.get("communities", [])
                if not communities:
                    warnings.append("No communities found; stopping.")
                    break

                member_ids = {mid for comm in communities for mid in comm.get("members", [])}
                if member_ids:
                    rows = session.query(Experience).filter(Experience.id.in_(member_ids)).all()
                    status_by_id = {row.id: row.sync_status for row in rows}
                else:
                    status_by_id = {}

                selected = select_communities(
                    communities,
                    status_by_id,
                    process_oversized=args.process_oversized,
                    max_size=args.max_community_size,
                    batch_size=args.batch_size,
                    already_done=state.get("communities_resolved", []),
                )

                if not selected:
                    warnings.append("No eligible communities to process; stopping.")
                    break

                merges_this_round = 0
                manual_this_round = 0

                for idx, comm in enumerate(selected, start=1):
                    if args.max_runtime_seconds is not None:
                        elapsed = time.time() - start_time
                        if elapsed >= args.max_runtime_seconds:
                            warnings.append("Max runtime reached; stopping early.")
                            below_threshold_streak = 0
                            raise StopIteration
                    state["last_community_index"] = idx
                    community_id = comm.get("id")
                    if not community_id:
                        continue
                    if args.verbose:
                        print(f"[round {round_index}] LLM call for {community_id} ({idx}/{len(selected)})", flush=True)

                    members = fetch_member_records(db_path, comm.get("members", []))
                    prompt_path = Path(args.prompt) if args.prompt else None
                    messages = build_prompt_messages(
                        comm,
                        members,
                        round_index=round_index,
                        prompt_path=prompt_path,
                    )

                    max_retries = 0
                    retry_delays: List[int] = []
                    retry_backoff = "exponential"

                    ok, errs, warn, normalized, raw_reply = call_llm_with_retries(
                        agent,
                        messages,
                        allowed_ids=comm.get("members", []),
                        max_retries=max_retries,
                        retry_delays=retry_delays,
                        retry_backoff=retry_backoff,
                    )
                    if warn and args.verbose:
                        for w in warn:
                            print(f"[round {round_index}] {community_id} warning: {w}", flush=True)
                    decision_label = normalized.get("decision") if ok else None
                    merge_count = len(normalized.get("merges", []) or []) if ok else 0
                    decision_msg = decision_label or ("invalid" if not ok else "unknown")
                    if args.verbose:
                        print(
                            f"[round {round_index}] LLM response for {community_id}: {decision_msg} merges={merge_count}",
                            flush=True,
                        )

                    decisions_for_log: List[dict] = []
                    if not ok:
                        warnings.extend(errs)
                        errors.extend(errs)
                        manual_this_round += 1
                        manual_queue.append(community_id)
                        decisions_for_log.extend(
                            add_decision_note(
                                session,
                                comm.get("members", []),
                                action="manual_review",
                                user=args.user,
                                notes="llm failure",
                                dry_run=args.dry_run,
                            )
                        )
                    else:
                        warnings.extend(warn)
                        decision = normalized.get("decision")
                        if decision in {"merge_all", "merge_subset"}:
                            merged_count, merge_decisions = apply_merges(
                                session,
                                normalized.get("merges", []),
                                user=args.user,
                                dry_run=args.dry_run,
                            )
                            merges_this_round += merged_count
                            decisions_for_log.extend(merge_decisions)
                        elif decision == "manual_review":
                            manual_this_round += 1
                            manual_queue.append(community_id)
                            decisions_for_log.extend(
                                add_decision_note(
                                    session,
                                    comm.get("members", []),
                                    action="manual_review",
                                    user=args.user,
                                    notes=normalized.get("notes") or "manual review",
                                    dry_run=args.dry_run,
                                )
                            )
                        else:
                            decisions_for_log.extend(
                                add_decision_note(
                                    session,
                                    comm.get("members", []),
                                    action="keep_separate",
                                    user=args.user,
                                    notes=normalized.get("notes") or "keep separate",
                                    dry_run=args.dry_run,
                                )
                            )

                        state.setdefault("communities_resolved", []).append(community_id)

                        if not args.dry_run:
                            session.commit()

                        StateManager.save_state(state, state_file, dry_run=args.dry_run)

                    if decisions_for_log:
                        write_eval_log(decisions_for_log, eval_log_path, dry_run=args.dry_run)

                # Rebuild communities using neighbor cache
                items = load_items(session)
                neighbor_records = load_neighbors_records(neighbors_path)
                G = build_graph_from_neighbors(
                    items=items,
                    neighbor_records=neighbor_records,
                    threshold=args.edge_threshold,
                    per_category=per_category,
                )

                if G.number_of_nodes() == 0:
                    warnings.append("Graph rebuild produced no nodes; stopping.")
                    break

                communities_by_cat = detect_communities(G, algorithm=args.algorithm, per_category=per_category)
                metadata = {
                    "total_items": G.number_of_nodes(),
                    "graph_edges": G.number_of_edges(),
                    "min_threshold": args.edge_threshold,
                    "algorithm": args.algorithm,
                    "per_category": per_category,
                    "min_community_size": min_comm_size,
                    "max_community_size": args.max_community_size,
                }

                communities_payload = export_communities(
                    G=G,
                    communities_by_cat=communities_by_cat,
                    output_path=communities_path,
                    metadata=metadata,
                    min_size=min_comm_size,
                    max_size=args.max_community_size,
                    dry_run=args.dry_run,
                )

                pending_now, total_active_now = compute_counts(session)
                prev_pending = initial_pending if not rounds_report else rounds_report[-1]["items"]
                prev_comms = rounds_report[-1]["communities"] if rounds_report else len(communities)
                comms_now = len(communities_payload.get("communities", []))

                items_delta_pct = 0.0 if prev_pending == 0 else (prev_pending - pending_now) / max(prev_pending, 1)
                comms_delta_pct = 0.0 if prev_comms == 0 else (prev_comms - comms_now) / max(prev_comms, 1)

                rounds_report.append(
                    {
                        "round": round_index,
                        "communities": comms_now,
                        "items": pending_now,
                        "merges": merges_this_round,
                        "manual_reviews": manual_this_round,
                        "items_delta_pct": items_delta_pct,
                        "comms_delta_pct": comms_delta_pct,
                    }
                )

                state["current_round"] = round_index + 1
                state["progress_history"].append(
                    {
                        "round": round_index,
                        "items_delta_pct": items_delta_pct,
                        "comms_delta_pct": comms_delta_pct,
                    }
                )
                state["last_community_index"] = 0
                state["communities_resolved"] = []

                StateManager.save_state(state, state_file, dry_run=args.dry_run)

                if items_delta_pct == 0.0 and comms_delta_pct == 0.0:
                    warnings.append("Zero progress in round; stopping.")
                    break

                if items_delta_pct < args.improvement_threshold and comms_delta_pct < args.improvement_threshold:
                    below_threshold_streak += 1
                else:
                    below_threshold_streak = 0

                if below_threshold_streak >= 2:
                    break
        except StopIteration:
            pass

        final_pending, _ = compute_counts(session)
    finally:
        session.close()

    rounds_run = len(rounds_report)
    reduction_pct = 0.0 if initial_pending == 0 else (initial_pending - final_pending) / max(initial_pending, 1)
    stop_reason = "convergence" if below_threshold_streak >= 2 else "max_rounds or early_stop"

    report_path = Path("data/curation/morning_report.md")
    write_morning_report(
        report_path,
        summary={
            "initial_pending": initial_pending,
            "final_pending": final_pending,
            "reduction_pct": reduction_pct,
            "rounds_run": rounds_run,
            "stop_reason": stop_reason,
            "estimated_max_runtime_seconds": args.max_runtime_seconds,
            "estimated_llm_calls": getattr(args, "_estimated_llm_calls", None),
            "estimated_llm_seconds": estimated_seconds_per_call,
            "runtime_multiplier": args.runtime_multiplier if estimated_seconds_per_call is not None else None,
        },
        rounds=rounds_report,
        manual_queue=manual_queue,
        warnings=warnings,
        dry_run=args.dry_run,
    )

    if warnings:
        print("Warnings:")
        for warning in warnings:
            print(f"- {warning}")
    if errors:
        print("Errors encountered; exiting with failure status.")
        return 2

    print(f"✓ Completed rounds: {rounds_run}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
