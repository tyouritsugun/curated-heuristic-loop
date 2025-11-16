# Architecture Refinement Plan

## User Story & Objectives
- **Primary goal**: Any developer can stand up the API server with hardware-appropriate Python environments (Apple/Metal, NVIDIA CUDA, AMD ROCm, Intel oneAPI, CPU-only) without touching Docker. After that, they run `uv sync` (from `pyproject.toml`) to provision the MCP server.
- **Experience**: Provide a guided diagnosis flow (CLI script mirroring `src/api/routers/ui.py` prompts) that inspects system readiness, emits a structured report + prompt, and helps the user ask an LLM for fixes when the environment is misconfigured.
- **Separation of concerns**: MCP never manipulates SQLite/FAISS directly; it communicates with the API server via HTTP after both runtimes are installed per their instructions.
- **Deployment context**: Local-only deployment where MCP and API run on the same machine. No authentication required. MCP receives API server URL as a start parameter.

## Current Codebase Gap
1. **Native setup ambiguity** – There is no single source of truth for bringing up the API server per hardware class (Apple Metal, CUDA, ROCm, oneAPI, CPU-only); instructions vary by file or past context.
2. **Requirements drift** – `requirements_cpu.txt`, `requirements_apple.txt`, and the default `requirements.txt` overlap but are not curated for the new “API server venv per platform” story.
3. **Diagnostics absent** – No script exists to audit GPU drivers/CUDA/ROCm/oneAPI/Metal availability, compiler toolchains, or `llama-cpp-python` loadability; troubleshooting remains manual.
4. **Runtime coupling** – MCP and API code share repositories/config in ad-hoc ways; nothing enforces the “MCP talks HTTP only” constraint.
5. **Docs misaligned** – `doc/architecture.md`, README, etc. still reference legacy workflows and do not guide users through platform-specific native setups or the diagnosis prompt loop.

## Architecture Decision Records (ADRs)

### ADR-001: HTTP-Based Architecture for API ↔ MCP Communication

**Decision**: Use HTTP/REST API as the sole communication method between MCP server and API server, instead of shared library imports or direct database access.

**Rationale:**
- **Multi-client concurrency**: Users may run multiple MCP clients simultaneously (e.g., Cursor and Claude Code), creating lightweight concurrency challenges
- **Resource control**: HTTP server provides a single point of control with built-in lock mechanisms for embedding services and FAISS index operations
- **Process isolation**: Separate processes prevent resource conflicts and simplify debugging
- **Error handling**: HTTP provides clear error boundaries and failure modes

**Implications:**
- All MCP operations must go through API endpoints
- API server becomes the sole authority for database and FAISS operations
- Requires comprehensive API surface for all MCP operations
- Lock mechanisms in API server prevent concurrent modification issues

### ADR-002: CPU/GPU Runtime Separation via Strategy Pattern

**Decision**: Separate CPU-only and GPU-accelerated implementations into distinct modules (`src/api/cpu/` and `src/api/gpu/`) using strategy pattern, rather than runtime polymorphism or conditional logic scattered throughout the codebase.

**Rationale:**
- **Simplicity**: Clear separation makes code easier to understand and maintain
- **Dependency isolation**: CPU mode has no GPU dependencies, enabling lighter installations
- **Testing**: Can test each mode independently without mocking GPU/CPU-specific code
- **Developer onboarding**: New developers can focus on one mode at a time

**Implications:**
- Mode is fixed at startup (no runtime switching between CPU/GPU)
- Switching modes requires data cleanup and re-setup
- Runtime builder (`src/api/runtime_builder.py`) provides the abstraction layer

### ADR-003: Directory Structure for Clarity

**Decision**: Adopt the three-tier directory structure `src/api/`, `src/mcp/`, `src/common/` with explicit boundaries.

**Rationale:**
- **Easy to understand**: Directory names directly map to architectural components
- **Enforced boundaries**: Clear import rules prevent accidental coupling
- **Developer experience**: New developers can quickly orient themselves
- **Scalability**: Structure supports future growth without major refactoring

**Implications:**
- Common code must have no dependencies on API or MCP
- API and MCP communicate only via HTTP (CHLAPIClient)
- Boundary tests enforce architectural rules

### ADR-004: Local-Only Deployment Model

