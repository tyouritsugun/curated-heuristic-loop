"""GPU detection and llama-cpp wheel installation helpers."""
from __future__ import annotations

import ctypes
import json
import logging
import os
import platform
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import platform

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
GPU_STATE_PATH = DATA_DIR / "gpu_state.json"
LEGACY_GPU_STATE_PATH = Path(__file__).resolve().parents[1] / "data" / "gpu_state.json"

SUPPORTED_GPU_BACKENDS = ("metal", "cuda", "rocm", "cpu")
DEFAULT_GPU_PRIORITY = ("metal", "cuda", "rocm", "cpu")

CUDA_COMPAT_MATRIX = [
    ((11, 8), "cu118"),
    ((12, 0), "cu120"),
    ((12, 1), "cu121"),
    ((12, 2), "cu122"),
    ((12, 4), "cu124"),
    ((12, 5), "cu125"),
]

ROCM_COMPAT_MATRIX = [
    ((5, 6), "rocm5.6"),
    ((5, 7), "rocm5.7"),
    ((6, 0), "rocm6.0"),
]

_DEFAULT_WHEEL_BASE = "https://abetlen.github.io/llama-cpp-python/whl"
LLAMA_CPP_WHEEL_BASE = os.getenv("CHL_GPU_WHEEL_BASE", _DEFAULT_WHEEL_BASE).rstrip("/")
PYPI_SIMPLE_INDEX = os.getenv("CHL_PYPI_SIMPLE_INDEX", "https://pypi.org/simple")
NVIDIA_DRIVER_URL = "https://www.nvidia.com/Download/index.aspx"
CUDA_TOOLKIT_URL = "https://developer.nvidia.com/cuda-downloads"
APPLE_CLT_URL = "https://developer.apple.com/xcode/resources/"
ROCM_URL = "https://rocm.docs.amd.com/en/latest/how_to/install_guide/index.html"


class GPUInstallerError(RuntimeError):
    """Raised when GPU detection or installation encounters an unrecoverable error."""


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _today_iso() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def parse_gpu_priority(value: Optional[str]) -> List[str]:
    if not value:
        ordered = list(DEFAULT_GPU_PRIORITY)
    else:
        tokens = [token.strip().lower() for token in value.split(",") if token.strip()]
        ordered = []
        for token in tokens:
            if token in SUPPORTED_GPU_BACKENDS and token not in ordered:
                ordered.append(token)
        for backend in DEFAULT_GPU_PRIORITY:
            if backend not in ordered:
                ordered.append(backend)
    return ordered


def load_gpu_state(path: Optional[Path] = None) -> Optional[Dict]:
    primary = path or GPU_STATE_PATH
    candidates = [primary]
    if LEGACY_GPU_STATE_PATH not in candidates:
        candidates.append(LEGACY_GPU_STATE_PATH)

    for candidate in candidates:
        if not candidate.exists():
            continue
        try:
            with candidate.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
            if not isinstance(data, dict):
                continue
            if candidate != primary:
                try:
                    save_gpu_state(data, primary)
                except Exception:
                    logger.debug("Failed to migrate gpu_state.json from %s", candidate, exc_info=True)
            return data
        except json.JSONDecodeError as exc:
            logger.warning("Invalid GPU state file %s: %s", candidate, exc)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to read GPU state file %s: %s", candidate, exc)
    return None


def save_gpu_state(state: Dict, path: Optional[Path] = None) -> None:
    path = path or GPU_STATE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2)


def _parse_version_tuple(raw: Optional[str]) -> Optional[Tuple[int, int]]:
    if not raw:
        return None
    match = re.search(r"(\d+)\.(\d+)", raw)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def determine_cuda_wheel(version: Optional[str]) -> Tuple[str, List[str]]:
    diagnostics: List[str] = []
    version_tuple = _parse_version_tuple(version)
    if not version_tuple:
        diagnostics.append("CUDA version unavailable; defaulting to cu125 wheel")
        return CUDA_COMPAT_MATRIX[-1][1], diagnostics

    for boundary, suffix in reversed(CUDA_COMPAT_MATRIX):
        if version_tuple >= boundary:
            diagnostics.append(f"Detected CUDA {version}; using {suffix}")
            return suffix, diagnostics

    diagnostics.append(
        f"Detected CUDA {version} older than supported set; using {CUDA_COMPAT_MATRIX[0][1]}"
    )
    return CUDA_COMPAT_MATRIX[0][1], diagnostics


