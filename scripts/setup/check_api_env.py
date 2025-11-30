#!/usr/bin/env python
"""Environment diagnostics menu for CHL API server (Phase B).

This script provides an interactive menu to select your hardware platform
and dispatches to the appropriate platform-specific diagnostics script:
  - check_api_env_cpu.py for CPU-only mode (no GPU)
  - check_api_env_apple.py for Apple Silicon (Metal GPU)
  - check_api_env_nvidia.py for NVIDIA GPUs (CUDA)
  - check_api_env_amd.py for AMD GPUs (ROCm) - TBD
  - check_api_env_intel.py for Intel GPUs (oneAPI) - TBD

It must be run with the API server stopped.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
DATA_DIR = PROJECT_ROOT / "data"
HELP_PROMPT_PATH = DATA_DIR / "hardware_selection_help.txt"


def _show_menu() -> str:
    """Display hardware selection menu and return the selected option."""
    print("=" * 70)
    print("CHL Environment Diagnostics - Hardware Selection")
    print("=" * 70)
    print()
    print("Please select your hardware platform:")
    print()
    print("  1. I do not have a GPU (CPU-only mode)")
    print("  2. My computer is Apple Silicon (M1/M2/M3/M4)")
    print("  3. I have an NVIDIA GPU")
    print("  4. I have an AMD GPU (Coming soon - Not yet supported)")
    print("  5. I have an Intel GPU (Coming soon - Not yet supported)")
    print()
    print("  6. I don't know which option to choose (Get help)")
    print()
    print("  0. Exit")
    print()

    while True:
        try:
            choice = input("Enter your choice (0-6): ").strip()
            if choice in {"0", "1", "2", "3", "4", "5", "6"}:
                return choice
            print("✗ Invalid choice. Please enter a number between 0 and 6.")
        except (EOFError, KeyboardInterrupt):
            print("\n\nExiting...")
            sys.exit(0)


def _build_help_prompt() -> str:
    """Generate a prompt to help users identify their hardware platform."""
    import platform

    lines = []
    lines.append("I'm trying to set up the CHL (Curated Heuristic Loop) project with the correct hardware path (HF/Torch stack), but I'm not sure which option to choose.")
    lines.append("")
    lines.append("## My System Information")
    lines.append(f"- Operating System: {platform.system()} {platform.release()}")
    lines.append(f"- Architecture: {platform.machine()}")
    lines.append(f"- Python Version: {platform.python_version()}")
    lines.append("")

    lines.append("## Available Hardware Options")
    lines.append("")

    lines.append("### Option 1: CPU-Only Mode (No GPU)")
    lines.append("**When to choose:**")
    lines.append("- You don't have a dedicated GPU")
    lines.append("- You only have integrated graphics (Intel HD, Intel Iris, etc.)")
    lines.append("- You want minimal setup complexity")
    lines.append("- You're okay with keyword-based search instead of semantic search")
    lines.append("")
    lines.append("**Requirements:**")
    lines.append("- No special hardware or drivers needed")
    lines.append("- Works on any system with Python 3.10-3.12")
    lines.append("")
    lines.append("**Trade-offs:**")
    lines.append("- Search uses SQLite LIKE queries (exact phrase matching)")
    lines.append("- No semantic similarity or AI-powered reranking")
    lines.append("- Faster setup, but less powerful search")
    lines.append("")

    lines.append("### Option 2: Apple Silicon (M1/M2/M3/M4)")
    lines.append("**When to choose:**")
    lines.append("- You have a Mac with Apple Silicon chip (NOT Intel Mac)")
    lines.append("- You want GPU-accelerated semantic search")
    lines.append("")
    lines.append("**Requirements:**")
    lines.append("- macOS with Apple M1, M2, M3, or M4 chip")
    lines.append("- Xcode Command Line Tools installed")
    lines.append("- At least 8GB unified memory (16GB+ recommended)")
    lines.append("- Python 3.10-3.12 (NOT Python 3.13)")
    lines.append("")
    lines.append("**How to check:**")
    lines.append("- Open \"About This Mac\" → Check if it says \"Chip: Apple M1/M2/M3/M4\"")
    lines.append("- Or run: `sysctl -n machdep.cpu.brand_string` (should show \"Apple\")")
    lines.append("")

    lines.append("### Option 3: NVIDIA GPU (CUDA)")
    lines.append("**When to choose:**")
    lines.append("- You have an NVIDIA GPU (desktop or laptop)")
    lines.append("- You want GPU-accelerated semantic search on Linux/Windows")
    lines.append("")
    lines.append("**Requirements:**")
    lines.append("- NVIDIA GPU with Compute Capability 6.0+ (Pascal or newer)")
    lines.append("  - Examples: GTX 1060+, RTX 2000/3000/4000 series, Tesla, etc.")
    lines.append("- NVIDIA GPU drivers installed")
    lines.append("- CUDA Toolkit 11.8+ (12.x recommended)")
    lines.append("- At least 4GB VRAM (8GB+ recommended)")
    lines.append("- CMake build tools")
    lines.append("- Python 3.10-3.12 (NOT Python 3.13)")
    lines.append("")
    lines.append("**How to check:**")
    lines.append("- Run: `nvidia-smi` (should show your GPU and driver version)")
    lines.append("- Run: `nvcc --version` (should show CUDA toolkit version)")
    lines.append("")

    lines.append("### Option 4: AMD GPU (ROCm) - Coming Soon")
    lines.append("**Status:** Not yet supported. Use CPU-only mode for now.")
    lines.append("")
    lines.append("**Planned requirements:**")
    lines.append("- AMD GPU with ROCm support (RX 6000/7000 series, etc.)")
    lines.append("- ROCm 5.x or 6.x drivers installed")
    lines.append("- Linux only (ROCm doesn't support Windows/macOS)")
    lines.append("")

    lines.append("### Option 5: Intel GPU (oneAPI) - Coming Soon")
    lines.append("**Status:** Not yet supported. Use CPU-only mode for now.")
    lines.append("")
    lines.append("**Planned requirements:**")
    lines.append("- Intel Arc GPU or integrated graphics with oneAPI support")
    lines.append("- Intel oneAPI Base Toolkit installed")
    lines.append("")

    lines.append("## Investigation Commands")
    lines.append("")
    lines.append("Please run these commands and paste the output below:")
    lines.append("")

    if platform.system() == "Darwin":
        lines.append("**For macOS:**")
        lines.append("```bash")
        lines.append("# Check if you have Apple Silicon")
        lines.append("sysctl -n machdep.cpu.brand_string")
        lines.append("")
        lines.append("# Check total memory")
        lines.append("sysctl hw.memsize")
        lines.append("")
        lines.append("# Check macOS version")
        lines.append("sw_vers")
        lines.append("```")
    elif platform.system() == "Linux":
        lines.append("**For Linux:**")
        lines.append("```bash")
        lines.append("# Check for NVIDIA GPU")
        lines.append("nvidia-smi 2>/dev/null || echo 'nvidia-smi not found'")
        lines.append("")
        lines.append("# Check for AMD GPU")
        lines.append("lspci | grep -i 'vga\\|3d\\|display' | grep -i amd")
        lines.append("")
        lines.append("# Check CUDA toolkit")
        lines.append("nvcc --version 2>/dev/null || echo 'nvcc not found'")
        lines.append("")
        lines.append("# Check ROCm")
        lines.append("rocm-smi --showdriverversion 2>/dev/null || echo 'rocm-smi not found'")
        lines.append("```")
    elif platform.system() == "Windows":
        lines.append("**For Windows:**")
        lines.append("```powershell")
        lines.append("# Check for NVIDIA GPU")
        lines.append("nvidia-smi")
        lines.append("")
        lines.append("# Check CUDA toolkit")
        lines.append("nvcc --version")
        lines.append("")
        lines.append("# List all GPUs")
        lines.append("wmic path win32_VideoController get name")
        lines.append("```")

    lines.append("")
    lines.append("## What I Need Help With")
    lines.append("")
    lines.append("Based on my system information and the investigation commands output above:")
    lines.append("")
    lines.append("**Please tell me which hardware option (1, 2, 3, 4, or 5) I should choose, and why.**")
    lines.append("")
    lines.append("Then, instruct me to go back to the CHL environment diagnostics script:")
    lines.append("```bash")
    lines.append("python3 scripts/setup/check_api_env.py")
    lines.append("```")
    lines.append("")
    lines.append("And select the option you recommended. The script will handle all installation steps automatically.")

    return "\n".join(lines)


def _save_help_prompt(prompt: str) -> None:
    """Save help prompt to disk."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with HELP_PROMPT_PATH.open("w", encoding="utf-8") as fh:
        fh.write(prompt)


