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
            if version_tuple != boundary:
                diagnostics.append(
                    f"Detected CUDA {version} not in compatibility table; using {suffix}"
                )
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
                diagnostics.append(f"Found nvcc at {candidate}")
                break
    if nvcc_path:
        try:
            nvcc_result = subprocess.run(
                [nvcc_path, "--version"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if nvcc_result.returncode == 0:
                match = re.search(r"release\s+([0-9.]+)", nvcc_result.stdout)
                if match:
                    nvcc_version = match.group(1)
                    diagnostics.append(f"nvcc reports CUDA toolkit {nvcc_version}")
                    if cuda_version and cuda_version != nvcc_version:
                        diagnostics.append(
                            f"Using nvcc-reported version {nvcc_version} instead of nvidia-smi {cuda_version}"
                        )
                    cuda_version = nvcc_version
                else:
                    diagnostics.append("nvcc output missing version string")
            else:
                diagnostics.append(f"nvcc --version exited with {nvcc_result.returncode}")
        except subprocess.TimeoutExpired:
            diagnostics.append("nvcc --version timed out")

    if not detected and nvcc_version:
        detected = True
        diagnostics.append("Detected CUDA toolkit via nvcc")

    if not detected:
        return None

    wheel_suffix, wheel_notes = determine_cuda_wheel(cuda_version)
    diagnostics.extend(wheel_notes)
    return {
        "backend": "cuda",
        "version": cuda_version,
        "wheel": wheel_suffix,
        "driver_version": driver_version,
        "diagnostics": diagnostics,
        "detected_via": "nvidia-smi" if smi_path else "devices",
    }


def detect_rocm_backend() -> Optional[Dict]:
    diagnostics: List[str] = []
    detected = False
    rocm_version: Optional[str] = None
    rocminfo_path = shutil.which("rocminfo")
    if rocminfo_path:
        try:
            result = subprocess.run(
                [rocminfo_path],
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )
            if result.returncode == 0:
                detected = True
                match = re.search(r"ROCm\s+Version:\s*([0-9.]+)", result.stdout)
                if match:
                    rocm_version = match.group(1)
            else:
                diagnostics.append(
                    f"rocminfo exited with code {result.returncode}: {result.stderr.strip()}"
                )
        except subprocess.TimeoutExpired:
            diagnostics.append("rocminfo timed out while probing GPU")
        except FileNotFoundError:
            pass

    if not detected:
        dri_path = Path("/dev/dri")
        has_render_nodes = dri_path.exists() and any(dri_path.glob("render*"))
        if Path("/dev/kfd").exists() or has_render_nodes:
            detected = True
            diagnostics.append("Detected ROCm device nodes (/dev/kfd or /dev/dri/render*)")

    if not detected and (os.getenv("ROCM_VISIBLE_DEVICES") or os.getenv("HIP_VISIBLE_DEVICES")):
        detected = True
        diagnostics.append("ROCm visibility env vars present")

    if not detected:
        try:
            ctypes.CDLL("libhip_hcc.so")
            detected = True
            diagnostics.append("Loaded libhip_hcc.so via ctypes")
        except OSError:
            pass

    if not detected:
        return None

    wheel_suffix, wheel_notes = determine_rocm_wheel(rocm_version)
    diagnostics.extend(wheel_notes)
    return {
        "backend": "rocm",
        "version": rocm_version,
        "wheel": wheel_suffix,
        "diagnostics": diagnostics,
        "detected_via": "rocminfo" if rocminfo_path else "devices",
    }


def detect_cpu_backend() -> Dict:
    return {
        "backend": "cpu",
        "version": None,
        "wheel": "cpu",
        "diagnostics": ["No supported GPU backend detected; defaulting to CPU wheel"],
        "detected_via": "fallback",
    }


DETECTORS = {
    "metal": detect_metal_backend,
    "cuda": detect_cuda_backend,
    "rocm": detect_rocm_backend,
    "cpu": detect_cpu_backend,
}


def detect_gpu_backend(priority: List[str]) -> Dict:
    for backend in priority:
        detector = DETECTORS.get(backend)
        if not detector:
            continue
        result = detector()
        if result:
            return result
    return detect_cpu_backend()


def ensure_gpu_state(
    priority: List[str],
    backend_override: Optional[str],
    force_detect: bool,
    *,
    state_path: Optional[Path] = None,
) -> Tuple[Dict, bool]:
    path = state_path or GPU_STATE_PATH
    cached_state = load_gpu_state(path)
    if backend_override:
        backend_override = backend_override.lower()
        if backend_override not in SUPPORTED_GPU_BACKENDS:
            raise GPUInstallerError(
                f"Invalid backend override '{backend_override}'. Supported: {', '.join(SUPPORTED_GPU_BACKENDS)}"
            )
        state = {
            "backend": backend_override,
            "version": None,
            "wheel": "cpu" if backend_override == "cpu" else backend_override,
            "status": "override",
            "override": backend_override,
            "priority": priority,
            "detected_at": _utcnow_iso(),
            "verified_at": None,
            "diagnostics": ["Override set via CLI/ENV"],
        }
        save_gpu_state(state, path)
        return state, False

    if cached_state and not force_detect:
        cached_priority = cached_state.get("priority")
        if isinstance(cached_priority, list) and cached_priority != priority:
            logger.info(
                "GPU priority changed from %s to %s; re-running detection",
                cached_priority,
                priority,
            )
        else:
            cached_state.setdefault("status", "cached")
            cached_state.setdefault("priority", priority)
            return cached_state, True

    result = detect_gpu_backend(priority)
    state = {
        "backend": result.get("backend", "cpu"),
        "version": result.get("version"),
        "wheel": result.get("wheel", "cpu"),
        "driver_version": result.get("driver_version"),
        "status": "detected",
        "priority": priority,
        "detected_at": _utcnow_iso(),
        "verified_at": None,
        "diagnostics": result.get("diagnostics", []),
    }
    save_gpu_state(state, path)
    return state, False


def recommended_wheel_suffix(state: Dict) -> Optional[str]:
    backend = state.get("backend") or "cpu"
    if backend == "cpu":
        return None
    wheel = state.get("wheel")
    if backend == "metal":
        return "metal"
    if backend == "cuda":
        return wheel or "cu124"
    if backend == "rocm":
        return wheel or "rocm6.0"
    return wheel


def wheel_index_url(suffix: str) -> str:
    suffix = suffix.strip("/")
    return f"{LLAMA_CPP_WHEEL_BASE}/{suffix}"


def _installer_command() -> List[str]:
    if shutil.which("uv"):
        return ["uv", "pip", "install"]
    python = shutil.which("python") or shutil.which("python3") or "python"
    return [python, "-m", "pip", "install"]


def install_llama_cpp(state: Dict, *, upgrade: bool = True) -> Tuple[bool, str]:
    backend = state.get("backend") or "cpu"
    cmd = _installer_command()
    if upgrade:
        cmd.append("--upgrade")
    cmd.extend(["--force-reinstall", "--no-cache-dir", "llama-cpp-python"])
    suffix = recommended_wheel_suffix(state)
    if suffix:
        primary_index = wheel_index_url(suffix)
        cmd.extend(["--index-url", primary_index])
        fallback_index = os.getenv("CHL_GPU_FALLBACK_INDEX", PYPI_SIMPLE_INDEX)
        if fallback_index:
            cmd.extend(["--extra-index-url", fallback_index])

    logger.info("Installing llama-cpp-python via %s", " ".join(cmd))
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except Exception as exc:  # noqa: BLE001
        return False, f"Installer failed: {exc}"

    output = (result.stdout or "") + ("\n" + result.stderr if result.stderr else "")
    success = result.returncode == 0
    if success:
        state["status"] = "installed" if backend != "cpu" else "cpu"
        state["installed_at"] = _utcnow_iso()
        state["install_log"] = output[-4000:]
        save_gpu_state(state)
    return success, output


def verify_llama_install(state: Dict) -> Tuple[bool, str]:
    backend = state.get("backend") or "cpu"
    _ensure_env_defaults()
    try:
        from llama_cpp import llama_supports_gpu_offload  # type: ignore
    except ImportError as exc:
        return False, f"Import failed: {exc}"

    try:
        supports = llama_supports_gpu_offload()
    except Exception as exc:  # noqa: BLE001
        return False, f"llama_supports_gpu_offload() errored: {exc}"

    if backend == "cpu":
        state["status"] = "verified"
        state["verified_at"] = _utcnow_iso()
        save_gpu_state(state)
        return True, "CPU mode verified"

    if supports:
        state["status"] = "verified"
        state["verified_at"] = _utcnow_iso()
        save_gpu_state(state)
        return True, "GPU offload supported"

    state["status"] = "needs_attention"
    save_gpu_state(state)
    return False, "llama_supports_gpu_offload() returned False"


def _ensure_env_defaults() -> None:
    cuda_home = os.getenv("CUDA_HOME") or os.getenv("CUDA_PATH")
    if not cuda_home and Path("/usr/local/cuda-12.5").exists():
        cuda_home = "/usr/local/cuda-12.5"

    if cuda_home:
        lib_path = Path(cuda_home) / "lib64"
        current = os.getenv("LD_LIBRARY_PATH", "")
        if lib_path.exists() and str(lib_path) not in current.split(":"):
            os.environ["LD_LIBRARY_PATH"] = f"{lib_path}:{current}" if current else str(lib_path)

    os.environ.setdefault("LLAMA_CUBLAS", "1")
    os.environ.setdefault("LLAMA_CUDA", "1")


def _has_executable(name: str) -> bool:
    return shutil.which(name) is not None


def _has_file(path: str) -> bool:
    return Path(path).exists()


def prerequisite_check(state: Optional[Dict]) -> Dict[str, Optional[str]]:
    """Return prerequisite status/message before attempting GPU install."""
    if not state:
        return {
            "status": "unknown",
            "message": "Run hardware detection first.",
            "action_url": None,
        }

    backend = (state.get("backend") or "cpu").lower()
    version = state.get("version")

    if backend == "cuda":
        if not (
            _has_executable("nvidia-smi")
            or _has_file("/proc/driver/nvidia/version")
            or _has_file("/dev/nvidia0")
        ):
            return {
                "status": "error",
                "message": "NVIDIA driver not detected. Install the latest driver + CUDA toolkit, reboot, then rerun detection.",
                "action_url": NVIDIA_DRIVER_URL,
            }

        parsed = _parse_version_tuple(version)
        max_supported = CUDA_COMPAT_MATRIX[-1][0]
        if parsed and parsed > max_supported:
            target_version = f"{max_supported[0]}.{max_supported[1]}"
            wheel = state.get("wheel") or "cu125"
            return {
                "status": "warn",
                "message": (
                    f"Detected CUDA {version} newer than supported {target_version}. Install the NVIDIA driver + CUDA Toolkit {target_version} and rerun install (wheel {wheel})."
                ),
                "action_url": CUDA_TOOLKIT_URL,
            }

        return {
            "status": "ok",
            "message": f"NVIDIA driver detected (CUDA {version or 'unknown'}).",
            "action_url": None,
        }

    if backend == "rocm":
        if not (_has_file("/dev/kfd") or _has_file("/dev/dri/renderD128")):
            return {
                "status": "error",
                "message": "ROCm devices not found. Install ROCm drivers and restart before retrying.",
                "action_url": ROCM_URL,
            }
        return {
            "status": "ok",
            "message": f"ROCm environment detected ({version or 'unknown'}).",
            "action_url": None,
        }

    if backend == "metal":
        if platform.system() != "Darwin" or platform.machine().lower() not in {"arm64", "aarch64"}:
            return {
                "status": "error",
                "message": "Metal backend requires Apple Silicon on macOS 11+. Switch to CPU mode or run on a supported Mac.",
                "action_url": None,
            }
        if not _has_executable("xcodebuild"):
            return {
                "status": "warn",
                "message": "Install Xcode Command Line Tools so Metal kernels can compile (run `xcode-select --install`).",
                "action_url": APPLE_CLT_URL,
            }
        return {
            "status": "ok",
            "message": "Metal prerequisites detected.",
            "action_url": None,
        }

    return {
        "status": "ok",
        "message": "CPU mode active.",
        "action_url": None,
    }


def system_profile() -> Dict[str, str]:
    return {
        "os": platform.system() or "unknown",
        "os_version": platform.release() or "unknown",
        "machine": platform.machine() or "unknown",
    }


def build_support_prompt(
    state: Optional[Dict],
    prereq: Dict[str, Optional[str]],
    *,
    verify_log: Optional[str] = None,
) -> str:
    facts = system_profile()
    today = _today_iso()
    backend = (state or {}).get("backend", "cpu")
    wheel = (state or {}).get("wheel", "cpu")
    version = (state or {}).get("version")
    driver_version = (state or {}).get("driver_version")
    status = (state or {}).get("status")
    diag_summary = "; ".join(state.get("diagnostics", [])) if state else ""
    prereq_msg = prereq.get("message") if prereq else ""

    lines = []
    lines.append("You are an AI assistant that must provide the newest, official instructions for installing GPU drivers/toolchains.")
    lines.append(f"Today's date: {today}.")
    lines.append("Here is my environment:")
    lines.append(f"- Operating system: {facts['os']} {facts['os_version']} ({facts['machine']})")
    lines.append(f"- GPU backend detected by the application I want to install: {backend} (runtime status: {status})")
    if version:
        lines.append(f"- Reported backend version: {version}")
    if driver_version:
        lines.append(f"- Driver version: {driver_version}")
    lines.append(f"- Required llama-cpp-python wheel suffix: {wheel}")
    if prereq_msg:
        lines.append(f"- Installer prerequisite warning: {prereq_msg}")
    if diag_summary:
        lines.append(f"- Hardware diagnostics: {diag_summary}")
    if verify_log:
        lines.append("- Verification log excerpt:")
        lines.append(verify_log.strip())

    lines.append("\nWhat I need from you:")
    lines.append("1. Step-by-step instructions to install/update the correct official driver/toolkit for this hardware/backend.")
    lines.append("2. Any prerequisite software (e.g., NVIDIA driver + CUDA Toolkit version, AMD ROCm packages, Apple CLT) and how to get them from official sources.")
    lines.append("3. Exact commands or UI steps to verify the installation succeeded (e.g., `nvidia-smi`, `rocminfo`, `clang --version`, sample llama-cpp smoke test).")
    lines.append("4. Safety notes such as backing up work, reboot requirements, and how to roll back if needed.")
    lines.append("5. Keep instructions current as of today's date and cite official vendor docs when possible.")

    return "\n".join(lines)
