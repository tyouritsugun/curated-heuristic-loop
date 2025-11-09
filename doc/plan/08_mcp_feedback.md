# MCP Toolset Feedback (v1)

This document captures feedback and suggestions for improving the CHL MCP toolset based on direct interaction and a review of `doc/concept.md`.

### 1. Critical: Search Inconsistency After Write/Update/Delete

- **Observation:** Database investigation confirms that when an entry is created or updated, the `embedding_status` column for that row is correctly set to `pending` in the `experiences` and `category_manuals` tables. However, the expected background job that should process these pending entries is not being triggered. This failure to automatically process the queue means the FAISS index is not updated with new content, causing semantic search to become progressively stale.
- **Impact:** This is the most significant issue observed. It creates a confusing and inconsistent user experience. An agent or user cannot find content they just created, leading them to believe the write operation failed or to create duplicate entries.
- **Suggestion:**
    - **Immediate Solution:** Modify the response of `write_entry`, `update_entry`, and `delete_entry` to include a clear warning. The message should state that indexing is in progress and will take several seconds, during which semantic search for the new content will not be available. For example: `"message": "Experience created successfully. Indexing is in progress and may take up to 15 seconds. Semantic search will not reflect this change until indexing is complete."`
    - **Long-term:** Introduce an optional, asynchronous indexing mechanism. A new MCP tool like `rebuild_index_for_entries(ids: list[str])` could be exposed to incrementally update the FAISS index for specific entries without a full rebuild. This provides a middle ground between immediate-but-slow writes and the current high-latency operator workflow.

### 2. High-Impact: Missing "Decision Hints" on Write

- **Expected Behavior (from `concept.md`):** According to the "Dedup & Decision Hints" section in `concept.md`, the `write_entry` tool should perform an automatic similarity search upon being called. The response is expected to contain not only the new entry's ID but also a list of the most similar existing entries (top-k matches), along with guidance on whether to keep the new entry, refactor it, or merge it. This serves as a final, automatic quality control check.
- **Actual Behavior:** The `write_entry` tool only returns a simple success message with the new entry's ID (e.g., `{"success":true,"entry_id":"...","message":"..."}`). It does not provide any similarity matches or decision guidance in its response.
- **Impact:** The absence of this write-time check means a key quality control step, designed to prevent content duplication, is missing. It places the full burden of deduplication on the user/agent's initial, manual search via `read_entries`. This undermines the "Curated" aspect of the loop, where the system is expected to actively assist in preventing content fragmentation.
- **Suggestion:** Implement the decision hints as described in the concept document. The `write_entry` response should be augmented to include a `similar_entries` field containing the top-k matches and a `recommendation` field suggesting the next action.

### 3. Medium-Impact: Opaque Schemas in `write_entry`

- **Observation:** The `write_entry` tool's `data` parameter is a generic dictionary. The required fields for different `entity_type` values (e.g., "experience" vs. "manual") are not defined in the tool's schema. This led to validation errors that were only resolved through trial and error.
- **Impact:** Poor developer/agent experience. It requires guessing the correct data structure, which is inefficient and error-prone.
- **Suggestion:**
    - Follow the standard practice for the `FastMCP` framework (as referenced in its documentation, e.g., `https://gofastmcp.com/servers/tools`) to define the schema for the `data` parameter directly within the tool's docstring. This would involve using structured comments or type annotations that the framework can parse to provide clear, machine-readable definitions for the required fields of each `entity_type` (e.g., 'experience' requires 'title' and 'playbook'; 'manual' requires 'title', 'summary', and 'content').
    - As a fallback, the error message for validation failures should be more specific. Instead of "Content cannot be empty," it should say, "Validation failed for entity_type 'manual': missing required field 'content'."

### 4. Medium-Impact: Inefficient Read-After-Write Workflow

- **Observation:** The `write_entry` and `update_entry` tools return only a success message and an ID. To verify the operation or use the new/updated data, a subsequent `read_entries` call is required.
- **Impact:** This adds unnecessary network latency and complexity to common workflows.
- **Suggestion:** Modify the `write_entry` and `update_entry` tools to return the full entry object in the response payload upon success. This is a standard pattern in many REST APIs and improves efficiency.


