# User Example: Bug Ticket Writing Demo

## Goal

Create a sample dataset that demonstrates CHL's value by showing how it teaches LLMs **project-specific conventions** that aren't in their training data.

### Why This Matters: The Behavioral Delta

**Without CHL:**
- LLM rushes to fix code when user reports a bug
- LLM writes tickets but ignores required project artifacts (Run ID, pipeline stage, logs)
- Generic approach lacks team-specific process awareness

**With CHL:**
- LLM clarifies user intent first (fix vs. document vs. investigate)
- LLM enforces project-specific ticket requirements
- LLM asks for required artifacts before drafting tickets

**The demo shows this difference in 2 minutes of A/B testing.**

## The Problem We're Solving

Modern LLMs (Claude, GPT-4) already know generic "bug report best practices" from training data. We need to show differentiation:

**Common LLM Pitfall #1: Rushing to Code**
When user says "I found a bug, here's the error...", LLM immediately rushes to fix code without clarifying intent.

**What user might actually want:**
- Write a bug ticket (not fix it)
- Document the issue
- Investigate/reproduce first
- Discuss root cause

**Common LLM Pitfall #2: Incomplete Tickets**
When user says "Write a ticket for this error", LLM writes a generic ticket but ignores project-specific requirements like:
- Run ID from the execution
- Pipeline stage where failure occurred
- Log excerpts from specific files
Even when these artifacts are readily available in the repo.

## The Solution: Project-Specific Bug Report Format

Instead of generic advice, create a **fictional project with specific requirements** that aren't in LLM training data.

### Fictional Project: "DataPipe CLI"

A fictional ETL/data processing tool with specific bug reporting conventions.

### Sample Content Structure

#### Category: `TMG` (Ticket Management)

**Manual entries:**
1. "DataPipe bug report template"
   - Required sections: Summary, Environment, Reproduction Steps, Expected vs Actual, Logs
   - Must include: Run ID, pipeline stage, and a 50-line log excerpt from the most recent run artifacts (the script prints where these files are saved)

2. "DataPipe debugging checklist"
   - Pre-filing checks: Check the run metadata JSON emitted by the demo script for context; review the corresponding log tail for stack traces

**Experience entries (8-10 examples):**
1. "Always include Run ID from the run metadata JSON in ticket header"
2. "Specify pipeline stage from metadata (extract/transform/load/validate)"
3. "Attach last 50 lines from the run's log file"
4. "Include exact error message from log (don't paraphrase)"
5. "Note timestamp from metadata for time-sensitive issues"
6. "Check log for stack trace before filing - include full trace if present"
7. "Specify if bug is intermittent or deterministic (check multiple runs)"
8. "Add context: what operation was being attempted?"
9. "Search existing tickets by Run ID to avoid duplicates"
10. "Mark severity: P0 (data loss), P1 (pipeline blocked), P2 (degraded), P3 (minor)"

**Note:** Default artifact location is `data/output/` for the provided demo script, but the guidance applies to any script that emits run metadata and logs.

### Demo Flow

1. **User runs buggy script**: `python scripts/demo_datapipe_bug.py`
   - Script fails with realistic error (e.g., missing data file)
   - Script prints:
     - Error message to console (for user to paste to LLM)
     - Artifact locations: "Artifacts saved to data/output/run_meta.json and data/output/app.log"
   - Generated artifacts contain:
     - `data/output/run_meta.json`: run_id, pipeline_stage, timestamp
     - `data/output/app.log`: last 50 log lines with stack trace and error details

