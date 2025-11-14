# GPU / CPU Mode Isolation – Design Proposal

## 1. Context and Goal

CHL currently supports two execution postures:

- **GPU / semantic mode** – `CHL_SEARCH_MODE=auto` with FAISS + embeddings + reranker.
- **CPU-only mode** – `CHL_SEARCH_MODE=sqlite_only` with SQLite keyword search only.

The implementation today shares a lot of code paths, and behavior is often controlled by
`if search_mode == "sqlite_only"` branches. This makes it easy for a CPU-only change to
accidentally affect the GPU experience (and vice versa), especially in UI, docs, and
operations logic.

**Goal:** Keep shared infrastructure (database schema and config) but make the *features*
for CPU-only and GPU/semantic modes as isolated as possible, so that:

- Each mode has a clear, self-contained implementation and docs.
- Mode-specific changes do not require constant back-and-forth edits.
- Tests and operations can be reasoned about per mode.

Non-goals:

- Redesigning the search algorithms themselves.
- Introducing new modes (e.g., `vector_only`) beyond what `Config` already hints at.

---

## 2. Current Coupling Map

This section records where CPU/GPU (search mode) concerns are currently intertwined.

### 2.1. Configuration and core runtime

- `src/config.py`
  - Parses `CHL_SEARCH_MODE` and exposes `config.search_mode` as a lowercase string.
  - Other modules read either `config.search_mode` or the raw env var directly.
- `src/api_server.py`
  - In `lifespan`, uses `config.search_mode` to decide whether to initialize:
    - FAISS / vector provider.
    - Embedding client / reranker.
    - Background worker + worker pool.
  - In sqlite-only mode, constructs `SearchService` with `primary_provider="sqlite_text"`
    and no vector provider.
- `src/search/service.py`
  - `SearchService` itself is mode-agnostic but can register both:
    - `SQLiteTextProvider`.
    - `VectorFAISSProvider` (when available).
  - It encapsulates the fallback from vector → SQLite, but is configured differently
    depending on mode.

**Observation:** Config and search orchestration are already relatively clean; the
primary coupling pain is not here but in the layers above (UI, diagnostics, operations,
docs).

### 2.2. Services and health/diagnostics

- `src/services/operations_service.py`
  - Uses `_vector_mode_enabled()` which re-reads `CHL_SEARCH_MODE` from the environment
    (`sqlite_only` → no re-embed / sync).
  - Embedding-related operations are guarded by this check but live in the same class
    as CPU-only operations (e.g., import/export).
- `src/services/settings_service.py`
  - Uses `CHL_SEARCH_MODE` to decide FAISS diagnostics:
    - In sqlite-only mode: FAISS is reported as “Semantic search disabled”.
    - Otherwise: inspects FAISS index files + metadata.
  - Diagnostics for DB, disk, credentials are shared between modes.
- `src/api/routers/health.py`
  - Has a mode-dependent branch:
    - `search_mode=sqlite_only` → FAISS and embedding components are “disabled”.
    - Else → inspect vector provider and adjust overall status.

**Observation:** Health/diagnostics mix the “core” checks (DB, disk) with
mode-specific checks (FAISS, embeddings) in single functions. This is where CPU vs GPU
behavior is tightly coupled.

### 2.3. API, MCP, and search UX

- `src/api/routers/entries.py`
  - Uses `_runtime_search_mode(config, search_service)` to compute an effective mode
    (`auto` vs `sqlite_only`) for response metadata.
  - `read_entries` always calls `search_service.search()` for semantic behavior; the
    concrete behavior depends on the search stack, not on explicit branching here.
- `src/mcp/handlers_entries.py`
  - Similar `_runtime_search_mode` helper to expose mode in `meta.search_mode`.
  - MCP clients rely on this to tailor UX (keyword guidance when in sqlite-only mode).

**Observation:** These layers are already relatively mode-neutral; they defer to the
search stack and only expose mode as metadata. This is aligned with the isolation goal.

### 2.4. Web UI and templates

- `src/api/routers/ui.py`
  - `/settings` endpoint selects template dynamically:
    - `settings_cpu.html` when `config.search_mode == "sqlite_only"`.
    - `settings.html` otherwise (GPU/semantic).
  - `/operations` and telemetry endpoints pass `search_mode` into template contexts.
- `src/web/templates/settings.html`
  - GPU/semantic-flavored dashboard; assumes FAISS + embeddings are desirable.
- `src/web/templates/settings_cpu.html`
  - CPU-only dashboard; hides GPU-only controls (models card, index controls).
