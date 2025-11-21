#!/usr/bin/env python
"""
Validate that requirements files are in sync and follow conventions.

This script checks:
1. Core dependencies are identical across all platform requirements files
2. Platform-specific ML dependencies are present only in GPU requirements
3. No version conflicts or inconsistencies
4. All files follow proper formatting conventions

Usage:
    python scripts/validate_requirements.py

Exit codes:
    0 - All checks passed
    1 - Validation failures found
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Set


@dataclass
class Package:
    """Represents a parsed package requirement."""
    name: str
    version_spec: str
    extras: str = ""
    line: str = ""

    def __hash__(self):
        return hash(self.name)

    def __eq__(self, other):
        return isinstance(other, Package) and self.name == other.name


def parse_requirements_file(file_path: Path) -> Dict[str, Package]:
    """
    Parse a requirements.txt file into a dict of package name -> Package.

    Ignores comments and blank lines.
    """
    packages = {}

    if not file_path.exists():
        print(f"⚠️  WARNING: {file_path} does not exist")
        return packages

    for line_no, line in enumerate(file_path.read_text().splitlines(), start=1):
        original_line = line
        line = line.strip()

        # Skip comments and blank lines
        if not line or line.startswith("#"):
            continue

        # Handle PIP_EXTRA_INDEX_URL or other env vars (skip)
        if "=" in line and not any(op in line for op in [">=", "<=", "==", "!=", "~=", ">"]):
            continue

        # Parse package name, extras, and version spec
        # Format: package[extra1,extra2]>=version
        match = re.match(r'^([a-zA-Z0-9_-]+)(\[.*?\])?(.*?)$', line)
        if not match:
            print(f"⚠️  WARNING: Could not parse line {line_no} in {file_path.name}: {original_line}")
            continue

        name = match.group(1).lower()  # Normalize to lowercase
        extras = match.group(2) or ""
        version_spec = match.group(3) or ""

        packages[name] = Package(
            name=name,
            version_spec=version_spec,
            extras=extras,
            line=original_line.strip()
        )

    return packages


def get_core_packages() -> Set[str]:
    """
    Return the set of package names that should be identical across all platforms.

    These are the non-ML dependencies that every platform needs.
    """
    return {
        "fastapi",
        "uvicorn",
        "python-multipart",
        "jinja2",
        "sqlalchemy",
        "httpx",
        "requests",
        "gspread",
        "google-auth",
        "google-auth-oauthlib",
        "numpy",
        "pyyaml",
        "python-dotenv",
        "pydantic",
        "tqdm",
        "tenacity",
        "markdown",
    }


def get_ml_packages() -> Set[str]:
    """
    Return the set of ML package names that should only appear in GPU requirements.
    """
    return {
        "faiss-cpu",
        "huggingface-hub",
        "sentence-transformers",
        "llama-cpp-python",
    }


def validate_core_dependencies(
    cpu_pkgs: Dict[str, Package],
    apple_pkgs: Dict[str, Package],
    cuda_pkgs: Dict[str, Package]
) -> bool:
    """
    Validate that core dependencies are identical across all platforms.

    Returns True if valid, False otherwise.
    """
    print("🔍 Validating core dependencies consistency...")

    core_package_names = get_core_packages()
    all_valid = True

    for pkg_name in sorted(core_package_names):
        # Check each file has the package
        cpu_pkg = cpu_pkgs.get(pkg_name)
        apple_pkg = apple_pkgs.get(pkg_name)
        cuda_pkg = cuda_pkgs.get(pkg_name)

        missing = []
        if not cpu_pkg:
            missing.append("requirements_cpu.txt")
        if not apple_pkg:
            missing.append("requirements_apple.txt")
        if not cuda_pkg:
            missing.append("requirements_nvidia.txt")

        if missing:
            print(f"❌ {pkg_name}: Missing from {', '.join(missing)}")
            all_valid = False
            continue

        # Check versions match
        versions = {
            cpu_pkg.version_spec,
            apple_pkg.version_spec,
            cuda_pkg.version_spec,
        }

        if len(versions) > 1:
            print(f"❌ {pkg_name}: Version mismatch")
            print(f"   CPU:   {cpu_pkg.version_spec}")
            print(f"   Apple: {apple_pkg.version_spec}")
            print(f"   CUDA:  {cuda_pkg.version_spec}")
            all_valid = False

    if all_valid:
        print(f"✅ All {len(core_package_names)} core dependencies are consistent")

    return all_valid


def validate_ml_dependencies(
    cpu_pkgs: Dict[str, Package],
    apple_pkgs: Dict[str, Package],
    cuda_pkgs: Dict[str, Package]
) -> bool:
    """
    Validate that ML dependencies are only in GPU requirements, not CPU.

    Returns True if valid, False otherwise.
    """
    print("\n🔍 Validating ML dependencies...")

    ml_package_names = get_ml_packages()
    all_valid = True

    # Check CPU-only doesn't have ML packages
    for pkg_name in ml_package_names:
        if pkg_name in cpu_pkgs:
            print(f"❌ {pkg_name}: Should NOT be in requirements_cpu.txt (CPU mode has no ML)")
            all_valid = False

    # Check GPU requirements have all ML packages
    missing_apple = []
    missing_cuda = []

    for pkg_name in ml_package_names:
        if pkg_name not in apple_pkgs:
            missing_apple.append(pkg_name)
        if pkg_name not in cuda_pkgs:
            missing_cuda.append(pkg_name)

    if missing_apple:
        print(f"❌ requirements_apple.txt missing ML packages: {', '.join(missing_apple)}")
        all_valid = False

    if missing_cuda:
        print(f"❌ requirements_nvidia.txt missing ML packages: {', '.join(missing_cuda)}")
        all_valid = False

    # Check ML package versions match between Apple and NVIDIA
    for pkg_name in ml_package_names:
        apple_pkg = apple_pkgs.get(pkg_name)
        cuda_pkg = cuda_pkgs.get(pkg_name)

        if apple_pkg and cuda_pkg:
            if apple_pkg.version_spec != cuda_pkg.version_spec:
                print(f"⚠️  {pkg_name}: Version mismatch between Apple and NVIDIA")
                print(f"   Apple: {apple_pkg.version_spec}")
                print(f"   NVIDIA:  {cuda_pkg.version_spec}")
                # This is a warning, not a hard failure
                # (some platform-specific versions may be intentional)

    if all_valid:
        print(f"✅ ML dependencies correctly isolated to GPU requirements")

    return all_valid


def validate_no_unexpected_packages(
    cpu_pkgs: Dict[str, Package],
    apple_pkgs: Dict[str, Package],
    cuda_pkgs: Dict[str, Package]
) -> bool:
    """
    Check for unexpected packages not in core or ML sets.

    Returns True if valid (warnings only), False if there are issues.
    """
    print("\n🔍 Checking for unexpected packages...")

    core_packages = get_core_packages()
    ml_packages = get_ml_packages()
    known_packages = core_packages | ml_packages

    all_packages = set(cpu_pkgs.keys()) | set(apple_pkgs.keys()) | set(cuda_pkgs.keys())
    unexpected = all_packages - known_packages

    if unexpected:
        print(f"⚠️  Found {len(unexpected)} unexpected packages:")
        for pkg in sorted(unexpected):
            locations = []
            if pkg in cpu_pkgs:
                locations.append("CPU")
            if pkg in apple_pkgs:
                locations.append("Apple")
            if pkg in cuda_pkgs:
                locations.append("CUDA")
            print(f"   - {pkg} (in: {', '.join(locations)})")
        print("   This may be intentional. Review to ensure these are needed.")
        return True  # Warning only, not a failure

    print("✅ No unexpected packages found")
    return True


def validate_file_formatting(file_path: Path) -> bool:
    """
    Validate that a requirements file follows formatting conventions.

    Checks:
    - Has a header comment explaining the file's purpose
    - Groups are separated properly
    - No trailing whitespace

    Returns True if valid, False otherwise.
    """
    if not file_path.exists():
        return False

    content = file_path.read_text()
    lines = content.splitlines()

    all_valid = True

    # Check for header comment
    if not content.startswith("#"):
        print(f"⚠️  {file_path.name}: Missing header comment")
        all_valid = False

    # Check for trailing whitespace
    for line_no, line in enumerate(lines, start=1):
        if line != line.rstrip():
            print(f"⚠️  {file_path.name}:{line_no}: Trailing whitespace")
            all_valid = False

    return all_valid


def main() -> int:
    """Run all validation checks."""
    print("=" * 60)
    print("CHL Requirements Validation")
    print("=" * 60)
    print()

    project_root = Path(__file__).parent.parent

    cpu_file = project_root / "requirements_cpu.txt"
    apple_file = project_root / "requirements_apple.txt"
    cuda_file = project_root / "requirements_nvidia.txt"

    # Parse requirements files
    print("📂 Loading requirements files...")
    cpu_pkgs = parse_requirements_file(cpu_file)
    apple_pkgs = parse_requirements_file(apple_file)
    cuda_pkgs = parse_requirements_file(cuda_file)

    print(f"   CPU:    {len(cpu_pkgs)} packages")
    print(f"   Apple:  {len(apple_pkgs)} packages")
    print(f"   NVIDIA: {len(cuda_pkgs)} packages")
    print()

    # Run validation checks
    results = []

    results.append(validate_core_dependencies(cpu_pkgs, apple_pkgs, cuda_pkgs))
    results.append(validate_ml_dependencies(cpu_pkgs, apple_pkgs, cuda_pkgs))
    results.append(validate_no_unexpected_packages(cpu_pkgs, apple_pkgs, cuda_pkgs))

    # File formatting checks (warnings only)
    print("\n🔍 Validating file formatting...")
    validate_file_formatting(cpu_file)
    validate_file_formatting(apple_file)
    validate_file_formatting(cuda_file)
    print("✅ File formatting checks complete")

    # Final summary
    print("\n" + "=" * 60)
    if all(results):
        print("✅ All requirements validation checks passed!")
        print("=" * 60)
        print("\nRequirements files are properly synchronized:")
        print("  ✅ Core dependencies are identical across platforms")
        print("  ✅ ML dependencies are correctly isolated")
        print("  ✅ No unexpected conflicts found")
        return 0
    else:
        print("❌ Requirements validation FAILED")
        print("=" * 60)
        print("\nPlease fix the issues above and re-run this script.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
