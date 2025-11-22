# User Example: Bug Ticket Writing Demo

## Goal

Create a sample dataset that demonstrates CHL's value by showing how it teaches LLMs **project-specific conventions** that aren't in their training data.

## The Problem We're Solving

Modern LLMs (Claude, GPT-4) already know generic "bug report best practices" from training data. We need to show differentiation:

**Common LLM pitfall**: When user says "I found a bug, here's the error...", LLM immediately rushes to fix code without clarifying intent.

**What user might actually want:**
- Write a bug ticket (not fix it)
- Document the issue
- Investigate/reproduce first
- Discuss root cause

## The Solution: Project-Specific Bug Report Format

Instead of generic advice, create a **fictional project with specific requirements** that aren't in LLM training data.

### Fictional Project: "DataPipe CLI"

A fictional ETL/data processing tool with specific bug reporting conventions.

### Sample Content Structure

#### Category: `TMG` (Ticket Management)

**Manual entries:**
1. "DataPipe bug report template"
   - Required sections: Summary, Environment, Reproduction Steps, Expected vs Actual, Logs
   - Must include: Docker container ID, microservice name, request ID

2. "DataPipe debugging checklist"
   - Pre-filing checks: Run `datapipe doctor`, check Redis connectivity, verify config

**Experience entries (10-15 examples):**
1. "Always run `datapipe doctor` before filing production bugs"
2. "Include Docker container ID from `docker ps` output"
3. "Specify which microservice: auth/api/worker/scheduler"
4. "Attach last 50 lines from /var/log/datapipe/app.log"
5. "For auth bugs: paste JWT payload (redact sensitive fields)"
6. "For API bugs: include X-Request-ID header value"
7. "For data bugs: include Redis key pattern (use SCAN, not KEYS)"
8. "Test in staging environment first, note if reproducible there"
9. "Include `datapipe version` output"
10. "Specify if bug is intermittent (<100%) or deterministic (100%)"
11. "Add context: what were you trying to accomplish?"
12. "Check existing tickets first: search by error message"
13. "For performance issues: attach profiler output or slow query log"
14. "For crashes: include stack trace with full context (not truncated)"
15. "Mark severity: P0 (blocker), P1 (critical), P2 (normal), P3 (minor)"

### Demo Flow

1. **User runs buggy script**: `./demo/datapipe-broken.py`
   - Script fails with realistic error (Redis connection failed, auth token expired, etc.)
   - Generates error output to paste into LLM

2. **Test A: Without CHL** (baseline behavior)

   **How to simulate "Without CHL" state:**
   - **Option 1** (easiest): Start a fresh chat session in your code assistant, don't mention CHL or use any MCP tools
   - **Option 2**: Temporarily disable CHL MCP server in your MCP config and restart the assistant
   - **Option 3**: Use a different code assistant that doesn't have CHL configured

   **Expected baseline behavior:**
   ```
   User: "I found a bug in DataPipe, here's the error: [paste]"
   LLM: "Let me fix that for you!"
        [immediately starts reading code files and attempting fixes]
   ```

3. **Test B: With CHL** (improved behavior)

   **Setup:**
   - Ensure CHL MCP server is running (`./start-chl.sh`)
   - Ensure sample TMG data is imported (from Step 3 or Step 7)
   - Start a fresh chat session in your code assistant

   **Expected improved behavior:**
   ```
   User: "I found a bug in DataPipe, here's the error: [paste]"
   LLM: [calls read_entries to check TMG manual and experiences]
        "I see the error. Would you like me to:
         1. Fix it immediately
         2. Write a bug ticket following DataPipe's format
         3. Investigate the root cause first

         If you'd like a bug ticket, I'll need:
         - Docker container ID (from `docker ps`)
         - Which microservice is affected
         - Output from `datapipe doctor`
         - Last 50 lines from /var/log/datapipe/app.log"
   ```

4. **Compare Results**
   - Without CHL: LLM rushes to fix code without understanding your intent
   - With CHL: LLM clarifies what you want and follows project-specific conventions

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
7. [ ] Create 10-15 DataPipe-specific experiences (TMG category)
8. [ ] Create buggy demo script: `demo/datapipe-broken.py`
9. [ ] Create Google Sheets template with sample TMG content

### Phase 3: Integration
10. [ ] Add Step 7 to README: "Try the Demo"
    - Import sample Google Sheet
    - Run demo script
    - Test with and without CHL
11. [ ] Update Step 3 in README to mention optional sample sheet import

## Success Criteria

User can complete the A/B test:

### Test A (Without CHL):
1. Start fresh chat session without CHL
2. Run `./demo/datapipe-broken.py` to get error output
3. Paste error to LLM: "I found a bug in DataPipe, here's the error: [paste]"
4. Observe: LLM immediately tries to fix code without clarifying intent

### Test B (With CHL):
5. Import the sample Google Sheet into CHL (if not done in Step 3)
6. Ensure CHL MCP server is running
7. Start fresh chat session (with CHL MCP enabled)
8. Run `./demo/datapipe-broken.py` again
9. Paste same error to LLM: "I found a bug in DataPipe, here's the error: [paste]"
10. Observe: LLM **clarifies intent** (fix/ticket/investigate) instead of rushing to code
11. Choose "write a bug ticket"
12. Observe: LLM follows DataPipe-specific format (asks for container ID, microservice, datapipe doctor output, logs)

### Understanding:
User realizes: "CHL taught the LLM my team's conventions! Without CHL, it just rushes to fix. With CHL, it asks the right questions and follows our process."

## Decisions

1. ✅ **Buggy script**: Create a real working Python script that users can run to see the difference
2. ✅ **Demo scenarios**: One example is sufficient for demonstration
3. ✅ **README integration**: Add as final step after Step 6 (MCP installation complete) - "Step 7: Try the Demo"
4. ✅ **FastMCP instructions**: Use both mechanisms for maximum compatibility:
   - Set `mcp.instructions` property for clients that support it
   - Add concise description to FastMCP constructor name/description for universal visibility