2. **Test A: Without CHL** (baseline behavior - demonstrates both pitfalls)

   **How to simulate "Without CHL" state:**
   - **Option 1** (easiest): Start a fresh chat session in your code assistant, don't mention CHL or use any MCP tools
   - **Option 2**: Temporarily disable CHL MCP server in your MCP config and restart the assistant
   - **Option 3**: Use a different code assistant that doesn't have CHL configured

   **Test sequence:**

   a. **Pitfall #1: Rushing to code**
   ```
   User: "I found a bug in DataPipe, here's the error: [paste error]"
   LLM: "Let me fix that for you!"
        [immediately starts reading code files and attempting fixes]
   ```
   - **📝 Action**: Save this transcript/response to compare later

   b. **Pitfall #2: Incomplete ticket**
   ```
   User: "Actually, don't fix it. Write a bug ticket for this error instead."
   LLM: [writes generic ticket]
        Title: DataPipe validation error
        Description: The script failed with a validation error...
        Steps to reproduce: Run datapipe-broken.py
        [Missing: Run ID, pipeline stage, log excerpt - even though the script
         printed where the artifacts were saved]
   ```
   - **📝 Action**: Save this ticket output to compare later

3. **Test B: With CHL** (improved behavior - addresses both pitfalls)

   **Setup:**
   - Ensure CHL MCP server is running (`./start-chl.sh`)
   - Ensure sample TMG data is imported (from Step 3 or Step 7)
   - Start a fresh chat session in your code assistant

   **Test sequence:**

   a. **Fix: Clarifies intent first**
   ```
   User: "I found a bug in DataPipe, here's the error: [paste same error]"
   LLM: [calls read_entries(entity_type='manual', category_code='TMG', query='bug report')]
        "I see the error. Would you like me to:
         1. Fix it immediately
         2. Write a bug ticket following DataPipe's format
         3. Investigate the root cause first"
   ```
   - **Improvement**: LLM asks before acting, not after

   b. **Fix: Enforces project requirements**
   ```
   User: "Write a bug ticket."
   LLM: [calls read_entries(entity_type='experience', category_code='TMG', query='datapipe ticket requirements')]
        "I'll create a DataPipe ticket. First, I need the required artifacts.
         The script printed where it saved them - can you share those paths, or shall I
         look for the metadata JSON and log file in the standard location?"

   User: "They're in the printed location."

   LLM: [reads the run metadata JSON and log file from the artifact paths]
        [writes complete ticket with Run ID: DP-20250122-143052,
         Pipeline Stage: transform, log excerpt included]
   ```
   - **Improvement**: LLM enforces team-specific requirements, uses actual artifacts

4. **Compare Results**

   **Without CHL:**
   - Pitfall #1: Rushes to fix without clarifying intent
   - Pitfall #2: Writes incomplete ticket missing required artifacts

   **With CHL:**
   - Asks user intent before taking action
   - Enforces project-specific ticket format
   - Prompts for required artifacts (Run ID, pipeline stage, logs)
   - Produces complete, actionable ticket

## LLM Instruction Updates: Layered Defense Strategy

We can't rely on a single mechanism since different MCP clients support different features. Use a layered approach:

### Layer 1: FastMCP Constructor + Instructions Property
**File**: `src/mcp/server.py:37`

**Current:**
```python
mcp = FastMCP("CHL MCP Server")
```

**Proposed (dual approach for maximum compatibility):**
```python
# Concise description in constructor (always visible to all clients)
mcp = FastMCP("CHL: Manual & experience toolset - clarify task intent before action")

# Detailed instructions via property (for clients that support it)
# This will be set after tool registration in init_server()
```

**After tool registration (in init_server() function):**
```python
# Set detailed instructions for MCP clients that support it
mcp.instructions = json.dumps(build_handshake_payload())
```

**Rationale**:
- FastMCP constructor name is always visible (universal compatibility)
- `mcp.instructions` property provides full details for supporting clients
- Both mechanisms work together without conflicts

### Layer 2: AGENTS.md.sample (User-configured)
**File**: `AGENTS.md.sample`

Add to "During Tasks" section:
```markdown
## During Tasks
- **Clarify task intent first**: When user mentions bugs/errors/problems, don't immediately rush to fix code. Ask yourself: Are they asking to fix, document, file a ticket, or investigate? If unclear, ask: "Would you like me to [fix this / write a bug ticket / investigate further]?"
- Use categories: call `list_categories()` first; if the request includes one, honor it.
- ...
```