- `src/web/templates/operations.html`
  - Single template with conditional blocks:
    - `if search_mode == 'sqlite_only'` → CPU-only banner and hides models card.
    - Else → semantic-search banner and shows FAISS/embedding controls.
- `src/web/templates/partials/settings_onboarding.html`,
  `src/web/templates/partials/ops_onboarding.html`
  - Contain text that explicitly mentions both GPU and CPU flows.

**Observation:** Settings already uses separate templates per mode; operations and
onboarding still interleave CPU/GPU concepts in a single file, which is a common place
where changing CPU copy can unintentionally affect GPU UX.

### 2.5. Scripts and docs

- Scripts:
  - `scripts/setup-gpu.py` – GPU mode only.
  - `scripts/setup-cpu.py` – CPU-only; checks `CHL_SEARCH_MODE=sqlite_only`.
  - `scripts/search_health.py`, `scripts/import.py` – messaging references both modes.
- Docs:
  - `doc/manual.md` – single manual that interleaves GPU vs CPU instructions.
  - `doc/cpu_only_user.md` and `doc/cpu/implementation_plan.md` – CPU-focused but
    reference GPU semantics (e.g., FAISS snapshot compatibility).
  - Root docs (README, architecture) describe both modes in a single flow.

**Observation:** Documentation is where most cross-talk happens; editing CPU guidance
often requires keeping GPU guidance in sync within the same file.

---

## 3. Evaluation of the Isolation Idea

### 3.1. Benefits

- **Lower cognitive load:** Clear separation allows contributors to work on “CPU-only
  UX” or “semantic/GPU UX” without mentally simulating both at once.
- **Fewer regressions:** Mode-specific behavior is localized; changing CPU templates or
  diagnostics does not risk subtly changing GPU behavior.
- **Targeted testing:** We can run mode-specific test suites:
  - `CHL_SEARCH_MODE=sqlite_only` – validate CPU-only runtime + UI.
  - `CHL_SEARCH_MODE=auto` – validate full semantic stack.
- **Future extensibility:** If we later add `vector_only` or “cloud vector” modes, the
  same pattern can accommodate additional runtime profiles.

### 3.2. Costs / Risks

- **Initial refactor cost:** Introducing mode-specific packages and adapters touches
  core files (`api_server.py`, services, health router).
- **Duplication risk:** If we naively copy templates or diagnostics, they may drift
  over time. We should deliberately define a “shared core” and “mode-specific
  overlays” instead of full duplication.
- **Split documentation:** Users need a clear landing page; splitting docs per mode
  must still give a coherent top-level narrative.

### 3.3. Feasibility

Overall feasibility is **high**:

- `Config` already exposes `search_mode`, and almost all mode-related logic is gated on
  that flag or on the presence of a vector provider.
- Settings UI is already dual-templated (`settings.html` vs `settings_cpu.html`).
- The search stack is encapsulated in `SearchService`; we mainly need to reorganize
  *wiring* and *presentation*, not core algorithms.

The main design choice is how strong we want the isolation boundary to be. The proposal
below favors:

- Shared **core infrastructure**: database, config, repositories, `SearchService`.
- Mode-specific **runtime wiring**, **UI**, **operations**, and **docs**.

---

## 4. Proposed Architecture

### 4.1. Explicit mode model

Introduce a formal mode model in `src/config.py`:

- Add an enum:

  ```python
  # src/config.py
  from enum import Enum

  class SearchMode(str, Enum):
      AUTO = "auto"
      SQLITE_ONLY = "sqlite_only"
      # VECTOR_ONLY = "vector_only"  # future
  ```

- Change `Config.search_mode` to hold a `SearchMode` value (or a thin wrapper that keeps
  backwards compatibility for string callers).
- Provide helpers:

  ```python
  def is_cpu_only(self) -> bool: ...
  def is_semantic_enabled(self) -> bool: ...
  ```

This keeps the “source of truth” for mode semantics in one place and lets other modules
depend on the enum rather than re-parsing env vars.

### 4.2. Mode runtimes package

Add a dedicated package for runtime wiring:

- `src/modes/__init__.py`
- `src/modes/base.py`
- `src/modes/sqlite_only/runtime.py`
- `src/modes/vector/runtime.py`

Conceptually:

```python
# src/modes/base.py
from dataclasses import dataclass
from typing import Optional

@dataclass
class ModeRuntime:
    search_service: "SearchService"
    background_worker: Optional[object]
    worker_pool: Optional[object]
    # Optional hooks for health / diagnostics
    def describe_faiss_component(self) -> dict: ...
    def describe_embedding_component(self) -> dict: ...
```

