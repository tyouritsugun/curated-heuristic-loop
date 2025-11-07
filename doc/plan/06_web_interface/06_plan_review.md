# Review of Web Interface Plan (06_web_interface)

## Overall Assessment

The plan for the web interface is **excellent**. It is well-structured, technically sound, and follows a logical, incremental delivery path. The breakdown into phases is sensible, building foundational APIs before the UI. The guiding principles and technology choices are clear and appropriate for the project's goals.

The review of the detailed phase documents (`phase0` through `phase3`) confirms that they are coherent and build on each other logically. The progression from API foundations to the final user experience is a solid strategy.

This review identifies a few minor inconsistencies and potentially missing pieces. None of them block execution, but tightening them up now will prevent rework and gives each phase a clearer contract.

---

## Recommendations for Improvement

### 1. Add Explicit Job/Operation History Table

*   **Observation:** `phase3_core_operations_ux.md` mentions showing the last import/export run from "telemetry/job history tables".
*   **Gap:** `phase0_api_foundations.md` only defines `worker_metrics` for real-time signals; it never guarantees durable storage for completed jobs.
*   **Action:** Extend Phase 0 with a `job_history` (or `operations_log`) table capturing `job_id`, `job_type`, `status`, `start_time`, `end_time`, `result_summary`, `triggered_by`, and a pointer to related telemetry. This gives the UI a reliable source for historical cards and eliminates ad-hoc SQL over telemetry.

### 2. Operation Cancellation Endpoint

*   **Observation:** Phase 3 UX calls for cancel buttons on long-running operations.
*   **Gap:** Phase 0 never defines how cancellations work—no endpoint, no lock release semantics.
*   **Action:** Phase 0 should document a `POST /api/operations/{job_id}/cancel` (or similar) endpoint plus a cooperative cancellation mechanism in the background job runner. Even if initial implementation simply marks the job `cancel_requested` and waits for the current step to finish, the contract needs to exist before the UI depends on it.

### 3. Audit Log Schema in Phase 0

*   **Observation:** Phase 2 introduces audit logging requirements.
*   **Gap:** Phase 0 never creates a table/service to persist those events, so Phase 2 would have nowhere to write.
*   **Action:** Amend Phase 0 schema work to include an `audit_log` table (`id`, `timestamp`, `actor`, `source`, `action`, `details_json`). Also note retention policy and exposure endpoint so later phases can render history or export logs.

### 4. Telemetry Naming Consistency

*   **Observation:** Some documents say "telemetry table", others say `worker_metrics` or "metrics table".
*   **Action:** Pick one canonical name (e.g., `worker_metrics` for real-time signals + `job_history` for durable ops) and update references in Phases 0–3 accordingly so developers understand which data source to query.

---

## Conclusion

The plan is very strong. Addressing these points will help ensure that the foundational API built in Phase 0 fully supports all the features planned for the subsequent UI phases.
