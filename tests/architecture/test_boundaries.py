"""Architectural boundary tests to enforce Phase 0 separation rules.

These tests use AST parsing to verify import boundaries are maintained:
1. MCP may only import from src.common.{config,api_client,dto}.*
2. MCP must never import from src.api.*, src.common.{storage,interfaces,web_utils}.*
3. CPU must never import from src.api.gpu.*
4. Common must never import from src.api.* or src.mcp.*
5. Scripts (except setup/test scripts) must never import from src.api.*

This prevents accidental coupling and maintains clean architecture.
"""
import ast
from pathlib import Path
from typing import List, Set
import pytest


PROJECT_ROOT = Path(__file__).parent.parent.parent
SRC_ROOT = PROJECT_ROOT / "src"
SCRIPTS_ROOT = PROJECT_ROOT / "scripts"


def get_python_files(directory: Path) -> List[Path]:
    """Return all .py files in directory recursively."""
    return list(directory.rglob("*.py"))


def extract_imports(file_path: Path) -> Set[str]:
    """Extract all import module paths from a Python file using AST."""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=str(file_path))
    except (SyntaxError, UnicodeDecodeError):
        # Skip files that can't be parsed
        return set()

    imports = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.add(node.module)

    return imports


def test_mcp_only_imports_allowed_common_modules():
    """MCP may only import from src.common.{config,api_client,dto}.*"""
    mcp_files = get_python_files(SRC_ROOT / "mcp")

    allowed_prefixes = {
        "src.common.config",
        "src.common.api_client",
        "src.common.dto",
        "src.mcp",  # Internal imports within MCP
    }

    violations = []

    for file_path in mcp_files:
        imports = extract_imports(file_path)
        for imp in imports:
            if imp.startswith("src.common.") and not any(
                imp.startswith(prefix) for prefix in allowed_prefixes
            ):
                violations.append(
                    f"{file_path.relative_to(PROJECT_ROOT)}: "
                    f"imports forbidden {imp} (only config, api_client, dto allowed)"
                )

    assert not violations, "\n".join([
        "MCP imports forbidden common modules:",
        *violations
    ])


def test_mcp_never_imports_from_api():
    """MCP must never import from src.api.*"""
    mcp_files = get_python_files(SRC_ROOT / "mcp")

    violations = []

    for file_path in mcp_files:
        imports = extract_imports(file_path)
        for imp in imports:
            if imp.startswith("src.api."):
                violations.append(
                    f"{file_path.relative_to(PROJECT_ROOT)}: "
                    f"imports forbidden {imp} (MCP → API coupling)"
                )

    assert not violations, "\n".join([
        "MCP imports from API (forbidden):",
        *violations
    ])


def test_mcp_never_imports_forbidden_common_modules():
    """MCP must never import from src.common.{storage,interfaces,web_utils}.*"""
    mcp_files = get_python_files(SRC_ROOT / "mcp")

    forbidden_prefixes = {
        "src.common.storage",
        "src.common.interfaces",
        "src.common.web_utils",
    }

    violations = []

    for file_path in mcp_files:
        imports = extract_imports(file_path)
        for imp in imports:
            for forbidden in forbidden_prefixes:
                if imp.startswith(forbidden):
                    violations.append(
                        f"{file_path.relative_to(PROJECT_ROOT)}: "
                        f"imports forbidden {imp} (MCP must use HTTP API only)"
                    )

    assert not violations, "\n".join([
        "MCP imports forbidden common modules (storage/interfaces/web_utils):",
        *violations
    ])


def test_cpu_never_imports_from_gpu():
    """CPU implementation must never import from src.api.gpu.*"""
    cpu_files = get_python_files(SRC_ROOT / "api" / "cpu")

    violations = []

    for file_path in cpu_files:
        imports = extract_imports(file_path)
        for imp in imports:
            if imp.startswith("src.api.gpu."):
                violations.append(
                    f"{file_path.relative_to(PROJECT_ROOT)}: "
                    f"imports forbidden {imp} (CPU → GPU coupling)"
                )

    assert not violations, "\n".join([
        "CPU imports from GPU (forbidden):",
        *violations
    ])


def test_common_never_imports_from_api_or_mcp():
    """Common modules must never import from src.api.* or src.mcp.*"""
    common_files = get_python_files(SRC_ROOT / "common")

    violations = []

    for file_path in common_files:
        imports = extract_imports(file_path)
        for imp in imports:
            if imp.startswith("src.api.") or imp.startswith("src.mcp."):
                violations.append(
                    f"{file_path.relative_to(PROJECT_ROOT)}: "
                    f"imports forbidden {imp} (Common → API/MCP coupling)"
                )

    assert not violations, "\n".join([
        "Common imports from API/MCP (forbidden):",
        *violations
    ])


def test_scripts_never_import_from_api_except_exceptions():
    """Scripts (except setup/test scripts) must never import from src.api.*"""
    script_files = get_python_files(SCRIPTS_ROOT)

    # Exception: setup and test scripts may import from src.api.*
    exceptions = {
        "setup-gpu.py",
        "setup-cpu.py",
        "gpu_smoke_test.py",
        "check_api_env.py",
    }

    violations = []

    for file_path in script_files:
        # Skip exception scripts
        if file_path.name in exceptions:
            continue

        imports = extract_imports(file_path)
        for imp in imports:
            if imp.startswith("src.api."):
                violations.append(
                    f"{file_path.relative_to(PROJECT_ROOT)}: "
                    f"imports forbidden {imp} (scripts must use HTTP via CHLAPIClient)"
                )

    assert not violations, "\n".join([
        "Scripts import from API (forbidden, except setup/test scripts):",
        *violations
    ])


def test_api_may_import_from_common():
    """API may import from src.common.* (allowed)"""
    # This is a positive test to ensure the test framework works
    api_files = get_python_files(SRC_ROOT / "api")

    # Just verify we can find some common imports (smoke test)
    found_common_imports = False

    for file_path in api_files:
        imports = extract_imports(file_path)
        for imp in imports:
            if imp.startswith("src.common."):
                found_common_imports = True
                break
        if found_common_imports:
            break

    # This is expected - API should import from common
    # If this fails, either:
    # 1. The test is broken
    # 2. The API implementation is broken (unlikely)
    assert found_common_imports, "Expected API to import from common (sanity check failed)"


def test_no_circular_imports_between_api_and_mcp():
    """Verify no circular dependencies between API and MCP packages"""
    api_files = get_python_files(SRC_ROOT / "api")
    mcp_files = get_python_files(SRC_ROOT / "mcp")

    # API should never import from MCP
    api_violations = []
    for file_path in api_files:
        imports = extract_imports(file_path)
        for imp in imports:
            if imp.startswith("src.mcp."):
                api_violations.append(
                    f"{file_path.relative_to(PROJECT_ROOT)}: "
                    f"imports {imp} (API → MCP forbidden)"
                )

    # MCP should never import from API (already tested above, but check again)
    mcp_violations = []
    for file_path in mcp_files:
        imports = extract_imports(file_path)
        for imp in imports:
            if imp.startswith("src.api."):
                mcp_violations.append(
                    f"{file_path.relative_to(PROJECT_ROOT)}: "
                    f"imports {imp} (MCP → API forbidden)"
                )

    all_violations = api_violations + mcp_violations
    assert not all_violations, "\n".join([
        "Circular imports detected between API and MCP:",
        *all_violations
    ])


if __name__ == "__main__":
    # Allow running tests directly for quick feedback
    pytest.main([__file__, "-v"])