def _handle_help_request() -> None:
    """Generate and display help prompt for hardware selection."""
    print("\n" + "=" * 70)
    print("Generating Hardware Selection Help Prompt")
    print("=" * 70)
    print()

    prompt = _build_help_prompt()
    _save_help_prompt(prompt)

    print("A detailed help prompt has been saved to:")
    print(f"  {HELP_PROMPT_PATH}")
    print()
    print("Please:")
    print("  1. Open this file and copy its entire contents")
    print("  2. Paste it into ChatGPT, Claude, or another LLM")
    print("  3. Follow the LLM's guidance to identify your hardware")
    print("  4. Run this script again and choose the recommended option")
    print()
    print("=" * 70)
    print()

    # Show a preview of the prompt
    print("Preview of the help prompt:")
    print("-" * 70)
    lines = prompt.split("\n")
    for line in lines[:30]:  # Show first 30 lines
        print(line)
    if len(lines) > 30:
        print("...")
        print(f"({len(lines) - 30} more lines in the full file)")
    print("-" * 70)
    print()

    sys.exit(0)


def _get_platform_script(choice: str) -> Path:
    """Return the path to the platform-specific check script based on user choice."""
    script_map = {
        "1": ("check_api_env_cpu.py", "CPU-only"),
        "2": ("check_api_env_apple.py", "Apple Silicon"),
        "3": ("check_api_env_nvidia.py", "NVIDIA CUDA"),
        "4": ("check_api_env_amd.py", "AMD ROCm"),
        "5": ("check_api_env_intel.py", "Intel oneAPI"),
    }

    if choice == "0":
        print("\nExiting...")
        sys.exit(0)

    if choice not in script_map:
        raise ValueError(f"Invalid choice: {choice}")

    script_name, platform_name = script_map[choice]
    script_path = SCRIPTS_DIR / script_name

    # Check if the script exists (for future AMD/Intel support)
    if not script_path.exists():
        if choice in {"4", "5"}:
            print(f"\n✗ {platform_name} support is coming soon!")
            print(f"  The script '{script_name}' is not yet available.")
            print("\n  For now, please use CPU-only mode (option 1) or check back in a future release.")
            sys.exit(1)
        else:
            raise FileNotFoundError(f"Platform-specific script not found: {script_path}")

    return script_path, platform_name


def main() -> None:
    # Show menu and get user choice
    choice = _show_menu()

    # Handle help request (option 6)
    if choice == "6":
        _handle_help_request()
        # This function exits, so we never reach here

    # Get the platform-specific script
    try:
        script_path, platform_name = _get_platform_script(choice)
    except (ValueError, FileNotFoundError) as exc:
        print(f"\n✗ Error: {exc}")
        sys.exit(1)

    print(f"\nSelected: {platform_name}")
    print(f"Running diagnostics: {script_path.name}\n")
    print("=" * 70)
    print()

    # Build command with all arguments from sys.argv (excluding the script name)
    cmd = [sys.executable, str(script_path)] + sys.argv[1:]

    # Execute the platform-specific script
    try:
        result = subprocess.run(cmd, check=False)
        sys.exit(result.returncode)
    except Exception as exc:
        print(f"\n✗ Failed to execute {script_path.name}: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
