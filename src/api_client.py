"""Shared API client for operational scripts.

Usage:
    from src.api_client import get_api_client

    client = get_api_client()  # Uses CHL_API_BASE_URL env var

    # Check if API is available
    if client.is_available():
        # Pause queue before bulk import
        client.pause_queue()

        # Wait for queue to drain
        client.drain_queue(timeout=300)

        # Resume queue
        client.resume_queue()
"""
import os
import httpx
import logging
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


class ScriptAPIClient:
    """Simple API client for operational scripts."""

    def __init__(self, base_url: Optional[str] = None, timeout: float = 30.0):
        self.base_url = (base_url or os.getenv("CHL_API_BASE_URL", "http://localhost:8000")).rstrip('/')
        self.timeout = timeout

    def is_available(self) -> bool:
        """Check if API server is available."""
        try:
            response = httpx.get(f"{self.base_url}/health", timeout=5)
            return response.status_code == 200
        except Exception:
            return False

    def pause_queue(self) -> Dict[str, Any]:
        """Pause background workers."""
        response = httpx.post(f"{self.base_url}/admin/queue/pause", timeout=self.timeout)
        response.raise_for_status()
        return response.json()

    def resume_queue(self) -> Dict[str, Any]:
        """Resume background workers."""
        response = httpx.post(f"{self.base_url}/admin/queue/resume", timeout=self.timeout)
        response.raise_for_status()
        return response.json()

    def drain_queue(self, timeout: int = 300) -> Dict[str, Any]:
        """Wait for queue to empty."""
        response = httpx.post(
            f"{self.base_url}/admin/queue/drain",
            params={"timeout": timeout},
            timeout=timeout + 10  # Add buffer for HTTP timeout
        )
        response.raise_for_status()
        return response.json()

    def get_queue_status(self) -> Dict[str, Any]:
        """Get queue and worker status."""
        response = httpx.get(f"{self.base_url}/admin/queue/status", timeout=self.timeout)
        response.raise_for_status()
        return response.json()


def get_api_client(base_url: Optional[str] = None) -> ScriptAPIClient:
    """Factory function to get API client with default settings."""
    return ScriptAPIClient(base_url=base_url)
