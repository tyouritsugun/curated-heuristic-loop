"""Canonical telemetry metric names used for persistence.

These names use dot-delimited grouping to keep charts and queries consistent.

Examples:
- queue.depth
- workers.pool
- operations.import.progress_pct
"""

# Core metrics
QUEUE_DEPTH = "queue.depth"
WORKER_POOL = "workers.pool"

# Operation progress (optional extensions)
OP_IMPORT_PROGRESS = "operations.import.progress_pct"
OP_EXPORT_PROGRESS = "operations.export.progress_pct"
OP_INDEX_PROGRESS = "operations.index.progress_pct"

__all__ = [
    "QUEUE_DEPTH",
    "WORKER_POOL",
    "OP_IMPORT_PROGRESS",
    "OP_EXPORT_PROGRESS",
    "OP_INDEX_PROGRESS",
]