**Decision**: Design for local-only deployment where MCP and API server run on the same developer machine.

**Rationale:**
- **MVP stage**: Not building a cloud service yet
- **Simplicity**: No need for authentication, authorization, or distributed system complexity
- **Developer focus**: Keep focus on core functionality, not infrastructure
- **Fast iteration**: Minimal deployment overhead

**Implications:**
- No authentication layer required
- Simple error handling (404 if API unavailable, no reconnection logic)
- MCP receives API URL as start parameter (typically localhost:port)
- Lock mechanisms are sufficient for concurrency (no distributed locks needed)
- Background worker coordination uses existing in-process mechanisms

### ADR-005: Fixed Runtime Mode

**Decision**: Runtime mode (CPU vs GPU) is fixed at API server startup and does not change dynamically.

**Rationale:**
- **Simplicity**: Eliminates complex mode-switching logic
- **Resource management**: GPU resources (FAISS index, embeddings) are expensive to load/unload
- **Clear expectations**: Users know their deployment mode upfront
- **Data consistency**: Avoids issues with partially-synced embeddings or index mismatches

**Implications:**
- Mode change requires: stop server → cleanup data → reconfigure → restart
- Template selection happens once at startup
- No hot-swapping between CPU and GPU providers

## Phased Approach
0. **Phase 0 – Codebase Isolation Prerequisite**
   - Restructure `src/` into three top-level directories: `api/` (API server), `mcp/` (MCP server), `common/` (shared utilities)
   - Within the API server, isolate CPU-specific (`api/cpu/`) and GPU-specific (`api/gpu/`) implementations behind clean interfaces (strategy pattern)
   - Migrate shared API client to `common/api_client/` for reuse by scripts, MCP, and external tools
   - Extract runtime builder factory to `api/runtime_builder.py` to prevent circular dependencies between common and API layers
   - Reorganize web templates into `api/templates/{common,cpu,gpu}/` to support mode-specific UI
   - Move web utilities (static files, markdown rendering) to `common/web_utils/`
   - Update 13+ operational scripts to use new import paths and HTTP-based orchestration via `CHLAPIClient`
   - Add boundary validation tests (AST-based import checks) to enforce architectural rules and prevent future coupling
   - Document the boundaries, import rules, and entry points so future work during later phases builds on an already decoupled foundation

   **Key Deliverables:**
   - Clear separation: `api/` ↔ `mcp/` via HTTP only, no shared state
   - CPU/GPU isolation: Strategy pattern, no cross-imports
   - Scripts migration: Mode-aware orchestration (import.py detects CPU/GPU, export.py mode-agnostic)
   - Foundation for platform-specific requirements work (Phase A)
   - **Concurrency controls**: Retain existing lock mechanisms for FAISS file operations and background worker coordination

1. **Phase A – Requirements & Documentation Baseline**
   - Finalize `requirements_*.txt` matrices (Apple Metal, NVIDIA CUDA, AMD ROCm, Intel GPU, CPU) dedicated to the API server venv.
   - Update README + `doc/architecture.md` with the new installation story (API venv per platform + MCP via `uv sync`).

2. **Phase B – Diagnostics & Environment Guardrails**
   - Implement `scripts/check_api_env.py` (or similar) that:
     - Detects OS/GPU/toolchain readiness.
     - Attempts a minimal `llama_cpp` import targeting the selected backend.
     - Emits a JSON/text summary + “LLM prompt” saved to disk when failures occur.
   - Hook the script into onboarding docs so users run it before/after installing their venv.

3. **Phase C – Runtime Isolation**
   - Enforce MCP ↔ API separation: introduce clear service boundaries, ensure MCP never opens SQLite/FAISS, and relies on HTTP endpoints.
   - Refine shared modules (`src/common/`) to expose only DTOs/clients both sides need; keep runtime-specific code in dedicated directories.

4. **Phase D – Validation & Hardening**
   - Provide smoke-test commands per platform (native steps built on the curated requirements) to verify embeddings/rerankers.
   - Add CI or scripted checks that run the diagnostics, ensure requirements files stay in sync with `pyproject`, and keep documentation accurate.

Delivering these phases keeps the project native-first with strong diagnostics while preserving the MCP workflow via `uv sync`.
