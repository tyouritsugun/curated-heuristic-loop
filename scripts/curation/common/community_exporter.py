"""
Community export utilities for curation.

Provides functions to export community detection results to JSON files.
"""

import json
from pathlib import Path
from typing import Dict, List

import networkx as nx

from scripts.curation.common.community_scoring import priority_score, score_community


def export_communities(
    G: nx.Graph,
    communities_by_cat: Dict[str, List[List[str]]],
    output_path: Path,
    metadata: Dict[str, object],
    min_size: int,
    max_size: int,
    dry_run: bool = False,
) -> Dict[str, object]:
    """
    Export detected communities to JSON file.

    Args:
        G: NetworkX graph containing the communities
        communities_by_cat: Dictionary mapping category to list of communities
        output_path: Path where JSON file will be written
        metadata: Metadata dictionary to include in output
        min_size: Minimum community size (smaller communities skipped)
        max_size: Maximum community size (larger ones flagged as oversized)
        dry_run: If True, appends .dryrun suffix to output path

    Returns:
        Dictionary containing communities and metadata
    """
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
