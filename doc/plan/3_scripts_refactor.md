# Scripts Refactor Plan (Curation)

## Goal
Create a clean scripts/curation structure that:
- Keeps existing experience curation workflows working.
- Adds skill curation alongside experience curation.
- Allows **one-command** merge and **one-command** overnight runs for both domains.

## Constraints
- Do **not** break existing experience workflows; update paths/imports together.
- Direct moves are allowed, but must be validated with tests.
- Keep common utilities truly domain-agnostic.

## Proposed Structure
```
scripts/curation/
  common/
    io.py
    normalize.py
    validators.py
    prompt_utils.py
    decision_logging.py
    merge_all.py            # NEW: orchestrate exp+skills merge
    overnight_all.py        # NEW: orchestrate exp+skills overnight
  experience/
    merge/
    overnight/
    analysis/
    prepass/
    index/
  skills/
    import/
    export/
    dedupe/
    merge/
    overnight/
    index/
```

### Design Notes
- **Domain-specific logic** lives under `experience/` or `skills/`.
- **Shared orchestration** lives under `common/`:
  - `merge_all.py` runs both experience merge and skills merge in sequence.
  - `overnight_all.py` runs both experience overnight loop and skills overnight loop in sequence.
- Experience scripts move directly into `experience/` (no legacy path wrappers).

## One-Command Orchestration (Carlos Workflow)
Carlos runs two commands before bed:

1) Merge both domains:
```
python scripts/curation/common/merge_all.py
```
2) Run overnight curation for both domains:
```
python scripts/curation/common/overnight_all.py
```

### Behavior
- Each orchestrator logs per-domain status and continues if one domain fails (configurable).
- Default is sequential execution to avoid GPU/LLM contention.
- Optional flag `--parallel` can be added later if needed.

## Migration Plan (Direct Move + Validation)
1) Move experience scripts into `scripts/curation/experience/` (merge, overnight, prepass, analysis).
2) Update all hardcoded paths and docs to the new locations.
3) Add a lightweight test program to validate entrypoints/imports.
4) Add `common/merge_all.py` and `common/overnight_all.py` (experience-only at first, then skills).

## Risks
- If one domain fails, overall job may be marked failed. Mitigate by
  capturing per-domain errors and returning a combined report.
- Mixed resource usage (embedding/reranker). Keep sequential execution by default.

## Success Criteria
- Carlos can run merge + overnight with **two commands**.
- Experience curation continues to work unchanged.
- Skills curation integrates without duplicating infrastructure.
