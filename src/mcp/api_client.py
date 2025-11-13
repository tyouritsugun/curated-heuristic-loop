"""HTTP client for API server communication with circuit breaker."""
import httpx
import logging
import time
from typing import Dict, Any, Optional, Callable
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception

from src.mcp.errors import (
    MCPServerError,
    MCPTransportError,
    MCPValidationError,
    MCPNotFoundError,
    MCPConflictError,
    translate_http_error,
)

logger = logging.getLogger(__name__)


class CircuitBreaker:
    """
    Circuit breaker to prevent cascading failures.

    States:
    - CLOSED: Normal operation
    - OPEN: Too many failures, reject requests immediately
    - HALF_OPEN: Allow one test request after timeout

    Behavior:
    - Opens after `failure_threshold` consecutive failures
    - Stays open for `timeout` seconds
    - Transitions to HALF_OPEN to test recovery
    - Closes if test request succeeds
    """

    def __init__(self, failure_threshold: int = 5, timeout: int = 60):
        self.failure_threshold = failure_threshold
        self.timeout = timeout
        self.failures = 0
        self.state = "CLOSED"
        self.opened_at = None

    def call(self, func: Callable, *args, **kwargs):
        """Execute function with circuit breaker protection."""
        if self.state == "OPEN":
            # Check if timeout elapsed
            if self.opened_at and time.time() - self.opened_at >= self.timeout:
                logger.info("Circuit breaker transitioning to HALF_OPEN")
                self.state = "HALF_OPEN"
            else:
                wait_time = int(self.timeout - (time.time() - self.opened_at)) if self.opened_at else self.timeout
                raise MCPServerError(
                    f"API server is currently unavailable. "
                    f"Please try again in {wait_time} seconds."
                )

        try:
            result = func(*args, **kwargs)

            # Success: reset or close circuit
            if self.state == "HALF_OPEN":
                logger.info("Circuit breaker closing after successful test")
                self.state = "CLOSED"
            self.failures = 0

            return result

        except Exception as e:
            # Only count server-side failures and transport errors
            # Do NOT count client errors (400, 404, 409)
            is_server_failure = isinstance(e, (MCPServerError, MCPTransportError))
            is_client_error = isinstance(e, (MCPValidationError, MCPNotFoundError, MCPConflictError))

            if is_server_failure:
                self.failures += 1

                if self.failures >= self.failure_threshold:
                    logger.error(
                        "Circuit breaker opening after %d failures",
                        self.failures
                    )
                    self.state = "OPEN"
                    self.opened_at = time.time()
            elif not is_client_error:
                # Unknown error type - count it to be safe
                self.failures += 1

                if self.failures >= self.failure_threshold:
                    logger.error(
                        "Circuit breaker opening after %d failures (unknown error type)",
                        self.failures
                    )
                    self.state = "OPEN"
                    self.opened_at = time.time()

            raise


class APIClient:
    """HTTP client for CHL API server with retry and error handling."""

    def __init__(
        self,
        base_url: str,
        timeout: float = 30.0,
        circuit_breaker_threshold: int = 5,
        circuit_breaker_timeout: int = 60
    ):
        self.base_url = base_url.rstrip('/')
        self.client = httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": "CHL-MCP-Client/1.0"}
        )
        self.circuit_breaker = CircuitBreaker(
            failure_threshold=circuit_breaker_threshold,
            timeout=circuit_breaker_timeout
        )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception(
            lambda exc: isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in (503, 429)
                       or isinstance(exc, (httpx.TimeoutException, httpx.ConnectError))
        ),
        reraise=True
    )
    def _make_request(
        self,
        method: str,
        path: str,
        **kwargs
    ) -> httpx.Response:
        """
        Make HTTP request with automatic retry.

        Retries on:
        - Network errors (connection refused, timeout)
        - 503 Service Unavailable
        - 429 Too Many Requests

        Does NOT retry on:
        - 4xx client errors (except 429)
        - 500 Internal Server Error (should be investigated)
        """
        url = f"{self.base_url}{path}"
        logger.debug(f"{method} {url}")

        start = time.perf_counter()
        response = self.client.request(method, url, **kwargs)
        duration_ms = (time.perf_counter() - start) * 1000
        logger.info(
            "http_request method=%s path=%s status=%s duration_ms=%.1f",
            method,
            path,
            response.status_code,
            duration_ms,
        )

        # Retry on specific status codes by raising exception
        if response.status_code in (503, 429):
            logger.warning(
                f"Retryable error {response.status_code} from {url}"
            )
            response.raise_for_status()

        return response

    def request(
        self,
        method: str,
        path: str,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Make HTTP request with circuit breaker and error translation.

        Returns:
            Response JSON data

        Raises:
            MCPError subclass on failure
        """
        def _request():
            try:
                response = self._make_request(method, path, **kwargs)
                response.raise_for_status()
                return response.json()

            except httpx.HTTPStatusError as e:
                # Translate HTTP errors to MCP errors
                raise translate_http_error(e)

            except httpx.RequestError as e:
                raise MCPTransportError(f"Failed to connect to API server: {e}")

        # Execute with circuit breaker
        return self.circuit_breaker.call(_request)

    def check_health(self) -> Dict[str, Any]:
        """Check API server health."""
        response = self._make_request("GET", "/health")
        return response.json()

    def close(self):
        """Close HTTP client."""
        self.client.close()


def startup_health_check(api_client: APIClient, max_wait: int = 30) -> bool:
    """
    Check API health on startup.

    Behavior:
    - Wait up to max_wait seconds for API to become healthy
    - Poll health endpoint every 2 seconds
    - Return True if healthy or degraded, False otherwise
    """
    start_time = time.time()
    while time.time() - start_time < max_wait:
        try:
            health = api_client.check_health()
            status = health.get("status")

            if status == "healthy":
                logger.info("API server is healthy")
                return True
            elif status == "degraded":
                logger.warning(
                    "API server is degraded but functional: %s",
                    health.get("components")
                )
                return True  # Allow startup with degraded components
            else:
                logger.warning("API server is unhealthy, retrying...")

        except Exception as e:
            logger.warning(f"Health check failed: {e}, retrying...")

        time.sleep(2)

    logger.error(
        "API server did not become healthy within %d seconds",
        max_wait
    )
    return False
