#!/usr/bin/env python
"""Environment diagnostics for CHL API server - CPU-only backend (Phase B).

This script validates CPU-only environment readiness before installing the API
server environment. It must be run with the API server stopped.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import textwrap
import urllib.error
import urllib.request
from typing import Optional


def _extend_sys_path() -> None:
    """Ensure src/ is importable."""
    import sys
    from pathlib import Path

    project_root = Path(__file__).resolve().parent.parent
    root_str = str(project_root)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)
    from src.common.config.config import ensure_project_root_on_sys_path  # noqa: E402
    ensure_project_root_on_sys_path()


_extend_sys_path()

from src.common.config.config import DATA_DIR, RUNTIME_CONFIG_PATH, save_runtime_config  # noqa: E402


logger = logging.getLogger(__name__)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check CPU-only environment before API setup.",
    )
    parser.add_argument(
        "--api-url",
        default=os.getenv("CHL_API_BASE_URL", "http://127.0.0.1:8000"),
        help="API base URL to probe for a running server (default: %(default)s)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Allow running even if the API server appears to be running.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging to stderr.",
    )
    return parser.parse_args()


def _configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(levelname)s: %(message)s",
    )


def _api_server_running(api_url: str) -> bool:
    """Best-effort probe to see if anything responds at api_url."""
    try:
        with urllib.request.urlopen(api_url, timeout=2) as resp:
            # Any HTTP response (even 404) means "server is running".
            logger.debug("API probe to %s returned status %s", api_url, getattr(resp, "status", "unknown"))
            return True
    except urllib.error.URLError as exc:
        logger.debug("API probe to %s failed: %s (treating as not running)", api_url, exc)
        return False
    except Exception as exc:  # noqa: BLE001
        logger.debug("API probe to %s raised %s (treating as not running)", api_url, exc)
        return False


def _ensure_api_stopped(api_url: str, force: bool) -> None:
    if force:
        return
    if _api_server_running(api_url):
        msg = textwrap.dedent(
            f"""
            ✗ API server appears to be running at {api_url}

            Please stop the API server before running environment diagnostics.
            This script is intended for pre-installation checks only.

            If you are sure it is safe to proceed, re-run with:
              python scripts/check_api_env_cpu.py --force
            """
        ).strip()
        print(msg)
        sys.exit(1)


def _get_system_memory_gb() -> Optional[float]:
    """Best-effort detection of total system memory in GB."""
    try:
        if hasattr(os, "sysconf"):
            page_size = os.sysconf("SC_PAGE_SIZE")
            phys_pages = os.sysconf("SC_PHYS_PAGES")
            if isinstance(page_size, int) and isinstance(phys_pages, int):
                return (page_size * phys_pages) / (1024 ** 3)
    except (OSError, ValueError, AttributeError):
        pass

    if sys.platform == "win32":
        try:
            import ctypes as _ctypes

            class MEMORYSTATUSEX(_ctypes.Structure):
                _fields_ = [
                    ("dwLength", _ctypes.c_ulong),
                    ("dwMemoryLoad", _ctypes.c_ulong),
                    ("ullTotalPhys", _ctypes.c_ulonglong),
                    ("ullAvailPhys", _ctypes.c_ulonglong),
                    ("ullTotalPageFile", _ctypes.c_ulonglong),
                    ("ullAvailPageFile", _ctypes.c_ulonglong),
                    ("ullTotalVirtual", _ctypes.c_ulonglong),
                    ("ullAvailVirtual", _ctypes.c_ulonglong),
                    ("sullAvailExtendedVirtual", _ctypes.c_ulonglong),
                ]

            stat = MEMORYSTATUSEX()
            stat.dwLength = _ctypes.sizeof(MEMORYSTATUSEX)
            if _ctypes.windll.kernel32.GlobalMemoryStatusEx(_ctypes.byref(stat)):  # type: ignore[attr-defined]
                return stat.ullTotalPhys / (1024 ** 3)
        except Exception:  # noqa: BLE001
            return None

    return None




def _save_runtime_config(backend: str) -> None:
    """Save runtime configuration to runtime_config.json."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    config = {
        "backend": backend,
        "detected_at": None,  # CPU mode doesn't need detection timestamp
        "status": "configured",
    }
    save_runtime_config(config)
    logger.info("Saved runtime configuration to %s", RUNTIME_CONFIG_PATH)


def main() -> None:
    args = _parse_args()
    _configure_logging(args.verbose)

    _ensure_api_stopped(args.api_url, args.force)

    print("Running CHL environment diagnostics (CPU-only mode)...")

    # CPU-only mode: no GPU detection or prerequisite checks needed
    backend = "cpu"
    memory_gb = _get_system_memory_gb()

    # Save runtime configuration
    _save_runtime_config(backend)

    print("\n✓ Environment diagnostics completed successfully.\n")
    print(f"  - Detected backend: {backend}")
    if memory_gb:
        print(f"  - System memory: {memory_gb:.2f} GB")
    print("  - CPU-only mode: No GPU dependencies required")
    print("  - No embedding or reranker models needed (uses SQLite keyword search)")
    print(f"  - Runtime config saved to: {RUNTIME_CONFIG_PATH}")

    sys.exit(0)


if __name__ == "__main__":
    main()
