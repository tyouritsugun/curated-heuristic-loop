"""GPU detection and llama-cpp wheel installation helpers."""
from __future__ import annotations

import ctypes
import io
import json
import logging
import os
import platform
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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


# ---------------------------------------------------------------------------
# VRAM and environment diagnostics
# ---------------------------------------------------------------------------


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

    if platform.system() == "Windows":
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


def _get_nvidia_vram_gb() -> Optional[float]:
    """Return total VRAM (GB) for the largest NVIDIA GPU, if detectable."""
    smi_path = shutil.which("nvidia-smi")
    if not smi_path:
        return None

    cmd = [smi_path, "--query-gpu=memory.total", "--format=csv,noheader,nounits"]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None

    if result.returncode != 0:
        return None

    values: List[float] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            mib = float(line.split()[0])
            values.append(mib / 1024.0)
        except (ValueError, IndexError):
            continue

    return max(values) if values else None


def _get_metal_vram_gb() -> Optional[float]:
    """Estimate VRAM on Apple Silicon as 70% of unified memory."""
    if platform.system() != "Darwin":
        return None
    try:
        result = subprocess.run(
            ["sysctl", "hw.memsize"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None

    if result.returncode != 0:
        return None

    match = re.search(r"hw\.memsize:\s*(\d+)", result.stdout)
    if not match:
        return None
    try:
        bytes_total = float(match.group(1))
    except ValueError:
        return None
    # Heuristic: assume ~70% of unified memory is realistically usable for GPU workloads
    return (bytes_total / (1024 ** 3)) * 0.7


def _get_rocm_vram_gb() -> Optional[float]:
    """Best-effort VRAM detection for ROCm via rocm-smi."""
    rocm_smi = shutil.which("rocm-smi")
    if not rocm_smi:
        return None

    try:
        result = subprocess.run(
            [rocm_smi, "--showmeminfo", "vram"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None

    if result.returncode != 0:
        return None

    values: List[float] = []
    for line in result.stdout.splitlines():
        if "Total" not in line and "total" not in line.lower():
            continue
        numbers = re.findall(r"(\d+)", line)
        if not numbers:
            continue
        try:
            mb = float(numbers[0])
            values.append(mb / 1024.0)
        except ValueError:
            continue
    return max(values) if values else None


def get_vram_info(gpu_state: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """Return VRAM information for the selected backend.

    When gpu_state is provided, its ``backend`` field is used to decide which
    VRAM method to call. If no GPU backend is selected or nothing can be
    detected, returns None.
    """
    backend = (gpu_state or {}).get("backend")
    if not backend or backend == "cpu":
        mem_gb = _get_system_memory_gb()
        if mem_gb is None:
            return None
        return {
            "backend": "cpu",
            "vram_gb": float(f"{mem_gb:.2f}"),
            "method": "system_memory",
        }

    if backend == "cuda":
        vram = _get_nvidia_vram_gb()
        method = "nvidia-smi"
    elif backend == "metal":
        vram = _get_metal_vram_gb()
        method = "sysctl_hw_memsize"
    elif backend == "rocm":
        vram = _get_rocm_vram_gb()
        method = "rocm-smi"
    else:
        return None

    if vram is None:
        return None

    return {
        "backend": backend,
        "vram_gb": float(f"{vram:.2f}"),
        "method": method,
    }


def ensure_gpu_state(
    priority: List[str],
    backend_override: Optional[str],
    force_detect: bool,
    *,
    state_path: Optional[Path] = None,
) -> Tuple[Dict[str, Any], bool]:
    """Detect and persist GPU state, returning a summarized view.

    Returns (state, cached) where ``cached`` indicates whether an existing
    gpu_state.json was reused.
    """
    state_path = state_path or GPU_STATE_PATH

    if not force_detect:
        existing = load_gpu_state(state_path)
        if isinstance(existing, dict) and existing.get("backend"):
            return existing, True

    backends = detect_gpu_backends()
    record = {
        "detected_at": _utcnow_iso(),
        "backends": backends,
        "priority": priority,
        "backend_override": backend_override,
        "status": "detected",
    }

    # Honour override if valid, otherwise use recommended backend
    if backend_override and backend_override in backends:
        selected_backend = backend_override
        selected_info = backends[backend_override]
        diag_prefix = f"Using backend override {backend_override}"
    else:
        recommended = recommend_backend(",".join(priority) if priority else None)
        selected_backend = recommended["backend"]
        selected_info = recommended["info"]
        diag_prefix = f"Selected backend {selected_backend} via priority"

    diagnostics: List[str] = [diag_prefix]
    diagnostics.extend(selected_info.get("diagnostics") or [])

    record.update(
        {
            "backend": selected_backend,
            "version": selected_info.get("version") or "unknown",
            "driver_version": selected_info.get("driver_version"),
            "wheel": selected_info.get("wheel"),
            "diagnostics": diagnostics,
        }
    )

    save_gpu_state(record, path=state_path)
    return record, False


def prerequisite_check(gpu_state: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Validate GPU prerequisites and toolchains."""
    if not gpu_state:
        return {
            "status": "ok",
            "message": "No GPU state available; treating environment as CPU-only.",
            "issues": [],
        }

    backend = gpu_state.get("backend", "cpu")
    issues: List[str] = []

    if backend == "cpu":
        return {
            "status": "ok",
            "message": "CPU-only environment detected; GPU prerequisites are skipped.",
            "issues": [],
        }

    system = platform.system()
    status = "ok"

    if backend == "metal":
        if system != "Darwin":
            issues.append(f"Metal backend selected but system is {system}, expected macOS.")
        machine = platform.machine().lower()
        if machine not in {"arm64", "aarch64"}:
            issues.append(f"Metal requires Apple Silicon (arm64); detected {machine}.")
        if not shutil.which("cmake"):
            issues.append("CMake is not installed or not on PATH (required for llama-cpp build).")
        if issues:
            status = "error"
        message = "Apple Metal prerequisites validation completed."

    elif backend == "cuda":
        if system not in {"Linux", "Windows"}:
            issues.append(f"NVIDIA CUDA backend on unsupported OS: {system}.")
        if not shutil.which("nvidia-smi"):
            issues.append("nvidia-smi not found; NVIDIA drivers may not be installed or on PATH.")
        if not shutil.which("nvcc"):
            issues.append("nvcc not found; CUDA toolkit may not be installed or configured.")
        if not shutil.which("cmake"):
            issues.append("CMake is not installed or not on PATH (required for llama-cpp build).")
        if issues:
            status = "error"
        message = "CUDA prerequisites validation completed."

    elif backend == "rocm":
        if system != "Linux":
            issues.append(f"ROCm backend requires Linux; detected {system}.")
        if not shutil.which("rocm-smi") and not shutil.which("hipcc"):
            issues.append("Neither rocm-smi nor hipcc found; ROCm stack may not be installed.")
        if not shutil.which("cmake"):
            issues.append("CMake is not installed or not on PATH (required for llama-cpp build).")
        if issues:
            status = "error"
        message = "ROCm prerequisites validation completed."

    else:
        message = f"Unknown backend '{backend}' – treating as CPU-only."
        status = "warn"

    return {
        "status": status,
        "message": message,
        "issues": issues,
    }


def recommended_wheel_suffix(gpu_state: Optional[Dict[str, Any]]) -> Optional[str]:
    """Return the recommended wheel suffix for the selected backend."""
    if not gpu_state:
        return None
    backend = gpu_state.get("backend", "cpu")
    wheel = gpu_state.get("wheel")
    if backend == "cpu":
        return None
    return wheel


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


def record_gpu_detection(backends: Dict[str, Dict], *, state_path: Optional[Path] = None) -> Dict[str, Any]:
    """Persist GPU detection result to gpu_state.json."""
    state = {
        "detected_at": _utcnow_iso(),
        "backends": backends,
    }
    save_gpu_state(state, path=state_path)
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


def _import_llama_cpp() -> Tuple[bool, str]:
    """Try to import llama_cpp and return (success, log)."""
    try:
        import llama_cpp  # type: ignore[import]

        parts = [f"Imported llama_cpp version {getattr(llama_cpp, '__version__', 'unknown')}."]
        try:
            from llama_cpp import Llama  # type: ignore[import]

            if Llama:
                parts.append("Llama class is available.")
        except Exception as exc:  # noqa: BLE001
            parts.append(f"Could not import Llama class: {exc}")
        return True, "\n".join(parts)
    except Exception as exc:  # noqa: BLE001
        return False, f"Failed to import llama_cpp: {exc}"


def verify_llama_install(gpu_state: Optional[Dict[str, Any]]) -> Tuple[bool, str]:
    """Verify that llama-cpp-python can be imported for the selected backend."""
    if not gpu_state:
        return False, "No GPU state available; cannot verify llama-cpp installation."

    backend = gpu_state.get("backend", "cpu")
    ok, log = _import_llama_cpp()
    if backend == "cpu":
        # In CPU mode, llama-cpp is optional; propagate the result as-is.
        return ok, log
    return ok, log


def get_wheel_metadata(backend: str, suffix: str) -> Dict[str, Any]:
    """Fetch wheel metadata from the configured wheel index.

    This requires network access and raises GPUInstallerError on failure.
    """
    backend = backend.lower()
    if backend not in {"cpu", "metal", "cuda", "rocm"}:
        raise GPUInstallerError(f"Unsupported backend for wheel metadata: {backend}")

    index_url = f"{LLAMA_CPP_WHEEL_BASE}/llama-cpp-python/{backend}/"

    try:
        with urllib.request.urlopen(index_url, timeout=10) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise GPUInstallerError(f"Failed to fetch wheel index from {index_url}: {exc}") from exc

    wheel_href: Optional[str] = None
    pattern = re.compile(r'href=[\'"](?P<href>[^\'"]*llama_cpp_python-[^\'"]+\.whl)[\'"]')
    for match in pattern.finditer(html):
        href = match.group("href")
        if f"-{suffix}-" in href:
            wheel_href = href
            break
    if not wheel_href:
        raise GPUInstallerError(
            f"Could not find a llama_cpp_python wheel with suffix '{suffix}' in index {index_url}"
        )

    if wheel_href.startswith("http://") or wheel_href.startswith("https://"):
        wheel_url = wheel_href
    else:
        # Simple relative URL handling
        if wheel_href.startswith("/"):
            wheel_url = index_url.rstrip("/") + wheel_href
        else:
            wheel_url = index_url.rstrip("/") + "/" + wheel_href

    try:
        with urllib.request.urlopen(wheel_url, timeout=30) as resp:
            wheel_bytes = resp.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise GPUInstallerError(f"Failed to download wheel {wheel_url}: {exc}") from exc

    size_mb = len(wheel_bytes) / (1024 * 1024)

    python_requires = ""
    platforms: List[str] = []
    try:
        with zipfile.ZipFile(io.BytesIO(wheel_bytes)) as zf:
            metadata_name = None
            for name in zf.namelist():
                if name.endswith(".dist-info/METADATA"):
                    metadata_name = name
                    break
            if metadata_name:
                with zf.open(metadata_name) as fh:
                    metadata_text = fh.read().decode("utf-8", errors="replace")
                for line in metadata_text.splitlines():
                    if line.startswith("Requires-Python:"):
                        python_requires = line.split(":", 1)[1].strip()
                    elif line.startswith("Tag:"):
                        tag = line.split(":", 1)[1].strip()
                        platforms.append(tag)
    except zipfile.BadZipFile as exc:
        raise GPUInstallerError(f"Downloaded wheel {wheel_url} is not a valid zip archive: {exc}") from exc

    return {
        "python_requires": python_requires,
        "platforms": platforms,
        "url": wheel_url,
        "size_mb": float(f"{size_mb:.2f}"),
    }


def build_support_prompt(
    gpu_state: Optional[Dict[str, Any]],
    prereq: Optional[Dict[str, Any]],
    verify_log: Optional[str] = None,
) -> str:
    """Generate a structured prompt for LLM-based troubleshooting."""
    backend = (gpu_state or {}).get("backend", "cpu")
    detected_at = (gpu_state or {}).get("detected_at", "unknown")
    diagnostics = (gpu_state or {}).get("diagnostics") or []
    wheel = (gpu_state or {}).get("wheel")
    wheel_meta = (gpu_state or {}).get("wheel_metadata") or {}
    wheel_error = (gpu_state or {}).get("wheel_metadata_error")

    system_info = {
        "os": platform.system(),
        "os_release": platform.release(),
        "machine": platform.machine(),
        "python": sys.version.replace("\n", " "),
    }

    lines: List[str] = []
    lines.append("You are helping me configure GPU acceleration for llama-cpp-python.")
    lines.append("")
    lines.append("## System Context")
    lines.append(f"- OS: {system_info['os']} {system_info['os_release']} ({system_info['machine']})")
    lines.append(f"- Python: {system_info['python']}")
    lines.append(f"- Detected backend: {backend}")
    lines.append(f"- Detection timestamp: {detected_at}")

    if diagnostics:
        lines.append("- Detector diagnostics:")
        for item in diagnostics:
            lines.append(f"  - {item}")

    lines.append("")
    lines.append("## Prerequisite Check")
    if prereq:
        lines.append(f"- Status: {prereq.get('status', 'unknown')}")
        if prereq.get("message"):
            lines.append(f"- Message: {prereq['message']}")
        issues = prereq.get("issues") or []
        if issues:
            lines.append("- Issues:")
            for issue in issues:
                lines.append(f"  - {issue}")
    else:
        lines.append("- Prerequisite checks were not available.")

    lines.append("")
    lines.append("## Wheel Requirements")
    if wheel and wheel_meta:
        lines.append(f"- Selected wheel suffix: {wheel}")
        lines.append(f"- Wheel URL: {wheel_meta.get('url', 'unknown')}")
        if wheel_meta.get("python_requires"):
            lines.append(f"- Requires-Python: {wheel_meta['python_requires']}")
        platforms = wheel_meta.get("platforms") or []
        if platforms:
            lines.append("- Supported tags:")
            for tag in platforms:
                lines.append(f"  - {tag}")
        size_mb = wheel_meta.get("size_mb")
        if size_mb is not None:
            lines.append(f"- Approximate size: {size_mb} MB")
    elif wheel_error:
        lines.append(f"- Failed to fetch wheel metadata: {wheel_error}")
    else:
        lines.append("- Wheel metadata is not available; network access to the wheel index may have failed.")

    lines.append("")
    lines.append("## Relevant Resources")
    lines.append(f"- NVIDIA drivers: {NVIDIA_DRIVER_URL}")
    lines.append(f"- CUDA Toolkit: {CUDA_TOOLKIT_URL}")
    lines.append(f"- Apple Command Line Tools: {APPLE_CLT_URL}")
    lines.append(f"- AMD ROCm install guide: {ROCM_URL}")

    lines.append("")
    lines.append("## Diagnostic Logs")
    if verify_log:
        lines.append("```")
        lines.append(verify_log)
        lines.append("```")
    else:
        lines.append("_No llama-cpp import logs were captured._")

    lines.append("")
    lines.append("## Goal")
    lines.append(
        "Help me install and configure the correct drivers, toolchains, and llama-cpp-python wheel "
        "for this machine so that GPU acceleration works reliably for this backend."
    )

    return "\n".join(lines)


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
    "get_vram_info",
    "record_gpu_detection",
    "load_or_detect_backends",
    "recommend_backend",
    "ensure_gpu_state",
    "prerequisite_check",
    "recommended_wheel_suffix",
    "verify_llama_install",
    "get_wheel_metadata",
    "build_support_prompt",
    "install_llama_cpp",
]