```python
# src/modes/sqlite_only/runtime.py
from src.search.service import SearchService

def build_runtime(config) -> ModeRuntime:
    search_service = SearchService(
        primary_provider="sqlite_text",
        fallback_enabled=False,
        max_retries=0,
        vector_provider=None,
    )
    return ModeRuntime(
        search_service=search_service,
        background_worker=None,
        worker_pool=None,
    )
```

```python
# src/modes/vector/runtime.py
from src.search.service import SearchService
from src.search.vector_provider import VectorFAISSProvider
from src.embedding.client import EmbeddingClient
from src.embedding.reranker import RerankerClient

def build_runtime(config) -> ModeRuntime:
    # Assemble embedding client, reranker, vector provider, background worker, etc.
    ...
    search_service = SearchService(
        primary_provider="vector_faiss",
        fallback_enabled=True,
        max_retries=config.search_fallback_retries,
        vector_provider=vector_provider,
    )
    return ModeRuntime(
        search_service=search_service,
        background_worker=background_worker,
        worker_pool=worker_pool,
    )
```

`src/modes/__init__.py` would export a single entry:

```python
from .base import ModeRuntime
from .sqlite_only.runtime import build_runtime as build_cpu_runtime
from .vector.runtime import build_runtime as build_vector_runtime

def build_mode_runtime(config) -> ModeRuntime:
    if config.is_cpu_only():
        return build_cpu_runtime(config)
    return build_vector_runtime(config)
```

### 4.3. API server wiring via runtime

Update `src/api_server.py` to delegate to the runtime builder:

- Replace inline `if config.search_mode == "sqlite_only": ... else: ...` with:

  ```python
  from src.modes import build_mode_runtime

  runtime = build_mode_runtime(config)
  search_service = runtime.search_service
  background_worker = runtime.background_worker
  worker_pool = runtime.worker_pool
  ```

- Metrics:
  - Increment `search_mode_sqlite_only` or `search_mode_auto` based on `config.search_mode`
    once, after runtime initialization.

**Result:** All search stack bootstrapping is encapsulated in mode-specific code. The
API server just consumes a `ModeRuntime`.

### 4.4. Mode-specific health and diagnostics

Move FAISS / embedding health concerns out of shared routers/services into the runtime
layer:

- Add optional methods to `ModeRuntime` to describe mode-specific components:

  ```python
  class ModeRuntime:
      ...
      def health_components(self) -> dict[str, dict]:
          """Return FAISS / embedding component statuses appropriate to this mode."""
  ```

- Implementations:
  - `sqlite_only` runtime returns:

    ```python
    {
      "faiss_index": {"status": "disabled", "detail": "Intentional SQLite-only mode"},
      "embedding_model": {"status": "disabled", "detail": "Intentional SQLite-only mode"},
    }
    ```

  - `vector` runtime inspects the vector provider and FAISS index the same way
    `health.py` currently does.

- `src/api/routers/health.py` becomes:

  ```python
  @router.get("/health")
  def health(config=Depends(get_config), search_service=Depends(get_search_service)):
      ...
      from src.api_server import runtime  # or inject via dependency
      health_components.update(runtime.health_components())
      ...
  ```

Similarly, for settings diagnostics:

- Extract FAISS-specific diagnostics from `SettingsService.diagnostics()` into a helper
  that consults the runtime instead of re-reading `CHL_SEARCH_MODE`.
- `SettingsService` still owns DB, disk, credentials diagnostics (shared across modes).

**Result:** The only modules that “know” how FAISS behaves in each mode are the
mode-specific runtime implementations, not every caller.

### 4.5. Mode-specific operations behavior

Keep `OperationsService` as the shared orchestration layer but delegate mode-specific
behavior:

- Introduce a small adapter interface:

  ```python
  # src/modes/base.py
  class OperationsModeAdapter(Protocol):
      def can_run_vector_jobs(self) -> bool: ...
      def on_reembed_requested(self, session, operations_service) -> dict: ...
  ```

- Implementations:
  - `sqlite_only` adapter:
    - `can_run_vector_jobs()` → `False`.
    - `on_reembed_requested()` returns a skipped payload with CPU-only messaging (as
      currently done in `_vector_mode_enabled` error path).
  - `vector` adapter:
    - `can_run_vector_jobs()` → `True`.
    - `on_reembed_requested()` performs the current marking + sync triggering logic.

- Wire the adapter into `OperationsService` at construction time (or via dependency
  provider using `config.search_mode`).

