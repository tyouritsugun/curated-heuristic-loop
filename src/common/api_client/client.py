"""Shared HTTP client for CHL API server.

This is the canonical boundary adapter for calling the API server from
scripts and the MCP server.

Design constraints:
- Synchronous, single-shot HTTP calls
- No circuit breaker
- No automatic retries beyond what callers implement themselves
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import requests

from .errors import CHLAPIError, APIConnectionError, APIOperationError

logger = logging.getLogger(__name__)


class CHLAPIClient:
    """HTTP client for CHL API server.

    Provides helpers for:
    - Health checks
    - Worker coordination (pause, drain, resume)
    - Entry operations (read, write, update)
    - Queue status
    """

    # Timeout constants (in seconds)
    DEFAULT_TIMEOUT = 30
    HEALTH_CHECK_TIMEOUT = 2
    WORKER_OPERATION_TIMEOUT = 5
    QUEUE_DRAIN_TIMEOUT = 300
    QUEUE_DRAIN_BUFFER = 10  # Extra time for drain endpoint overhead

    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        timeout: int = DEFAULT_TIMEOUT,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()

    # Low-level helpers

    def _raw_request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        url = f"{self.base_url}{path}"
        timeout = kwargs.pop("timeout", self.timeout)
        try:
            response = self.session.request(method, url, timeout=timeout, **kwargs)
            return response
        except requests.RequestException as exc:
            logger.debug("API connection error: %s", exc)
            raise APIConnectionError(f"Failed to connect to API server at {url}: {exc}") from exc

    def request(self, method: str, path: str, **kwargs: Any) -> Dict[str, Any]:
        """Generic JSON request helper used by MCP layer."""
        response = self._raw_request(method, path, **kwargs)
        if not response.ok:
            raise APIOperationError(
                f"{method} {path} failed with status {response.status_code}: {response.text}"
            )
        try:
            return response.json()
        except ValueError as exc:
            raise APIOperationError(f"{method} {path} returned non-JSON response") from exc

    # Health & connection

    def check_health(self, timeout: Optional[int] = None) -> bool:
        """Return True if the server responds on /health."""
        try:
            response = self.session.get(
                f"{self.base_url}/health",
                timeout=timeout or self.HEALTH_CHECK_TIMEOUT,
            )
            return response.status_code in (200, 307)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Health check failed: %s", exc)
            return False

    def get_health(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """Return full /health payload."""
        return self.request("GET", "/health", timeout=timeout or self.timeout)

    def search_health(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """Return `/api/v1/search/health` payload."""
        return self.request("GET", "/api/v1/search/health", timeout=timeout or self.timeout)

    # Worker coordination / queue helpers

    def pause_workers(self, timeout: Optional[int] = None) -> bool:
        """Pause background workers."""
        try:
            response = self.session.post(
                f"{self.base_url}/admin/queue/pause",
                timeout=timeout or self.WORKER_OPERATION_TIMEOUT,
            )
            if response.status_code == 200:
                logger.info("Background workers paused successfully")
                return True
            if response.status_code == 503:
                logger.info("Worker pool not initialized (ML dependencies not available)")
                return True  # Not an error, just not available
            logger.warning("Failed to pause workers: HTTP %s", response.status_code)
            return False
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to pause workers: %s", exc)
            return False

    def resume_workers(self, timeout: Optional[int] = None) -> bool:
        """Resume background workers."""
        try:
            response = self.session.post(
                f"{self.base_url}/admin/queue/resume",
                timeout=timeout or self.WORKER_OPERATION_TIMEOUT,
            )
            if response.status_code == 200:
                logger.info("Background workers resumed successfully")
                return True
            if response.status_code == 503:
                logger.info("Worker pool not initialized (nothing to resume)")
                return True
            logger.warning("Failed to resume workers: HTTP %s", response.status_code)
            return False
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to resume workers: %s", exc)
            return False

    def drain_queue(self, timeout: int = None) -> Dict[str, Any]:
        """Wait for embedding queue to drain and return extended metadata."""
        if timeout is None:
            timeout = self.QUEUE_DRAIN_TIMEOUT
        result: Dict[str, Any] = {
            "success": False,
            "status": "error",
            "elapsed": None,
            "remaining": None,
            "message": None,
        }
        try:
            logger.info("Waiting for embedding queue to drain (max %ss)...", timeout)
            response = self.session.post(
                f"{self.base_url}/admin/queue/drain",
                params={"timeout": timeout},
                timeout=timeout + self.QUEUE_DRAIN_BUFFER,
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
                    logger.info("Queue drained in %.1fs", payload.get("elapsed", 0))
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
        except Exception as exc:  # noqa: BLE001
            result["message"] = str(exc)
            logger.warning("Failed to drain queue: %s", exc)
        return result

    def queue_status(self, timeout: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """Fetch queue + worker status snapshot."""
        try:
            response = self.session.get(
                f"{self.base_url}/admin/queue/status",
                timeout=timeout or self.WORKER_OPERATION_TIMEOUT,
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
        timeout: int = None,
        max_attempts: int = 3,
        stable_reads: int = 2,
    ) -> Dict[str, Any]:
        """Iteratively drain queue until it remains empty for multiple checks."""
        if timeout is None:
            timeout = self.QUEUE_DRAIN_TIMEOUT
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

    # Entry/category helpers

    def list_categories(self, timeout: Optional[int] = None) -> List[Dict[str, Any]]:
        """List all available categories."""
        try:
            response = self.session.get(
                f"{self.base_url}/api/v1/categories",
                timeout=timeout or self.timeout,
            )
            response.raise_for_status()
            return response.json()
        except requests.HTTPError as exc:
            raise APIOperationError(f"Failed to list categories: {exc}") from exc

    def read_entries(
        self,
        entity_type: str,
        category_code: str,
        ids: Optional[List[str]] = None,
        query: Optional[str] = None,
        limit: Optional[int] = None,
        timeout: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Read entries (experiences or manuals)."""
        payload: Dict[str, Any] = {
            "entity_type": entity_type,
            "category_code": category_code,
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
                timeout=timeout or self.timeout,
            )
            response.raise_for_status()
            return response.json()
        except requests.HTTPError as exc:
            raise APIOperationError(f"Failed to read entries: {exc}") from exc

    def write_entry(
        self,
        entity_type: str,
        category_code: str,
        data: Dict[str, Any],
        timeout: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Write a new entry."""
        payload = {
            "entity_type": entity_type,
            "category_code": category_code,
            "data": data,
        }

        try:
            response = self.session.post(
                f"{self.base_url}/api/v1/entries/write",
                json=payload,
                timeout=timeout or self.timeout,
            )
            response.raise_for_status()
            return response.json()
        except requests.HTTPError as exc:
            raise APIOperationError(f"Failed to write entry: {exc}") from exc

    def update_entry(
        self,
        entity_type: str,
        category_code: str,
        entry_id: str,
        updates: Dict[str, Any],
        force_contextual: bool = False,
        timeout: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Update an existing entry."""
        payload = {
            "entity_type": entity_type,
            "category_code": category_code,
            "entry_id": entry_id,
            "updates": updates,
            "force_contextual": force_contextual,
        }

        try:
            response = self.session.post(
                f"{self.base_url}/api/v1/entries/update",
                json=payload,
                timeout=timeout or self.timeout,
            )
            response.raise_for_status()
            return response.json()
        except requests.HTTPError as exc:
            raise APIOperationError(f"Failed to update entry: {exc}") from exc

    # Operations helpers

    def start_operation(
        self,
        job_type: str,
        payload: Optional[Dict[str, Any]] = None,
        timeout: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Trigger an operations job (e.g., import-sheets, rebuild-index)."""
        body = {"payload": payload or {}}
        response = self._raw_request(
            "POST",
            f"/api/v1/operations/{job_type}",
            json=body,
            timeout=timeout or self.timeout,
        )
        if not response.ok:
            raise APIOperationError(
                f"POST /api/v1/operations/{job_type} failed with status "
                f"{response.status_code}: {response.text}"
            )
        try:
            return response.json()
        except ValueError as exc:
            raise APIOperationError(
                f"POST /api/v1/operations/{job_type} returned non-JSON response"
            ) from exc

    def get_operation_job(
        self,
        job_id: str,
        timeout: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Fetch a single operations job by id."""
        return self.request(
            "GET",
            f"/api/v1/operations/jobs/{job_id}",
            timeout=timeout or self.timeout,
        )

    # Entry operations

    def export_entries(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """Export all entries (experiences, manuals, categories) from the database."""
        return self.request(
            "GET",
            "/api/v1/entries/export",
            timeout=timeout or self.timeout,
        )

    # Context manager support

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.session.close()