def determine_rocm_wheel(version: Optional[str]) -> Tuple[str, List[str]]:
    diagnostics: List[str] = []
    version_tuple = _parse_version_tuple(version)
    if not version_tuple:
        diagnostics.append("ROCm version unavailable; defaulting to rocm6.0 wheel")
        return ROCM_COMPAT_MATRIX[-1][1], diagnostics

    for boundary, suffix in reversed(ROCM_COMPAT_MATRIX):
        if version_tuple >= boundary:
            if version_tuple != boundary:
                diagnostics.append(
                    f"Detected ROCm {version} not in compatibility table; using {suffix}"
                )
            return suffix, diagnostics

    diagnostics.append(
        f"Detected ROCm {version} older than supported set; using {ROCM_COMPAT_MATRIX[0][1]}"
    )
    return ROCM_COMPAT_MATRIX[0][1], diagnostics


def detect_metal_backend() -> Optional[Dict]:
    diagnostics: List[str] = []
    system = platform.system()
    machine = platform.machine().lower()
    if system != "Darwin":
        return None
    if machine not in {"arm64", "aarch64"}:
        diagnostics.append("Metal requires Apple Silicon (arm64)")
        return None
    mac_version = platform.mac_ver()[0] or "unknown"
    return {
        "backend": "metal",
        "version": mac_version,
        "wheel": "metal",
        "diagnostics": diagnostics,
        "detected_via": "platform",
    }


def detect_cuda_backend() -> Optional[Dict]:
    diagnostics: List[str] = []
    detected = False
    cuda_version: Optional[str] = None
    driver_version: Optional[str] = None
    smi_path = shutil.which("nvidia-smi")
    if smi_path:
        try:
            result = subprocess.run(
                [smi_path],
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )
            if result.returncode == 0:
                detected = True
                cuda_match = re.search(r"CUDA Version:\s*([0-9.]+)", result.stdout)
                driver_match = re.search(r"Driver Version:\s*([0-9.]+)", result.stdout)
                if cuda_match:
                    cuda_version = cuda_match.group(1)
                if driver_match:
                    driver_version = driver_match.group(1)
            else:
                diagnostics.append(
                    f"nvidia-smi exited with code {result.returncode}: {result.stderr.strip()}"
                )
        except subprocess.TimeoutExpired:
            diagnostics.append("nvidia-smi timed out while probing GPU")
        except FileNotFoundError:
            pass

    if not detected:
        version_path = Path("/proc/driver/nvidia/version")
        if version_path.exists():
            try:
                driver_version = version_path.read_text(encoding="utf-8", errors="ignore").strip()
            except Exception:  # noqa: BLE001 – best effort
                driver_version = None
            detected = True
            diagnostics.append("Detected NVIDIA driver via /proc/driver/nvidia/version")

    if not detected:
        dev_path = Path("/dev")
        if dev_path.exists():
            for candidate in dev_path.glob("nvidia*"):
                detected = True
                diagnostics.append(f"Detected NVIDIA device node: {candidate}")
                break

    if not detected and os.getenv("NVIDIA_VISIBLE_DEVICES"):
        detected = True
        diagnostics.append("NVIDIA_VISIBLE_DEVICES present (container GPU passthrough)")

    if not detected:
        try:
            ctypes.CDLL("libcuda.so")
            detected = True
            diagnostics.append("Loaded libcuda.so via ctypes")
        except OSError:
            pass

    nvcc_version = None
    nvcc_path = shutil.which("nvcc")
    if not nvcc_path:
        candidates = []
        for env_var in ("CUDA_HOME", "CUDA_PATH"):
            base = os.getenv(env_var)
            if base:
                candidates.append(Path(base) / "bin" / "nvcc")
        candidates.extend(
            [
                Path("/usr/local/cuda/bin/nvcc"),
                Path("/opt/cuda/bin/nvcc"),
                Path("/usr/bin/nvcc"),
            ]
        )
        for candidate in candidates:
            if candidate.exists():
                nvcc_path = str(candidate)
                break

    if nvcc_path:
        try:
            result = subprocess.run(
                [nvcc_path, "--version"],
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )
            match = re.search(r"release\s+([0-9.]+)", result.stdout)
            if match:
                nvcc_version = match.group(1)
        except subprocess.TimeoutExpired:
            diagnostics.append("nvcc --version timed out while probing CUDA toolkit")
        except FileNotFoundError:
            pass

    suffix, compat_diagnostics = determine_cuda_wheel(cuda_version or nvcc_version)
    diagnostics.extend(compat_diagnostics)

    return {
        "backend": "cuda",
        "version": cuda_version or nvcc_version or "unknown",
        "driver_version": driver_version,
        "wheel": suffix,
        "diagnostics": diagnostics,
        "detected_via": "nvidia-smi" if smi_path else "heuristics",
    }