**Result:** All vector-specific operations logic lives in the vector mode adapter; CPU
mode logic lives in the cpu adapter. `OperationsService` calls the adapter regardless of
mode and does not need direct access to `CHL_SEARCH_MODE`.

### 4.6. UI and templates: per-mode surfaces

The UI can follow the same “shared core + mode-specific overlays” pattern.

**Shared:**

- `src/web/templates/base.html`
- Core partials that are truly mode-agnostic:
  - `partials/flash.html`
  - `partials/diagnostics_panel.html` (for DB/disk/credentials, not FAISS).

**Mode-specific:**

- Settings:
  - Keep:
    - `settings.html` → treat as **GPU/semantic** settings template.
    - `settings_cpu.html` → **CPU-only** settings template.
  - Extract any CPU/GPU-specific text from `partials/settings_onboarding.html` into:
    - `partials/settings_onboarding_gpu.html`
    - `partials/settings_onboarding_cpu.html`
  - `settings.html` includes `settings_onboarding_gpu.html`; `settings_cpu.html`
    includes `settings_onboarding_cpu.html`.

- Operations:
  - Split operations into two templates:
    - `operations_gpu.html` – current layout with models card and FAISS controls.
    - `operations_cpu.html` – simplified operations view without FAISS/embedding
      sections.
  - Keep a thin router wrapper:

    ```python
    # src/api/routers/ui.py
    template_name = "operations_cpu.html" if config.is_cpu_only() else "operations_gpu.html"
    ```

  - For truly shared content (queue, jobs list), use partials:
    - `partials/ops_queue_card.html`
    - `partials/ops_jobs_card.html`
    - `partials/ops_onboarding_gpu.html`
    - `partials/ops_onboarding_cpu.html`

**Result:** CPU-only UI work is done in CPU-only templates/partials; GPU work lives in
their counterparts. The only cross-mode branching in Python code is choosing the
template name.

### 4.7. Docs and manuals: per-mode tracks

Introduce a mode-focused docs structure:

- `doc/modes/overview.md` – short explanation of modes and when to choose each.
- `doc/modes/cpu/user_guide.md` – CPU-only operational guide (based on
  `doc/cpu_only_user.md` + relevant slices of `doc/manual.md`).
- `doc/modes/cpu/implementation_plan.md` – move/rename `doc/cpu/implementation_plan.md`
  here (or link as-is).
- `doc/modes/gpu/user_guide.md` – GPU/semantic operational guide, including model
  selection, FAISS snapshots, etc.

At the top level:

- Keep `doc/manual.md` as a neutral entry point that links to the mode-specific guides
  instead of embedding full CPU/GPU narratives inline.

Guidelines:

- Keep root `evaluator.md` as the general evaluator guide.
- Keep `evaluator_cpu.md` as the CPU-only evaluator guide.
- Optionally add a lightweight `evaluator_gpu.md` (or treat `evaluator.md` as that) and
  route based on `config.search_mode` (as already implemented in
  `src/api/routers/guidelines.py`).

**Result:** CPU docs can be edited in their own files without needing to touch GPU docs,
and vice versa; the manual just links to them.

---

## 5. Implementation Plan (Incremental)

An incremental refactor avoids large bang-bang changes and keeps the system shippable
between steps.

### Phase 1 – Mode model and config cleanup

- Add `SearchMode` enum and helpers to `src/config.py`.
- Replace direct `os.getenv("CHL_SEARCH_MODE", ...)` reads in services with
  `config.search_mode` access (start with new code paths, then migrate existing ones).
- Add tests in `tests/test_config.py` to assert enum mapping and helper behavior.

### Phase 2 – Mode runtime package and API server wiring

- Add `src/modes/base.py` and stub `ModeRuntime`.
- Implement `build_runtime` for `sqlite_only` and vector modes, initially copying
  logic from `src/api_server.py` without behavioral changes.
- Update `src/api_server.py` to use `build_mode_runtime(config)` and delete the inline
  `if search_mode == "sqlite_only"` block.
- Add tests (or smoke checks) to ensure:
  - API server starts in both modes.
  - `SearchService.primary_provider_name` is as expected per mode.

### Phase 3 – Health and diagnostics adapters

- Move FAISS/embedding health logic from `src/api/routers/health.py` into methods on
  `ModeRuntime` (or a dedicated `HealthModeAdapter` in `src/modes/...`).
- Update `health.py` to call the runtime adapter.
- Refactor `SettingsService.diagnostics()` to:
  - Keep DB/disk/credentials as-is.
  - Delegate FAISS/embedding diagnostics to the appropriate mode adapter.
