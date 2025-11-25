# Phase 2 Validation Plan

## Goal

Validate whether instruction-aware reranking (generation mode) improves over embedding-based reranking (current Phase 1) before full implementation.

## Current State

- ✅ Phase 1: Two-phase query parsing implemented and working
- ✅ Reranker: Qwen3-Reranker-0.6B-GGUF loaded in **embedding mode**
- ❌ Instruction-aware: NOT implemented yet (requires generation mode)

## Key Question

**Does switching from embedding-based to instruction-aware reranking provide meaningful improvement?**

If yes → Implement Phase 2 fully
If no → Stick with Phase 1 (embedding-based is simpler and faster)

## Validation Strategy

### Step 1: Verify Technical Feasibility

**Goal**: Confirm we can use Qwen3-Reranker in generation mode with logprobs

**Actions**:
1. Create test script to load model in generation mode (not embedding mode)
2. Test proper Qwen3 template structure from paper
3. Verify we can extract yes/no token probabilities
4. Check inference latency (must be <2s for 40 documents)

**Success Criteria**:
- ✅ Model loads successfully in generation mode
- ✅ Can generate text with proper template
- ✅ Can extract logprobs for "yes" and "no" tokens
- ✅ Latency acceptable (<50ms per document)

**If this fails**: Cannot proceed with Phase 2, stick with Phase 1

### Step 2: Small-Scale Comparison

**Goal**: Compare embedding vs instruction-aware on 10 test queries

**Test Queries** (representative of real usage):
1. Implementation task: `[SEARCH] authentication patterns [TASK] Implement OAuth2 with refresh tokens`
2. Troubleshooting: `[SEARCH] performance debugging [TASK] Fix slow database queries in production`
3. Design: `[SEARCH] schema design patterns [TASK] Design multi-tenant database with isolation`
4. Unclear requirements: `[SEARCH] specification workflow [TASK] Write spec but requirements not clear`
5. Access control: `[SEARCH] permission patterns [TASK] Different user roles see different data`
6. Migration: `[SEARCH] migration strategies [TASK] Migrate from MySQL to Postgres with zero downtime`
7. Testing: `[SEARCH] test patterns [TASK] Write end-to-end tests for checkout flow`
8. Refactoring: `[SEARCH] refactoring patterns [TASK] Extract reusable components from legacy code`
9. Deployment: `[SEARCH] deployment strategies [TASK] Roll out breaking changes gradually`
10. Error handling: `[SEARCH] error handling patterns [TASK] Gracefully handle third-party API failures`

**For each query**:
1. Run with **embedding-based reranking** (current Phase 1)
   - Record top-5 results with scores
2. Run with **instruction-aware reranking** (Phase 2 prototype)
   - Use proper Qwen3 template with `<think>` reasoning
   - Use generic instruction: "Determine if this provides practical guidance for the task"
   - Record top-5 results with scores
3. **Manual evaluation**: Which top-5 is more relevant?
   - Rate each result: Highly Relevant (2), Relevant (1), Not Relevant (0)
   - Calculate nDCG@5 for both methods

**Success Criteria**:
- Instruction-aware shows **>10% improvement** in nDCG@5 over embedding-based
- At least 7/10 queries show improvement

**If this fails**: Phase 2 not worth the complexity, stick with Phase 1

### Step 3: Test Instruction Variations (If Step 2 Succeeds)

**Goal**: Understand which instruction approach works best

**Variants to test** (on same 10 queries):

A. **Generic instruction** (baseline):
```
Determine if this experience or manual provides practical guidance for the task
```

B. **Perspective-based instruction**:
```
From a developer's perspective, determine if this provides actionable patterns for the task
```

C. **Transfer-learning emphasis**:
```
Determine if this demonstrates applicable patterns, even if specific technologies differ
```

D. **Task-type-specific** (example for implementation):
```
From a developer implementing a feature, determine if this demonstrates code structures or architectural decisions
```

**Evaluation**:
- Compare nDCG@5 across all variants
- Identify which performs best
- Check if task-specific beats generic

**Decision**:
- If generic is best → Use single instruction
- If task-specific is best → Implement task-type detection
- If perspective matters → Include role-based framing

### Step 4: LLM-Generated Instructions (If Step 3 Shows Benefit)

**Goal**: Test if LLM can craft better instructions than our hardcoded ones

**Approach**:
1. For each test query, ask Generator LLM to craft an instruction
2. Prompt: "Given this task: {TASK}, craft an instruction for a relevance judge to identify helpful experiences"
3. Use LLM-generated instruction in reranking
4. Compare vs best hardcoded instruction from Step 3

**Success Criteria**:
- LLM-generated instructions ≥ hardcoded performance
- Instructions are consistent (not wildly different each time)

**Decision**:
- If LLM-generated is better → Add `[INSTRUCT]` to query format
- If hardcoded is better → Use fixed instruction(s)

## Test Data Requirements

**What we have**: 14 experiences, 10 manuals across multiple categories

**What we need**:
- Diverse enough to test 10 queries
- Categories: PGS (8 exp), DSD (5 exp), FTH (1 exp)
- May need to add more test data if coverage is insufficient

**Mitigation**: If real data is too sparse, document which hypotheses we couldn't test

## Implementation Order (If Validation Passes)

1. **Step 1 passes** → Create minimal prototype for Step 2
2. **Step 2 passes** → Decide to proceed with Phase 2 implementation
3. **Step 3 results** → Determine instruction strategy
4. **Step 4 results** → Finalize query format (with/without [INSTRUCT])

## Output: Validation Report

Document should contain:
1. **Step 1 Results**: Technical feasibility (pass/fail)
2. **Step 2 Results**: Comparison table (embedding vs instruction-aware)
   - nDCG@5 scores
   - Example queries where each method wins
   - Failure cases
3. **Step 3 Results** (if applicable): Instruction variant comparison
4. **Step 4 Results** (if applicable): LLM-generated vs hardcoded
5. **Recommendation**: Go/No-Go for Phase 2 full implementation
6. **If Go**: Specific design decisions (instruction strategy, format)

## Timeline Estimate

- **Step 1**: 2-4 hours (model setup, template testing, logprob extraction)
- **Step 2**: 4-6 hours (run 10 queries x 2 methods, manual evaluation)
- **Step 3**: 2-3 hours (test 4 variants on 10 queries)
- **Step 4**: 2-3 hours (LLM generation + comparison)

**Total**: ~10-16 hours over 2-3 days

## Risk Mitigation

**Risk**: Step 1 fails (cannot extract logprobs from GGUF model)
- **Mitigation**: Check llama-cpp-python documentation first
- **Fallback**: Try different model format (not GGUF)

**Risk**: Step 2 shows no improvement
- **Outcome**: Document why, stick with Phase 1
- **Value**: Saves time not implementing Phase 2

**Risk**: Results are inconclusive (small differences)
- **Decision Rule**: Require >10% improvement to justify complexity
- **Outcome**: If <10%, stick with simpler Phase 1

## Next Steps

1. Start with Step 1: Technical feasibility test
2. If successful, create test script for Step 2
3. Run validation experiments
4. Document results and make Go/No-Go decision

---

**Status**: Ready to begin Step 1 - Technical Feasibility Test