### Layer 3: generator.md (Workflow-loaded)
**File**: `generator.md`

Add new **"Step 0: Clarify Task Intent"** before current "1. Align on the request":

```markdown
### 0. Clarify task intent (before rushing to code)

When user mentions bugs, errors, or problems, pause and clarify what they want:

**Ask yourself:**
- Are they asking me to: fix code, write a bug ticket, investigate, or document?
- Did they explicitly request code changes, or just reporting an issue?

**Red flags that suggest "don't rush to code":**
- User says "I found..." / "I noticed..." / "There's a bug..."
- User pastes error output without explicit fix request
- Context suggests documentation task (e.g., after discussing tickets)

**If unclear, ask explicitly:**
"Would you like me to: (1) Fix this issue, (2) Write a bug ticket, or (3) Investigate further?"

**Special case - Bug tickets:**
Check TMG category for team's bug report format before writing tickets:
`read_entries(entity_type='manual', category_code='TMG', query='bug report template')`
```

### Layer 4: build_handshake_payload (Bonus if client supports)
**File**: `src/mcp/core.py:175`

Add to return payload:
```python
return {
    "version": SERVER_VERSION,
    "workflow_mode": workflow_mode_payload(),
    "tool_index": TOOL_INDEX,
    "search": search_payload,
    "categories": categories_data.get("categories", []),
    "mode": {...},
    "instructions": {
        "task_clarification": (
            "Clarify user's intent before taking action when they report bugs/errors. "
            "They may want to: fix code, write a ticket (check TMG category), "
            "investigate, or document. Don't assume they want an immediate code fix."
        )
    }
}
```

## Implementation Steps

### Phase 1: LLM Instruction Layers
1. ✅ Document the plan (this file)
2. [ ] Update FastMCP constructor name to include "clarify task intent" (Layer 1)
3. [ ] Update AGENTS.md.sample with task clarification (Layer 2)
4. [ ] Update generator.md with Step 0: Clarify Intent (Layer 3)
5. [ ] Update build_handshake_payload with instructions field (Layer 4)

### Phase 2: Demo Content Creation
6. [ ] Create sample DataPipe bug report manual (TMG category)
7. [ ] Create 8-10 DataPipe-specific experiences (TMG category) - simplified from 15
8. [ ] Create buggy demo script: `scripts/demo_datapipe_bug.py`

**Script Design (Keep it Simple):**

```python
#!/usr/bin/env python3
"""DataPipe Demo - Simulates a data pipeline bug for CHL demonstration."""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

def main():
    # Generate run ID
    run_id = f"DP-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"

    # Pipeline stages: extract -> transform -> load -> validate
    pipeline_stage = "transform"

    # Create output directory
    output_dir = Path("data/output")
    output_dir.mkdir(parents=True, exist_ok=True)

    # Simulate missing input file error
    input_file = "data/sample_input.csv"

    print(f"[{run_id}] Starting DataPipe at stage: {pipeline_stage}")
    print(f"[{run_id}] Reading input: {input_file}")

    # Write metadata
    metadata = {
        "run_id": run_id,
        "pipeline_stage": pipeline_stage,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

    metadata_path = output_dir / "run_meta.json"
    with open(metadata_path, "w") as f:
        json.dump(metadata, f, indent=2)

    # Simulate error and write log
    error_msg = f"FileNotFoundError: {input_file} does not exist"
    log_lines = [
        f"[{datetime.now().isoformat()}] INFO: DataPipe started",
        f"[{datetime.now().isoformat()}] INFO: Run ID: {run_id}",
        f"[{datetime.now().isoformat()}] INFO: Stage: {pipeline_stage}",
        f"[{datetime.now().isoformat()}] ERROR: {error_msg}",
        "Traceback (most recent call last):",
        '  File "scripts/demo_datapipe_bug.py", line 45, in main',
        f'    with open("{input_file}", "r") as f:',
        f"FileNotFoundError: [Errno 2] No such file or directory: '{input_file}'",
    ]

    log_path = output_dir / "app.log"
    with open(log_path, "w") as f:
        f.write("\n".join(log_lines))

    # Print error to console
    print(f"\n{'='*60}")
    print(f"ERROR: {error_msg}")
    print(f"{'='*60}")
    print(f"\nArtifacts saved to {output_dir}/run_meta.json and {output_dir}/app.log")
    print(f"\nRun ID: {run_id}")
    print(f"Pipeline Stage: {pipeline_stage}")
    print(f"\nSee {log_path} for full stack trace")

    sys.exit(1)

if __name__ == "__main__":
    main()
```