- Add/extend tests for `/health` and diagnostics in both modes (existing tests can be
  adapted to assert the new behavior).

### Phase 4 – Operations mode adapter

- Introduce `OperationsModeAdapter` in `src/modes/base.py`.
- Implement adapters in `src/modes/sqlite_only/operations.py` and
  `src/modes/vector/operations.py`.
- Update `OperationsService` to receive an adapter and call it for:
  - Re-embed operations.
  - Any new vector-only operations.
- Remove `_vector_mode_enabled()` environment-based checks once the adapter is wired.

### Phase 5 – UI split and partials

- Split `operations.html` into:
  - `operations_gpu.html`
  - `operations_cpu.html`
  with mode-specific banners and cards.
- Split mixed partials into CPU vs GPU variants where appropriate:
  - `partials/settings_onboarding_cpu.html`
  - `partials/settings_onboarding_gpu.html`
  - `partials/ops_onboarding_cpu.html`
  - `partials/ops_onboarding_gpu.html`
- Update `src/api/routers/ui.py` to select templates/partials based on `config.search_mode`.

### Phase 6 – Documentation reorganization

- Create `doc/modes/overview.md`, `doc/modes/cpu/user_guide.md`,
  `doc/modes/gpu/user_guide.md`.
- Move or link existing CPU docs (`doc/cpu_only_user.md`,
  `doc/cpu/implementation_plan.md`) into the CPU mode track.
- Trim `doc/manual.md` to:
  - Provide a high-level “choose your mode” explanation.
  - Link into the mode-specific guides.
- Ensure README and scripts usage strings point to the correct mode-specific docs.

### Phase 7 – Test suites per mode

- Extend pytest configuration (`pyproject.toml`) so we can run:
  - GPU/semantic suite (default `CHL_SEARCH_MODE=auto`).
  - CPU-only suite (`CHL_SEARCH_MODE=sqlite_only`).
- Add or adapt tests to cover:
  - UI template selection.
  - Health and diagnostics per mode.
  - Operations behavior (re-embed vs skipped).

---

## 6. Proposed Folder Structure (Summary)

High-level structure after refactor (only new/changed parts shown):

- `src/config.py`
  - `SearchMode` enum and helpers (`is_cpu_only`, `is_semantic_enabled`).
- `src/modes/`
  - `__init__.py` – `build_mode_runtime(config)`.
  - `base.py`
    - `ModeRuntime`
    - `OperationsModeAdapter` (and optional `HealthModeAdapter`).
  - `sqlite_only/`
    - `runtime.py` – builds CPU-only runtime.
    - `operations.py` – CPU-only operations adapter.
    - `health.py` (optional) – CPU-specific health reporting.
  - `vector/`
    - `runtime.py` – builds GPU/semantic runtime.
    - `operations.py` – vector-enabled operations adapter.
    - `health.py` (optional) – FAISS/embedding health reporting.
- `src/api_server.py`
  - Uses `build_mode_runtime(config)`; no inline mode-specific search wiring.
- `src/services/operations_service.py`
  - Accepts an `OperationsModeAdapter` instead of reading `CHL_SEARCH_MODE` directly.
- `src/api/routers/health.py`
  - Delegates FAISS/embedding components to runtime.
- `src/web/templates/`
  - `settings.html` – GPU/semantic settings.
  - `settings_cpu.html` – CPU-only settings.
  - `operations_gpu.html` – GPU/semantic operations dashboard.
  - `operations_cpu.html` – CPU-only operations dashboard.
  - `partials/settings_onboarding_gpu.html`
  - `partials/settings_onboarding_cpu.html`
  - `partials/ops_onboarding_gpu.html`
  - `partials/ops_onboarding_cpu.html`
- `doc/`
  - `manual.md` – neutral entry, links to mode-specific docs.
  - `modes/overview.md`
  - `modes/cpu/user_guide.md`
  - `modes/cpu/implementation_plan.md` (or link to `doc/cpu/implementation_plan.md`)
  - `modes/gpu/user_guide.md`

---

## 7. Summary

- The existing codebase already distinguishes CPU-only vs GPU/semantic mode via
  `CHL_SEARCH_MODE` and `config.search_mode`, but the mode-specific behavior is spread
  across services, health checks, UI templates, scripts, and docs.
- By introducing a **mode runtime** abstraction (`src/modes/*`) and splitting UI/docs
  into mode-specific surfaces, we can localize CPU vs GPU behavior while keeping the
  shared database and config untouched.
- The proposed refactor is incremental and backwards-compatible, and it should
  substantially reduce the need to “update CPU and then GPU” in the same file for most
  future changes.

