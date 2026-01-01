#!/usr/bin/env python
"""Environment diagnostics for CHL API server - NVIDIA CUDA backend (HF stack).

This script inspects NVIDIA GPU hardware and CUDA toolchain readiness for the
HF Transformers stack (Torch CUDA). It must be run with the API server stopped.
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
from typing import Any, Dict, Optional, Tuple


def _extend_sys_path() -> None:
    """Ensure src/ is importable."""
    import sys
    from pathlib import Path

    project_root = Path(__file__).resolve().parents[2]
    root_str = str(project_root)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)
    from src.common.config.config import ensure_project_root_on_sys_path  # noqa: E402
    ensure_project_root_on_sys_path()


_extend_sys_path()

from src.common.config.config import (  # noqa: E402
    DATA_DIR,
    MODEL_SELECTION_PATH,
    RUNTIME_CONFIG_PATH,
    save_model_selection,
    save_runtime_config,
)
from src.api.services import gpu_installer  # noqa: E402


logger = logging.getLogger(__name__)

SUPPORT_PROMPT_PATH = DATA_DIR / "support_prompt.txt"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check NVIDIA CUDA environment and llama-cpp-python compatibility before API setup.",
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
    log_level = os.getenv("CHL_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, log_level, logging.INFO)
    if verbose:
        level = logging.DEBUG
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
              python scripts/setup/check_api_env_nvidia.py --force
            """
        ).strip()
        print(msg)
        sys.exit(1)


def _detect_runtime_config() -> Dict[str, Any]:
    # Force CUDA backend for NVIDIA-specific script
    priority = ["cuda", "cpu"]
    backend_override = "cuda"
    config, cached = gpu_installer.ensure_runtime_config(priority, backend_override, force_detect=True)
    logger.debug("Runtime config (cached=%s): %s", cached, config)
    return config


def _recommend_models(backend: str, vram_gb: Optional[float]) -> Dict[str, str]:
    """Recommend HF embedding/reranker models based on backend and VRAM."""
    EMB_SMALL = ("Qwen/Qwen3-Embedding-0.6B", "fp16")
    EMB_MED = ("Qwen/Qwen3-Embedding-4B", "fp16")
    RER_SMALL = ("Qwen/Qwen3-Reranker-0.6B", "fp16")
    RER_MED = ("Qwen/Qwen3-Reranker-4B", "fp16")

    if backend == "cpu" or vram_gb is None:
        emb_repo, emb_quant = EMB_SMALL
        rer_repo, rer_quant = RER_SMALL
    else:
        if vram_gb >= 20.0:
            emb_repo, emb_quant = EMB_MED
            rer_repo, rer_quant = RER_MED
        elif vram_gb >= 12.0:
            emb_repo, emb_quant = EMB_MED
            rer_repo, rer_quant = RER_SMALL  # save VRAM on reranker
        elif vram_gb >= 8.0:
            emb_repo, emb_quant = EMB_SMALL
            rer_repo, rer_quant = RER_MED
        else:
            emb_repo, emb_quant = EMB_SMALL
            rer_repo, rer_quant = RER_SMALL

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

    save_model_selection(selection)
    logger.info("Saved model selection to %s", MODEL_SELECTION_PATH)


def _save_support_prompt(prompt: str) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with SUPPORT_PROMPT_PATH.open("w", encoding="utf-8") as fh:
        fh.write(prompt)
    logger.info("Saved support prompt to %s", SUPPORT_PROMPT_PATH)


def main() -> None:
    args = _parse_args()
    _configure_logging(args.verbose)

    _ensure_api_stopped(args.api_url, args.force)

    print("Running CHL environment diagnostics (NVIDIA CUDA)...")
    runtime_config = _detect_runtime_config()
    backend = runtime_config.get("backend", "cpu")

    # Verify this is actually CUDA backend
    if backend != "cuda":
        print(f"\n✗ This script is for NVIDIA CUDA only, but detected backend: {backend}")
        print("  Please use the appropriate check_api_env script for your platform.")
        sys.exit(1)

    vram_info = gpu_installer.get_vram_info(runtime_config)
    prereq = gpu_installer.prerequisite_check(runtime_config)
    # Torch CUDA path: wheel metadata/llama verification not needed
    suffix = gpu_installer.recommended_wheel_suffix(runtime_config)

    verify_log: Optional[str] = None

    # Decide overall success (prereqs only)
    prereq_status = prereq.get("status") if isinstance(prereq, dict) else "unknown"
    prereq_ok = prereq_status in {"ok", "warn"}
    overall_ok = prereq_ok

    if overall_ok:
        vram_gb = vram_info.get("vram_gb") if vram_info else None
        selection = _recommend_models(backend, vram_gb)
        _save_model_selection(selection)

        print("\n✓ Environment diagnostics completed successfully.\n")
        print(f"  - Detected backend: {backend}")
        if vram_info:
            print(f"  - VRAM/System memory: {vram_info['vram_gb']} GB (via {vram_info['method']})")
        if suffix:
            print(f"  - Recommended CUDA wheel suffix: {suffix}")
        print("  - Recommended models:")
        print(f"      Embedding: {selection['embedding_repo']} [{selection['embedding_quant']}]")
        print(f"      Reranker:  {selection['reranker_repo']} [{selection['reranker_quant']}]")

        print("\nRuntime configuration saved to data/runtime_config.json")

        sys.exit(0)

    # Failure path: build support prompt and exit 1
    prompt = gpu_installer.build_support_prompt(runtime_config, prereq, verify_log=verify_log)
    _save_support_prompt(prompt)

    print("\n✗ Environment diagnostics found blocking issues.\n")
    if not prereq_ok:
        print(f"  - Prerequisite status: {prereq_status}")
        issues = prereq.get("issues") or []
        if issues:
            print("  - Issues:")
            for issue in issues:
                print(f"      - {issue}")
    print("\nA detailed troubleshooting prompt has been written to "
          f"{SUPPORT_PROMPT_PATH}. Copy its contents into ChatGPT/Claude and "
          "follow the instructions to resolve the environment issues.")

    sys.exit(1)


if __name__ == "__main__":
    main()