def detect_rocm_backend() -> Optional[Dict]:
    diagnostics: List[str] = []
    rocm_version = None

    rocm_smi = shutil.which("rocm-smi")
    if rocm_smi:
        try:
            result = subprocess.run(
                [rocm_smi, "--showdriverversion"],
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )
            match = re.search(r"Driver version:\s*([0-9.]+)", result.stdout)
            if match:
                rocm_version = match.group(1)
        except subprocess.TimeoutExpired:
            diagnostics.append("rocm-smi timed out while probing ROCm")
        except FileNotFoundError:
            pass

    if not rocm_version:
        hipcc_path = shutil.which("hipcc")
        if hipcc_path:
            try:
                result = subprocess.run(
                    [hipcc_path, "--version"],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=5,
                )
                match = re.search(r"HIP version:\s*([0-9.]+)", result.stdout)
                if match:
                    rocm_version = match.group(1)
            except subprocess.TimeoutExpired:
                diagnostics.append("hipcc --version timed out while probing ROCm toolkit")
            except FileNotFoundError:
                pass

    suffix, compat_diagnostics = determine_rocm_wheel(rocm_version)
    diagnostics.extend(compat_diagnostics)

    if not rocm_version:
        diagnostics.append("ROCm version not detected; assuming ROCm 6.0+ environment")

    return {
        "backend": "rocm",
        "version": rocm_version or "unknown",
        "wheel": suffix,
        "diagnostics": diagnostics,
        "detected_via": "rocm-smi" if rocm_smi else "heuristics",
    }


def detect_cpu_backend() -> Dict:
    diagnostics: List[str] = []
    system = platform.system()
    machine = platform.machine().lower()
    if system == "Darwin" and machine in {"arm64", "aarch64"}:
        diagnostics.append("No GPU backend detected; using CPU on Apple Silicon")
    elif system == "Linux":
        diagnostics.append("No GPU backend detected; using CPU on Linux")
    else:
        diagnostics.append(f"No GPU backend detected; using CPU on {system}/{machine}")
    return {
        "backend": "cpu",
        "version": platform.python_version(),
        "wheel": "cpu",
        "diagnostics": diagnostics,
        "detected_via": "fallback",
    }


def detect_gpu_backends() -> Dict[str, Dict]:
    """Detect available GPU backends with diagnostics."""
    backends: Dict[str, Dict] = {}

    metal_info = detect_metal_backend()
    if metal_info:
        backends["metal"] = metal_info

    cuda_info = detect_cuda_backend()
    if cuda_info:
        backends["cuda"] = cuda_info

    rocm_info = detect_rocm_backend()
    if rocm_info:
        backends["rocm"] = rocm_info

    if not backends:
        backends["cpu"] = detect_cpu_backend()

    return backends


def _build_pip_command(extra_index_url: Optional[str] = None) -> Tuple[str, List[str]]:
    """Return base pip command and environment for invoking pip.

    For simplicity, we delegate to the current Python's pip via -m pip.
    """
    python_executable = sys.executable
    env = os.environ.copy()
    if extra_index_url:
        env["PIP_EXTRA_INDEX_URL"] = extra_index_url
    return python_executable, ["-m", "pip"], env


