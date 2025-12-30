# Manual → Skill Terminology Refactor (Concise Plan)

## Goal
Rename “manual” to “skill” across docs and system while keeping data safe and users unblocked.

**Final terminology**
- Experience = atomic lesson
- Skill = comprehensive workflow (formerly “manual”)

---

## Scope (What changes)
- **DB & storage**: table/model/repo names (`category_manuals` → `category_skills`)
- **DTO/API/MCP**: enums, payload models, route params, tool help text
- **Import/Export**: worksheet + CSV filenames
- **Docs**: docs and README
- **Data files**: `data/curation/approved/manuals.csv` → `skills.csv`

---

## Phases

### Phase 1 — Docs & User-Facing Text (no behavior change)
**Goal:** Make terminology consistent for users while backend stays “manual”.

Tasks
- Update docs/README to “skill” + add a short glossary note (“skills were previously called manuals”).
- Update MCP tool descriptions + API docstrings to “skill”.
- Add a short internal note: “manual is legacy term” in relevant code.

Validation
- `rg "manual" doc/` and quick doc pass for consistency.

Risks
- Doc/code drift → mitigated by glossary + internal note.

---

### Phase 2 — DB & API Migration
**Goal:** Align implementation with “skill”.

Prereqs
- Phase 1 complete
- Migration + rollback plan ready

Tasks
- DB migration: rename table, verify FKs/indexes, backup + rollback script.
- Code: rename schema/repo/DTO/API/MCP to “skill”.
- Import/Export: worksheet + CSV filename updates.
- Data file rename: `manuals.csv` → `skills.csv`.
- Rebuild derived data (e.g., embeddings/FAISS) if applicable.

Validation
- CRUD, import/export, search, MCP, regression tests.

Risks
- Data loss, broken integrations, import/export failures → mitigated by backup, tests, rollback.

---

## ID Prefix (Decision)
**Recommendation:** Keep `MNL-` for now to avoid cascading ref updates. Revisit later if needed.

---

## Backward Compatibility (Decision)
**Decision:** No runtime compatibility shims. We only accept old CSV/worksheet names **on import**; exports always use the new “skills” name.

---

## Success Criteria
- Docs + README consistently use “skill”.
- Migration completes without data loss.
- CRUD + import/export + search + MCP pass.
- Docs match implementation.

---

## Open Questions
- Major version bump (v1.0 → v2.0) or just breaking change flag?

---

## Next Steps
1. Decide on version bump policy (optional).
2. Execute Phase 1 (docs + user-facing text).
3. Prepare Phase 2 migration + rollback scripts.
