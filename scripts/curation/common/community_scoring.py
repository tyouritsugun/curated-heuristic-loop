"""
Community scoring utilities for curation.

Provides functions to score and prioritize communities based on similarity,
density, and size metrics.
"""

from typing import List, Tuple

import networkx as nx
import numpy as np


def score_community(G: nx.Graph, nodes: List[str]) -> Tuple[float, float, int]:
    """
    Score a community based on average similarity, density, and size.

    Args:
        G: NetworkX graph containing the nodes
        nodes: List of node IDs forming the community

    Returns:
        Tuple of (average_similarity, density, size)
    """
    sub = G.subgraph(nodes)
    size = sub.number_of_nodes()
    if size <= 1:
        return 0.0, 0.0, size
    weights = [data.get("weight", 0.0) for _, _, data in sub.edges(data=True)]
    avg_sim = float(np.mean(weights)) if weights else 0.0
    density = nx.density(sub) if size > 1 else 0.0
    return avg_sim, density, size


def size_score(size: int) -> float:
    """
    Compute a normalized size score.

    Small communities (< 3) get penalized, optimal is 3-10 nodes,
    larger communities get diminishing scores.

    Args:
        size: Number of nodes in the community

    Returns:
        Size score in range [0.0, 1.0]
    """
    if size < 3:
        return size / 3.0
    if size <= 10:
        return 1.0
    return max(0.0, min(1.0, 10.0 / float(size)))


def priority_score(avg_sim: float, density: float, size: int) -> float:
    """
    Compute overall priority score for a community.

    Weighted combination: 60% similarity, 30% density, 10% size.

    Args:
        avg_sim: Average edge weight (similarity) in [0.0, 1.0]
        density: Graph density in [0.0, 1.0]
        size: Number of nodes

    Returns:
        Priority score in range [0.0, 1.0]
    """
    return (0.6 * avg_sim) + (0.3 * density) + (0.1 * size_score(size))
