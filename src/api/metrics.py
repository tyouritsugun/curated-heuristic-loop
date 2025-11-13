"""Simple in-memory metrics collector for observability."""

from collections import defaultdict
import threading
from typing import Dict, Any


class SimpleMetrics:
    """Thread-safe in-memory metrics collector."""

    def __init__(self):
        self._counters = defaultdict(int)
        self._histograms = defaultdict(list)
        self._lock = threading.Lock()

    def increment(self, name: str, value: int = 1):
        """Increment a counter metric."""
        with self._lock:
            self._counters[name] += value

    def observe(self, name: str, value: float):
        """Observe a value for a histogram metric."""
        with self._lock:
            self._histograms[name].append(value)
            # Keep only last 1000 values to prevent unbounded growth
            if len(self._histograms[name]) > 1000:
                self._histograms[name] = self._histograms[name][-1000:]

    def get_snapshot(self) -> Dict[str, Any]:
        """Get a snapshot of all metrics."""
        with self._lock:
            return {
                "counters": dict(self._counters),
                "histograms": {
                    k: {
                        "count": len(v),
                        "sum": sum(v),
                        "avg": sum(v) / len(v) if v else 0,
                        "min": min(v) if v else 0,
                        "max": max(v) if v else 0,
                    }
                    for k, v in self._histograms.items()
                }
            }


# Global metrics instance
metrics = SimpleMetrics()
