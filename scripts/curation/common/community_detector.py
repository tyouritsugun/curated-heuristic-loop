"""
Community detection algorithms for curation.

Provides Leiden and Louvain community detection with automatic fallback.
"""

from typing import Dict, List

import networkx as nx


def detect_communities(G: nx.Graph, algorithm: str, per_category: bool) -> Dict[str, List[List[str]]]:
    """
    Detect communities in a graph using Leiden or Louvain algorithms.

    Args:
        G: NetworkX graph with weighted edges
        algorithm: "leiden" or "louvain" (falls back to louvain if leiden unavailable)
        per_category: If True, partition graph by category attribute and detect communities separately

    Returns:
        Dictionary mapping category name to list of communities (each community is a list of node IDs)
    """
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
