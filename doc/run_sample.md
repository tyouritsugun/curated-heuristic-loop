# Running the Demo

This demo shows how CHL teaches LLMs project-specific conventions that aren't in their training data. Using a fictional "DataPipe" project, you'll see the difference between generic bug reporting vs. team-specific ticket requirements.

## What You'll See

**Without CHL:**
- LLM rushes to fix code when user reports a bug
- LLM writes incomplete tickets missing required artifacts (Run ID, pipeline stage, logs)
- Generic approach lacks team-specific process awareness

**With CHL:**
- LLM clarifies user intent first (fix vs. document vs. investigate)
- LLM enforces project-specific ticket requirements
- LLM asks for required artifacts before drafting tickets

**The demo takes ~10 minutes end-to-end.**

## Prerequisites

Before running the demo, complete the main installation steps in the README:
1. Install API server (CPU or GPU mode)
2. Configure environment (.env file)
3. Initialize database
4. Start API server
5. Install MCP server

The demo script `scripts/demo_datapipe_bug.py` is included in the repository and ready to run.

## Sample Data

The demo requires TMG (Ticket Management) category data with DataPipe bug reporting guidance. This data is automatically seeded when you run `python scripts/setup-cpu.py` or `python scripts/setup-gpu.py`.

**Verify the data is present:**
1. Make sure that you set `IMPORT_SPREADSHEET_ID` in your `.env`, which value is same as in `.env.sample`. 
2. Import your database via Settings → "Import Spreadsheet". Note, this will reset your local data with the data in the spreadsheet.
3. Check the Experiences worksheet - you should see 10 TMG entries about bug reporting
4. Check the Manuals worksheet - you should see the "Bug Report Template" entry

## Running the Demo

### Step 1: Generate Bug Artifacts

Run the demo script to simulate a DataPipe failure:

```bash
# Activate your API server venv first
source .venv-cpu/bin/activate  # Or .venv-apple / .venv-nvidia

# Run the buggy script
python scripts/demo_datapipe_bug.py
```

The script will:
- Fail with a realistic error (missing data file)
- Print the error message to console
- Save artifacts to:
  - `data/output/run_meta.json` (Run ID, pipeline stage, timestamp)
  - `data/output/app.log` (error details and stack trace)

**Copy the error message from the console** - you'll paste this into your AI assistant.

### Step 2: Test A - Without CHL (Baseline)

This test demonstrates how generic LLMs behave without project-specific guidance.

**Setup:**
- Start a fresh chat session in your code assistant
- Do NOT mention CHL or use any MCP tools
- Or temporarily disable CHL MCP server and restart your assistant

**Test sequence:**

1. **Pitfall #1: Rushing to code**
   ```
   You: "I found a bug in DataPipe, here's the error: [paste error]"
   ```

   **Expected behavior:** LLM immediately starts reading code files and attempting fixes without asking what you want.

2. **Pitfall #2: Incomplete ticket**
   ```
   You: "Actually, don't fix it. Write a bug ticket for this error instead."
   ```

   **Expected behavior:** LLM writes a generic ticket like:
   ```
   Title: DataPipe validation error
   Description: The script failed with a validation error...
   Steps to reproduce: Run datapipe-broken.py
   ```

   **Missing:** Run ID, pipeline stage, log excerpt (even though the script printed where artifacts were saved)

### Step 3: Test B - With CHL (Improved)

This test shows how CHL enforces project-specific conventions.

**Setup:**
- Ensure CHL MCP server is running (`./start-chl.sh`)
- Ensure TMG sample data is imported (verify via export)
- Start a fresh chat session in your code assistant (with CHL enabled)

**Test sequence:**

1. **Fix #1: Clarifies intent first**
   ```
   You: "I found a bug in DataPipe, here's the error: [paste same error]"
   ```

   **Expected behavior:** LLM calls `read_entries(entity_type='manual', category_code='TMG', query='bug report')` and asks:
   ```
   "I see the error. Would you like me to:
    1. Fix it immediately
    2. Write a bug ticket following DataPipe's format
    3. Investigate the root cause first"
   ```

   **Improvement:** LLM asks before acting, not after.

2. **Fix #2: Enforces project requirements**
   ```
   You: "Write a bug ticket."
   ```

   **Expected behavior:** LLM says:
   ```
   "I'll create a DataPipe ticket. First, I need the required artifacts.
    The script printed where it saved them - can you share those paths, or shall I
    look for the metadata JSON and log file in the standard location?"
   ```

   ```
   You: "They're in the printed location."
   ```

   **Expected behavior:** LLM reads `data/output/run_meta.json` and `data/output/app.log`, then writes a complete ticket with:
   - Run ID: DP-20250122-143052 (example)
   - Pipeline Stage: transform
   - Log excerpt included

   **Improvement:** LLM enforces team-specific requirements, uses actual artifacts.


## Understanding the Value

This demo shows how CHL bridges the gap between generic LLM training and your team's specific processes:

1. **Intent Clarification**: CHL teaches the LLM to pause and clarify what the user wants before rushing to code
2. **Process Enforcement**: CHL stores your team's bug reporting conventions (what fields are required, where artifacts live)
3. **Artifact Awareness**: CHL guides the LLM to look for and use project-specific artifacts (Run ID, logs, metadata)

**Key insight:** Without CHL, the LLM gives generic advice. With CHL, it follows your team's actual conventions.

## Next Steps

After running the demo:

1. **Add your own categories**: Create categories for your team's workflows (architecture decisions, code review checklists, deployment procedures, etc.). See [Managing Categories](manual.md#62-managing-categories) for step-by-step instructions.

2. **Write experiences**: As you work, capture what works as atomic experiences:
   - "Always check X before doing Y"
   - "When Z happens, look at W first"
   - "Never do A without B"

3. **Create manuals**: Write process overviews and templates for complex workflows

4. **Share with team**: Export your database to Google Sheets and share the import sheet with teammates

5. **Iterate**: As you use CHL, refine your guidance based on what actually helps

