# Architecture Refinement Plan

## User Story & Objectives
- **Primary goal**: Any developer can stand up the API server with hardware-appropriate Python environments (Apple/Metal, NVIDIA CUDA, AMD ROCm, Intel oneAPI, CPU-only) without touching Docker. After that, they run `uv sync` (from `pyproject.toml`) to provision the MCP server.
- **Experience**: Provide a guided diagnosis flow (CLI script mirroring `src/api/routers/ui.py` prompts) that inspects system readiness, emits a structured report + prompt, and helps the user ask an LLM for fixes when the environment is misconfigured.
- **Separation of concerns**: MCP never manipulates SQLite/FAISS directly; it communicates with the API server via HTTP after both runtimes are installed per their instructions.

## Current Codebase Gap
1. **Native setup ambiguity** – There is no single source of truth for bringing up the API server per hardware class (Apple Metal, CUDA, ROCm, oneAPI, CPU-only); instructions vary by file or past context.
2. **Requirements drift** – `requirements_cpu.txt`, `requirements_apple.txt`, and the default `requirements.txt` overlap but are not curated for the new “API server venv per platform” story.
3. **Diagnostics absent** – No script exists to audit GPU drivers/CUDA/ROCm/oneAPI/Metal availability, compiler toolchains, or `llama-cpp-python` loadability; troubleshooting remains manual.
4. **Runtime coupling** – MCP and API code share repositories/config in ad-hoc ways; nothing enforces the “MCP talks HTTP only” constraint.
5. **Docs misaligned** – `doc/architecture.md`, README, etc. still reference legacy workflows and do not guide users through platform-specific native setups or the diagnosis prompt loop.

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
