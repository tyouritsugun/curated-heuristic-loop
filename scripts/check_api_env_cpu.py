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
from pathlib import Path
from typing import Any, Dict, Optional


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
MODEL_SELECTION_PATH = DATA_DIR / "model_selection.json"
SUPPORT_PROMPT_PATH = DATA_DIR / "support_prompt.txt"


def _extend_sys_path() -> None:
    """Ensure src/ is importable."""
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))


_extend_sys_path()

from src.api.services import gpu_installer  # noqa: E402


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


def _detect_gpu_state() -> Dict[str, Any]:
    # Force CPU backend for CPU-only script
    priority = ["cpu"]
    backend_override = "cpu"
    state, cached = gpu_installer.ensure_gpu_state(priority, backend_override, force_detect=True)
    logger.debug("GPU state (cached=%s): %s", cached, state)
    return state


def _recommend_models(backend: str, vram_gb: Optional[float]) -> Dict[str, str]:
    """Recommend embedding/reranker models for CPU-only mode."""
    # CPU-only mode uses small models for efficiency
    EMB_SMALL = ("Qwen/Qwen3-Embedding-0.6B-GGUF", "Q8_0")
    RER_SMALL_Q8 = ("Mungert/Qwen3-Reranker-0.6B-GGUF", "Q8_0")

    emb_repo, emb_quant = EMB_SMALL
    rer_repo, rer_quant = RER_SMALL_Q8

    return {
        "embedding_repo": emb_repo,
        "embedding_quant": emb_quant,
        "reranker_repo": rer_repo,
        "reranker_quant": rer_quant,
    }


def _save_model_selection(selection: Dict[str, str]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        existing: Optional[Dict[str, Any]] = None
        if MODEL_SELECTION_PATH.exists():
            with MODEL_SELECTION_PATH.open("r", encoding="utf-8") as fh:
                existing = json.load(fh)
        if isinstance(existing, dict) and all(existing.get(k) == v for k, v in selection.items()):
            logger.info("Model selection unchanged; keeping existing %s", MODEL_SELECTION_PATH)
            return
    except (json.JSONDecodeError, OSError):
        existing = None

    with MODEL_SELECTION_PATH.open("w", encoding="utf-8") as fh:
        json.dump(selection, fh, indent=2)
    logger.info("Saved model selection to %s", MODEL_SELECTION_PATH)


def main() -> None:
    args = _parse_args()
    _configure_logging(args.verbose)

    _ensure_api_stopped(args.api_url, args.force)

    print("Running CHL environment diagnostics (CPU-only mode)...")
    gpu_state = _detect_gpu_state()
    backend = gpu_state.get("backend", "cpu")

    vram_info = gpu_installer.get_vram_info(gpu_state)
    prereq = gpu_installer.prerequisite_check(gpu_state)

    # CPU mode doesn't need wheel metadata checks
    prereq_status = prereq.get("status") if isinstance(prereq, dict) else "unknown"
    prereq_ok = prereq_status in {"ok", "warn"}

    if prereq_ok:
        vram_gb = vram_info.get("vram_gb") if vram_info else None
        selection = _recommend_models(backend, vram_gb)
        _save_model_selection(selection)

        print("\n✓ Environment diagnostics completed successfully.\n")
        print(f"  - Detected backend: {backend}")
        if vram_info:
            print(f"  - System memory: {vram_info['vram_gb']} GB (via {vram_info['method']})")
        print("  - CPU-only mode: No GPU dependencies required")
        print("  - Recommended models:")
        print(f"      Embedding: {selection['embedding_repo']} [{selection['embedding_quant']}]")
        print(f"      Reranker:  {selection['reranker_repo']} [{selection['reranker_quant']}]")

        print("\nSuggested CHL_SEARCH_MODE: cpu (CPU-only)")
        print("\nNote: CPU mode uses SQLite text search (LIKE queries) instead of semantic similarity.")

        sys.exit(0)

    # Failure path (unlikely for CPU mode, but included for completeness)
    print("\n✗ Environment diagnostics found issues.\n")
    print(f"  - Prerequisite status: {prereq_status}")
    issues = prereq.get("issues") or []
    if issues:
        print("  - Issues:")
        for issue in issues:
            print(f"      - {issue}")

    print("\nPlease resolve the issues above before proceeding with installation.")

    sys.exit(1)


if __name__ == "__main__":
    main()
