"""Shared HTTP client for CHL API server.

This client provides a reusable interface for:
- Scripts (import, export, operational tools)
- MCP server (when forwarding requests to API)
- External integrations

The client handles:
- Connection checking and health monitoring
- Worker coordination (pause/drain/resume)
- Standard API operations (CRUD, search)
- Error handling and retries
"""

import logging
from typing import Optional, Dict, Any, List
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


logger = logging.getLogger(__name__)


class CHLAPIError(Exception):
    """Base exception for CHL API client errors."""
    pass


class APIConnectionError(CHLAPIError):
    """API server is not reachable."""
    pass


class APIOperationError(CHLAPIError):
    """API operation failed."""
    pass


class CHLAPIClient:
    """HTTP client for CHL API server.

    Provides methods for:
    - Health checks
    - Worker coordination (pause, drain, resume)
    - Entry operations (read, write, update, delete)
    - Index operations (status, rebuild)
    - Queue management

    Example:
        client = CHLAPIClient("http://localhost:8000")

        # Check health
        if client.check_health():
            # Coordinate with workers
            if client.pause_workers():
                client.drain_queue(timeout=300)
                # ... do import/export ...
                client.resume_workers()
    """

    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        timeout: int = 30,
        retry_count: int = 3,
    ):
        """Initialize API client.

        Args:
            base_url: Base URL for API server
            timeout: Default request timeout in seconds
            retry_count: Number of retries for failed requests
        """
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

        # Create session with retry logic
        self.session = requests.Session()
        retry_strategy = Retry(
            total=retry_count,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "POST", "DELETE"]
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

    # Health & Connection

    def check_health(self, timeout: Optional[int] = None) -> bool:
        """Check if API server is running.

        Args:
            timeout: Request timeout in seconds (default: use client default)

        Returns:
            True if server is reachable, False otherwise
        """
        try:
            response = self.session.get(
                f"{self.base_url}/health",
                timeout=timeout or 2
            )
            return response.status_code in (200, 307)
        except Exception as e:
            logger.debug(f"Health check failed: {e}")
            return False

    # Worker Coordination

    def pause_workers(self, timeout: Optional[int] = None) -> bool:
        """Pause background workers.

        Args:
            timeout: Request timeout in seconds

        Returns:
            True if successful, False otherwise
        """
        try:
            response = self.session.post(
                f"{self.base_url}/admin/queue/pause",
                timeout=timeout or 5
            )
            if response.status_code == 200:
                logger.info("Background workers paused successfully")
                return True
            elif response.status_code == 503:
                logger.info("Worker pool not initialized (ML dependencies not available)")
                return True  # Not an error, just not available
            else:
                logger.warning(f"Failed to pause workers: HTTP {response.status_code}")
                return False
        except Exception as e:
            logger.warning(f"Failed to pause workers: {e}")
            return False

    def resume_workers(self, timeout: Optional[int] = None) -> bool:
        """Resume background workers.

        Args:
            timeout: Request timeout in seconds

        Returns:
            True if successful, False otherwise
        """
        try:
            response = self.session.post(
                f"{self.base_url}/admin/queue/resume",
                timeout=timeout or 5
            )
            if response.status_code == 200:
                logger.info("Background workers resumed successfully")
                return True
            elif response.status_code == 503:
                logger.info("Worker pool not initialized (nothing to resume)")
                return True
            else:
                logger.warning(f"Failed to resume workers: HTTP {response.status_code}")
                return False
        except Exception as e:
            logger.warning(f"Failed to resume workers: {e}")
            return False

    def drain_queue(self, timeout: int = 300) -> Dict[str, Any]:
        """Wait for embedding queue to drain and return extended metadata."""
        result: Dict[str, Any] = {
            "success": False,
            "status": "error",
            "elapsed": None,
            "remaining": None,
            "message": None,
        }
        try:
            logger.info(f"Waiting for embedding queue to drain (max {timeout}s)...")
            response = self.session.post(
                f"{self.base_url}/admin/queue/drain",
                params={"timeout": timeout},
                timeout=timeout + 10,
            )
            if response.status_code == 200:
                payload = response.json()
                status = payload.get("status") or "unknown"
                result.update(
                    status=status,
                    elapsed=payload.get("elapsed"),
                    remaining=payload.get("remaining"),
                    success=status == "drained",
                )
                if result["success"]:
                    logger.info(f"Queue drained in {payload.get('elapsed', 0):.1f}s")
                else:
                    logger.warning(
                        "Queue drain returned status=%s (remaining=%s)",
                        status,
                        payload.get("remaining"),
                    )
            elif response.status_code == 503:
                result.update(
                    success=True,
                    status="skipped",
                    message="Worker pool not initialized",
                )
                logger.info("Worker pool not initialized (nothing to drain)")
            else:
                result["message"] = f"HTTP {response.status_code}"
                logger.warning("Failed to drain queue: HTTP %s", response.status_code)
        except Exception as exc:  # noqa: BLE001 - best-effort logging
            result["message"] = str(exc)
            logger.warning("Failed to drain queue: %s", exc)
        return result

    def queue_status(self, timeout: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """Fetch queue + worker status snapshot."""
        try:
            response = self.session.get(
                f"{self.base_url}/admin/queue/status",
                timeout=timeout or 5,
            )
            if response.status_code == 200:
                return response.json()
            logger.warning("Failed to fetch queue status: HTTP %s", response.status_code)
            return None
        except Exception as exc:  # noqa: BLE001
            logger.debug("Queue status fetch failed: %s", exc)
            return None

    @staticmethod
    def queue_active_total(snapshot: Optional[Dict[str, Any]]) -> Optional[int]:
        """Return pending+processing total from a queue snapshot (if available)."""
        if not snapshot:
            return None
        queue_block = snapshot.get("queue") if isinstance(snapshot, dict) else None
        if not isinstance(queue_block, dict):
            queue_block = snapshot if isinstance(snapshot, dict) else None
        if not isinstance(queue_block, dict):
            return None
        pending = queue_block.get("pending")
        processing = queue_block.get("processing")
        total = 0
        found = False
        if isinstance(pending, dict) and isinstance(pending.get("total"), int):
            total += pending["total"]
            found = True
        if isinstance(processing, dict) and isinstance(processing.get("total"), int):
            total += processing["total"]
            found = True
        return total if found else None

    def wait_for_queue_drain(
        self,
        timeout: int = 300,
        max_attempts: int = 3,
        stable_reads: int = 2,
    ) -> Dict[str, Any]:
        """Iteratively drain queue until it remains empty for multiple checks."""
        history: List[Dict[str, Any]] = []
        summary: Dict[str, Any] = {
            "success": False,
            "attempts": 0,
            "stable_reads": 0,
            "initial_remaining": None,
            "final_remaining": None,
            "last_result": None,
            "history": history,
        }

        snapshot = self.queue_status()
        initial_remaining = self.queue_active_total(snapshot)
        summary["initial_remaining"] = initial_remaining
        if initial_remaining == 0:
            summary["success"] = True
            summary["final_remaining"] = 0
            return summary

        while summary["attempts"] < max_attempts:
            summary["attempts"] += 1
            result = self.drain_queue(timeout=timeout)
            snapshot = self.queue_status()
            remaining = self.queue_active_total(snapshot)
            history.append(
                {
                    "attempt": summary["attempts"],
                    "result": result,
                    "remaining": remaining,
                }
            )
            summary["last_result"] = result
            summary["final_remaining"] = remaining
            if not result.get("success"):
                break
            if remaining == 0:
                summary["stable_reads"] += 1
                if summary["stable_reads"] >= stable_reads:
                    summary["success"] = True
                    break
            elif remaining is None and result.get("success"):
                summary["success"] = True
                break
            else:
                summary["stable_reads"] = 0
        return summary

    # Entry Operations

    def list_categories(self, timeout: Optional[int] = None) -> List[Dict[str, Any]]:
        """List all available categories.

        Args:
            timeout: Request timeout in seconds

        Returns:
            List of category dictionaries

        Raises:
            APIOperationError: If request fails
        """
        try:
            response = self.session.get(
                f"{self.base_url}/api/v1/categories",
                timeout=timeout or self.timeout
            )
            response.raise_for_status()
            return response.json()
        except requests.HTTPError as e:
            raise APIOperationError(f"Failed to list categories: {e}") from e

    def read_entries(
        self,
        entity_type: str,
        category_code: str,
        ids: Optional[List[str]] = None,
        query: Optional[str] = None,
        limit: Optional[int] = None,
        timeout: Optional[int] = None
    ) -> Dict[str, Any]:
        """Read entries (experiences or manuals).

        Args:
            entity_type: "experience" or "manual"
            category_code: Category code (e.g., "PGS")
            ids: Optional list of entry IDs to retrieve
            query: Optional semantic search query
            limit: Optional result limit
            timeout: Request timeout in seconds

        Returns:
            Dictionary with entries and metadata

        Raises:
            APIOperationError: If request fails
        """
        payload = {
            "entity_type": entity_type,
            "category_code": category_code
        }
        if ids is not None:
            payload["ids"] = ids
        if query is not None:
            payload["query"] = query
        if limit is not None:
            payload["limit"] = limit

        try:
            response = self.session.post(
                f"{self.base_url}/api/v1/entries/read",
                json=payload,
                timeout=timeout or self.timeout
            )
            response.raise_for_status()
            return response.json()
        except requests.HTTPError as e:
            raise APIOperationError(f"Failed to read entries: {e}") from e

    def write_entry(
        self,
        entity_type: str,
        category_code: str,
        data: Dict[str, Any],
        timeout: Optional[int] = None
    ) -> Dict[str, Any]:
        """Write a new entry.

        Args:
            entity_type: "experience" or "manual"
            category_code: Category code
            data: Entry data dictionary
            timeout: Request timeout in seconds

        Returns:
            Result dictionary with entry_id

        Raises:
            APIOperationError: If request fails
        """
        payload = {
            "entity_type": entity_type,
            "category_code": category_code,
            "data": data
        }

        try:
            response = self.session.post(
                f"{self.base_url}/api/v1/entries/write",
                json=payload,
                timeout=timeout or self.timeout
            )
            response.raise_for_status()
            return response.json()
        except requests.HTTPError as e:
            raise APIOperationError(f"Failed to write entry: {e}") from e

    def update_entry(
        self,
        entity_type: str,
        category_code: str,
        entry_id: str,
        updates: Dict[str, Any],
        force_contextual: bool = False,
        timeout: Optional[int] = None
    ) -> Dict[str, Any]:
        """Update an existing entry.

        Args:
            entity_type: "experience" or "manual"
            category_code: Category code
            entry_id: Entry ID to update
            updates: Dictionary of fields to update
            force_contextual: Force update even if global entry
            timeout: Request timeout in seconds

        Returns:
            Result dictionary

        Raises:
            APIOperationError: If request fails
        """
        payload = {
            "entity_type": entity_type,
            "category_code": category_code,
            "entry_id": entry_id,
            "updates": updates,
            "force_contextual": force_contextual
        }

        try:
            response = self.session.post(
                f"{self.base_url}/api/v1/entries/update",
                json=payload,
                timeout=timeout or self.timeout
            )
            response.raise_for_status()
            return response.json()
        except requests.HTTPError as e:
            raise APIOperationError(f"Failed to update entry: {e}") from e

    # Index Operations

    def rebuild_index(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """Trigger FAISS index rebuild.

        Args:
            timeout: Request timeout in seconds (should be longer for rebuild)

        Returns:
            Result dictionary

        Raises:
            APIOperationError: If request fails
        """
        try:
            response = self.session.post(
                f"{self.base_url}/admin/index/rebuild",
                timeout=timeout or 600  # Rebuild can take a while
            )
            response.raise_for_status()
            return response.json()
        except requests.HTTPError as e:
            raise APIOperationError(f"Failed to rebuild index: {e}") from e

    # Context Manager Support

    def __enter__(self):
        """Enter context manager."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Exit context manager (close session)."""
        self.session.close()