def install_llama_cpp(
    backend: str,
    wheel_suffix: str,
    *,
    index_url: Optional[str] = None,
    extra_index_url: Optional[str] = None,
) -> Dict[str, Any]:
    """Install llama-cpp-python wheel for the selected backend."""
    if backend not in SUPPORTED_GPU_BACKENDS:
        raise GPUInstallerError(f"Unsupported backend: {backend}")

    # Construct wheel URL
    if backend == "cpu":
        wheel_url = f"{LLAMA_CPP_WHEEL_BASE}/llama-cpp-python/cpu/llama_cpp_python-{wheel_suffix}-cp39-abi3-manylinux_x86_64.whl"
    elif backend == "metal":
        wheel_url = f"{LLAMA_CPP_WHEEL_BASE}/llama-cpp-python/metal/llama_cpp_python-{wheel_suffix}-cp39-abi3-macosx_11_0_arm64.whl"
    elif backend == "cuda":
        wheel_url = f"{LLAMA_CPP_WHEEL_BASE}/llama-cpp-python/cuda/llama_cpp_python-{wheel_suffix}-cp39-abi3-manylinux_x86_64.whl"
    elif backend == "rocm":
        wheel_url = f"{LLAMA_CPP_WHEEL_BASE}/llama-cpp-python/rocm/llama_cpp_python-{wheel_suffix}-cp39-abi3-manylinux_x86_64.whl"
    else:
        raise GPUInstallerError(f"Unsupported backend: {backend}")

    python_executable, pip_args, env = _build_pip_command(extra_index_url)

    cmd = [python_executable, *pip_args, "install", "--upgrade"]
    if index_url:
        cmd.extend(["--index-url", index_url])
    cmd.append(wheel_url)

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
    except Exception as exc:
        raise GPUInstallerError(f"Failed to invoke pip: {exc}") from exc

    if result.returncode != 0:
        raise GPUInstallerError(
            f"pip install failed with code {result.returncode}: {result.stderr.strip()}"
        )

    return {
        "command": " ".join(cmd),
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def record_gpu_detection(backends: Dict[str, Dict]) -> Dict[str, Any]:
    """Persist GPU detection result to gpu_state.json."""
    state = {
        "detected_at": _utcnow_iso(),
        "backends": backends,
    }
    save_gpu_state(state)
    return state


def load_or_detect_backends(force_refresh: bool = False) -> Dict[str, Dict]:
    """Load GPU backends from cache or detect fresh."""
    if not force_refresh:
        state = load_gpu_state()
        if state and isinstance(state.get("backends"), dict):
            return state["backends"]
    backends = detect_gpu_backends()
    record_gpu_detection(backends)
    return backends


def recommend_backend(priority: Optional[str] = None) -> Dict[str, Any]:
    """Recommend the best backend based on detection and priority."""
    ordered = parse_gpu_priority(priority or os.getenv("CHL_GPU_PRIORITY"))
    backends = load_or_detect_backends()
    diagnostics: List[str] = []

    for candidate in ordered:
        info = backends.get(candidate)
        if not info:
            continue
        diagnostics.append(f"Selected backend {candidate} (detected: {info.get('diagnostics')})")
        return {
            "backend": candidate,
            "info": info,
            "priority": ordered,
            "detected_backends": backends,
            "diagnostics": diagnostics,
        }

    # Fallback: should not happen because detect_gpu_backends() always returns at least cpu
    cpu_info = backends.get("cpu") or detect_cpu_backend()
    diagnostics.append("No preferred backend available; falling back to CPU")
    return {
        "backend": "cpu",
        "info": cpu_info,
        "priority": ordered,
        "detected_backends": backends,
        "diagnostics": diagnostics,
    }


__all__ = [
    "GPUInstallerError",
    "parse_gpu_priority",
    "load_gpu_state",
    "save_gpu_state",
    "determine_cuda_wheel",
    "determine_rocm_wheel",
    "detect_metal_backend",
    "detect_cuda_backend",
    "detect_rocm_backend",
    "detect_cpu_backend",
    "detect_gpu_backends",
    "install_llama_cpp",
    "record_gpu_detection",
    "load_or_detect_backends",
    "recommend_backend",
]