**What the script does:**
- ✅ Generates unique Run ID (matches experience #1)
- ✅ Sets pipeline stage to "transform" (matches experience #2)
- ✅ Creates `data/output/run_meta.json` with run_id, pipeline_stage, timestamp (matches experiences #1, #2, #5)
- ✅ Creates `data/output/app.log` with error message and stack trace (matches experiences #3, #4, #6)
- ✅ Prints error to console for user to paste
- ✅ Prints artifact locations
- ✅ Fails with realistic FileNotFoundError (simple, reproducible)

**What we removed:**
- ❌ source_file field (not needed for simple demo)
- ❌ input_file field in metadata (the input file path is in the error message, that's enough)
- ❌ error_code field (simpler without it)
- ❌ execution_time field (not in core experiences)

9. [ ] Create Google Sheets template with sample TMG content

### Phase 3: Integration
10. [ ] Add Step 7 to README: "Try the Demo"
    - Import sample Google Sheet
    - Run demo script
    - Test with and without CHL
11. [ ] Update Step 3 in README to mention optional sample sheet import

## Success Criteria

User can complete the A/B test demonstrating both pitfalls and fixes:

### Setup
1. Run `python scripts/demo_datapipe_bug.py` once to generate artifacts:
   - `data/output/run_meta.json` (with Run ID, pipeline stage, timestamp)
   - `data/output/app.log` (with error details and stack trace)
2. Note the console error output to paste into LLM

### Test A (Without CHL): Observe Both Pitfalls
3. Start fresh chat session without CHL
4. Paste error: "I found a bug in DataPipe, here's the error: [paste]"
5. **Observe Pitfall #1**: LLM rushes to fix code without asking intent
6. **📝 Save this response**
7. Follow up: "Actually, don't fix it. Write a bug ticket instead."
8. **Observe Pitfall #2**: LLM writes incomplete ticket missing Run ID, pipeline stage, log excerpt (even though script printed artifact locations)
9. **📝 Save this incomplete ticket**

### Test B (With CHL): Observe Both Fixes
10. Import sample TMG data (if not done in Step 3)
11. Ensure CHL MCP server is running (`./start-chl.sh`)
12. Start fresh chat session (with CHL MCP enabled)
13. Paste same error: "I found a bug in DataPipe, here's the error: [paste]"
14. **Observe Fix #1**: LLM clarifies intent (fix/ticket/investigate) instead of rushing to code
15. Choose: "Write a bug ticket"
16. **Observe Fix #2**: LLM asks for artifact locations (metadata JSON and log file) that script printed
17. Let LLM read the files from those locations and generate complete ticket
18. **Compare**: Ticket now includes Run ID, pipeline stage, and log excerpt

### Understanding
User realizes:
- **Pitfall #1 → Fix #1**: CHL taught the LLM to clarify intent before acting
- **Pitfall #2 → Fix #2**: CHL taught the LLM our team's ticket requirements
- **Value**: "Without CHL, generic advice. With CHL, project-specific process enforcement."

## Decisions

1. ✅ **Buggy script**: Create a real working Python script that users can run to see the difference
2. ✅ **Demo scenarios**: One example is sufficient for demonstration
3. ✅ **README integration**: Add as final step after Step 6 (MCP installation complete) - "Step 7: Try the Demo"
4. ✅ **FastMCP instructions**: Use both mechanisms for maximum compatibility:
   - Set `mcp.instructions` property for clients that support it
   - Add concise description to FastMCP constructor name/description for universal visibility
