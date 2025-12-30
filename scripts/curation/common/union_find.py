"""
Union-Find (Disjoint Set Union) data structure for grouping items.

Provides efficient union and find operations for merging duplicate pairs
into connected groups.
"""

from typing import Dict, List, Set, Tuple


class UnionFind:
    """
    Union-Find data structure with path compression.

    Used to group duplicate pairs into connected components.
    """

    def __init__(self):
        """Initialize empty union-find structure."""
        self.parent: Dict[str, str] = {}

    def find(self, x: str) -> str:
        """
        Find the root of the set containing x (with path compression).

        Args:
            x: Element to find

        Returns:
            Root element of the set containing x
        """
        if x not in self.parent:
            self.parent[x] = x
            return x

        # Path compression: flatten the tree
        if self.parent[x] != x:
            self.parent[x] = self.find(self.parent[x])

        return self.parent[x]

    def union(self, a: str, b: str) -> None:
        """
        Merge the sets containing a and b.

        Args:
            a: First element
            b: Second element
        """
        root_a = self.find(a)
        root_b = self.find(b)

        if root_a != root_b:
            self.parent[root_b] = root_a

    def get_groups(self) -> Dict[str, List[str]]:
        """
        Get all connected components as groups.

        Returns:
            Dictionary mapping root element to list of all elements in that set
        """
        groups: Dict[str, List[str]] = {}
        for node in self.parent:
            root = self.find(node)
            groups.setdefault(root, []).append(node)
        return groups


def group_pairs(pairs: List[Tuple[str, str]]) -> Dict[str, List[str]]:
    """
    Group pairs of items into connected components.

    Args:
        pairs: List of (item_a, item_b) tuples representing connections

    Returns:
        Dictionary mapping representative item to list of all connected items

    Example:
        >>> pairs = [("A", "B"), ("B", "C"), ("D", "E")]
        >>> groups = group_pairs(pairs)
        >>> len(groups)
        2
        >>> sorted(groups.values(), key=len, reverse=True)
        [['A', 'B', 'C'], ['D', 'E']]
    """
    if not pairs:
        return {}

    uf = UnionFind()
    nodes: Set[str] = set()

    for a, b in pairs:
        nodes.add(a)
        nodes.add(b)
        uf.union(a, b)

    return uf.get_groups()
