# Semantic Search Refinement Plan

## Problem Statement

### Current State
The current search implementation uses Qwen3-Reranker but only exploits its embedding capabilities, not its instruction-aware features. The flow is:

1. **FAISS vector search**: Retrieves ~100 candidates using embedding similarity
2. **Reranking**: Uses Qwen3-Reranker in embedding mode to compute cosine similarity
3. **Returns**: Top-k results

**Key Issues:**
- Qwen3-Reranker is instruction-aware (supports custom prompts) but we only use it for embeddings
- Query formulation guidance in `generator.md` focuses on topical keywords rather than task-utility
- Embedding similarity finds topically similar content, but may miss logically relevant patterns
- Example: Query "OAuth2 authentication" finds content WITH "OAuth2" but misses transferable auth implementation patterns from other systems

### The Core Challenge

**Use case**: "To implement this feature, which manual and experience are related?"

This requires:
- **Logical/causal relevance**: Understanding what's helpful for a task, not just topically similar
- **Transferable patterns**: Finding experiences from different domains that demonstrate applicable approaches
- **Task-oriented ranking**: Prioritizing usefulness over keyword overlap

**Current approach** (embedding similarity) is good at:
- Lexical overlap and semantic similarity
- Finding topically related content

**Current approach struggles with**:
- Logical dependencies ("Manual X is prerequisite knowledge for task Y")
- Analogical patterns ("Experience with problem A teaches approach for problem B")
- Task utility ("This is helpful for accomplishing the goal" vs "This mentions similar keywords")

## Proposed Solution: Two-Phase Query Architecture

### Architecture Overview

**Phase 1: FAISS Vector Search (Recall)**
- **Purpose**: Fast retrieval, cast wide net
- **Input**: Short, keyword-focused search phrase
- **Method**: Embedding similarity on existing FAISS index
- **Output**: ~100 candidates
- **Example input**: `"authentication implementation patterns"`

**Phase 2: Reranking (Precision)**
- **Purpose**: Intelligent ranking by task-relevance
- **Input**: Full task context + search phrase
- **Method**: Instruction-aware reranking (future) or enhanced embedding (current)
- **Output**: Top 40 → Top 10 results
- **Example input**: `"To implement OAuth2 login with refresh tokens, which experiences are helpful? Relevant concepts: authentication implementation patterns"`

### Why This Design

**Separation of concerns:**
- **Recall** (Phase 1): Efficiency-focused, doesn't need task understanding
- **Precision** (Phase 2): Quality-focused, only runs on 40 candidates so can afford sophistication

**Query decomposition benefits:**
- Search phrase can be broader/more general → finds transferable patterns
- Task context enables semantic understanding of utility
- LLM can craft different query styles for different purposes

**Backward compatibility:**
- If query doesn't contain special format, use full query for both phases
- Existing behavior preserved as fallback

## Implementation Plan

### Phase 1: Query Format & Parsing (Minimal Change)

**Why now**: Current code (`src/api/gpu/search_provider.py`) still embeds the entire query string for both FAISS and reranking. Without the parser and wiring, none of the two-phase behavior is live.

#### 1.1 Define Query Format

**Required format (strict):**
```
[SEARCH] authentication implementation patterns
[TASK] Implement secure OAuth2 login with refresh tokens
```

**No backward compatibility:** Queries without proper format will raise `ValueError` with helpful error messages to guide the LLM to correct the format.

#### 1.2 Add Query Parser

Create utility function in `src/api/gpu/search_provider.py` (or `src/api/gpu/query_utils.py` if we prefer to keep search_provider slim):

```python
def parse_two_phase_query(query: str) -> tuple[str, str]:
    """
    Parse a two-phase query into (search_phrase, task_context).

    Required format:
    - "[SEARCH] phrase [TASK] context"

    Returns:
        (search_phrase, full_query_for_reranking)

    Raises:
        ValueError: If query doesn't match required format or has empty parts.
                   Error messages guide LLM to correct the format.
    """
    # Check for required markers
    if "[SEARCH]" not in query:
        raise ValueError(
            "Query format error: Missing [SEARCH] marker.\n"
            "Required format: [SEARCH] <short keyword phrase> [TASK] <task description>\n"
            "Example: [SEARCH] authentication implementation patterns [TASK] Implement OAuth2 login\n"
            f"Your query: {query[:200]}"
        )

    if "[TASK]" not in query:
        raise ValueError(
            "Query format error: Missing [TASK] marker.\n"
            "Required format: [SEARCH] <short keyword phrase> [TASK] <task description>\n"
            "Example: [SEARCH] authentication implementation patterns [TASK] Implement OAuth2 login\n"
            f"Your query: {query[:200]}"
        )

    # Parse [SEARCH]/[TASK] format
    parts = query.split("[TASK]", 1)
    search = parts[0].replace("[SEARCH]", "").strip()
    task = parts[1].strip()

    # Validate: both parts must be non-empty
    if not search:
        raise ValueError(
            "Query format error: [SEARCH] phrase is empty.\n"
            "The SEARCH phrase should be 3-6 words combining [process] + [domain].\n"
            "Examples: 'migration planning', 'performance troubleshooting', 'API design'\n"
            f"Your query: {query[:200]}"
        )

    if not task:
        raise ValueError(
            "Query format error: [TASK] context is empty.\n"
            "The TASK should be one sentence describing your goal and constraints.\n"
            "Example: Implement secure OAuth2 login with refresh tokens\n"
            f"Your query: {query[:200]}"
        )

    # Construct full context for reranking
    full_context = f"{task}\n\nRelevant concepts: {search}"
    return (search, full_context)
```

#### 1.3 Modify VectorFAISSProvider.search

In `src/api/gpu/search_provider.py`:

```python
def search(self, session, query: str, entity_type=None, category_code=None, top_k=10):
    """Search using vector similarity with two-phase query support."""

    # Parse query into two phases (new)
    search_phrase, full_context = parse_two_phase_query(query)

    # Phase 1: FAISS with search phrase only
    try:
        query_embedding = self.embedding_client.encode_single(search_phrase)  # CHANGED
    except EmbeddingClientError as exc:
        raise SearchProviderError(f"Failed to generate query embedding: {exc}") from exc

    # ... FAISS search unchanged ...

    # Phase 2: Reranking with full context
    if self.reranker_client and len(entity_mappings) > 1:
        entity_mappings = self._rerank_candidates(
            session, full_context, entity_mappings[:self.topk_rerank]  # CHANGED
        )

    # ... rest unchanged ...
```

**Key changes:**
- Use `search_phrase` for embedding (was `query`)
- Pass `full_context` to reranker (was `query`)
- Keep `_filter_by_category` untouched; parsing happens before entity filtering.
- Add light input sanitation: strip both strings; if either is empty after parsing, fall back to the raw `query`.

#### 1.4 Update generator.md

Replace Section 3 with concise two-phase query guidance:

```markdown
### 3. Craft two-phase queries

Search uses two phases:
1. **SEARCH phrase** (fast vector search) → casts wide net with keywords
2. **TASK context** (smart reranking) → picks most relevant for your goal

**Query format:**
```
[SEARCH] authentication implementation patterns
[TASK] Implement secure OAuth2 login with refresh tokens
```

**Basic principle:**
- SEARCH: Combine [process] + [domain] (3-6 words)
  - Examples: "migration planning", "performance troubleshooting", "feature rollout", "API design"
  - Broader beats narrow; patterns beat technologies
- TASK: Your goal + key constraints (one sentence)
  - Helps ranker identify what would be useful

**Issue 2-3 variants** with different SEARCH phrases to explore the semantic space.

**Examples:**

| User Request | Query |
|---|---|
| Implement OAuth2 login | `[SEARCH] authentication implementation patterns`<br>`[TASK] Implement secure OAuth2 login with refresh tokens` |
| Fix slow database queries | `[SEARCH] query performance troubleshooting`<br>`[TASK] Optimize slow Postgres queries in production API` |
| Add feature flags | `[SEARCH] gradual feature rollout`<br>`[TASK] Deploy new checkout flow with progressive rollout` |

If top score <0.50, reformulate the SEARCH phrase.
```

**Also update Section 4** (line 41 in generator.md):
Change the example call from:
```markdown
Call `read_entries(entity_type="experience", category_code=..., query=...)` for each variant.
```
To:
```markdown
Call `read_entries(entity_type="experience", category_code=..., query="[SEARCH] ... [TASK] ...")` for each variant.
```

### Phase 2: Instruction-Aware Reranking (Future Enhancement)

**⚠️ NOTE**: This phase contains proposed enhancements based on Qwen3-Reranker paper analysis. These need empirical validation with real data before implementation.

This phase makes full use of Qwen3-Reranker's instruction-aware capabilities using the proper template structure from the paper.

#### 2.1 Instruction Design Principles (from Qwen3 Paper Analysis)

Based on the paper (https://arxiv.org/html/2506.05176v3), key findings:

1. **Template Structure**: Qwen3 uses explicit separation of Instruct, Query, and Document
2. **Chain-of-Thought**: Model generates `<think>` reasoning before yes/no answer
3. **Training Data**: Model trained on role-based queries (keyword, factual, summary, judgment)
4. **Two-Stage Paradigm**: Designed for reranking top-100 from embedding retrieval
5. **Scoring Formula**: `score = e^P(yes) / [e^P(yes) + e^P(no)]`

**Instruction Design Guidelines:**
- **Specify perspective**: "From a developer's perspective..." or "From a troubleshooting perspective..."
- **Define relevance criteria**: What makes a document useful for the task?
- **Allow for transfer learning**: "...even if terminology or technology differs"
- **Be action-oriented**: Match the task type (implementation, troubleshooting, design)

**Task Type Classification:**
Instructions can be customized based on the task type inferred from the TASK context:
- **Implementation**: Focus on patterns, code structures, architectural decisions
- **Troubleshooting**: Focus on diagnostic approaches, failure modes, resolution strategies
- **Design**: Focus on design decisions, trade-offs, architectural patterns
- **General**: Broader applicability criteria

**Note**: Whether to implement task-type detection or use LLM-generated instructions per query should be validated with real data.

#### 2.2 Modify RerankerClient API

In `src/api/gpu/reranker_client.py`, change from embedding mode to generation mode using the exact template from the paper:

```python
def rerank_with_instruction(
    self,
    query: str,
    documents: List[str],
    instruction: Optional[str] = None,
    task_type: str = "general"
) -> List[float]:
    """
    Rerank documents using instruction-aware scoring.

    Uses Qwen3-Reranker's pointwise binary classification approach
    with proper template structure from the paper.

    Args:
        query: The user's task description
        documents: List of experience/manual texts to rank
        instruction: Custom instruction (if None, uses task_type default)
        task_type: "implementation", "troubleshooting", "design", or "general"
    """
    if instruction is None:
        instruction = self._get_instruction_for_task_type(task_type)

    scores = []
    for doc in documents:
        # Template from Qwen3-Reranker paper (exact format)
        prompt = f"""<|im_start|>system
Judge whether the Document meets the requirements based on the Query and the Instruct provided. Note that the answer can only be "yes" or "no".<|im_end|>
<|im_start|>user
<Instruct>: {instruction}
<Query>: {query}
<Document>: {doc}<|im_end|>
<|im_start|>assistant
<think>
"""
        # Model generates reasoning in <think> block, then yes/no token
        # Score formula: e^P(yes) / [e^P(yes) + e^P(no)]

        output = self.model(prompt, logprobs=True, max_tokens=50, temperature=0)

        # Extract P(yes) and P(no) from logprobs
        # Implementation details:
        # 1. Parse model output to find "yes" or "no" token
        # 2. Get log probability from logprobs data
        # 3. Convert to probability: prob = exp(logprob)
        # 4. Normalize: score = prob_yes / (prob_yes + prob_no)

        yes_prob = ...  # TODO: Extract from output['choices'][0]['logprobs']
        no_prob = ...   # TODO: Extract from output['choices'][0]['logprobs']

        score = yes_prob / (yes_prob + no_prob) if (yes_prob + no_prob) > 0 else 0.0
        scores.append(score)

    return scores

def _get_instruction_for_task_type(self, task_type: str) -> str:
    """
    Get task-specific instruction.

    NOTE: These are initial proposals based on paper analysis.
    Should be validated and potentially replaced with LLM-generated
    instructions based on generator.md guidance.
    """
    INSTRUCTIONS = {
        "implementation": (
            "From a developer's perspective implementing a feature, determine if this "
            "experience demonstrates applicable patterns, code structures, or architectural "
            "decisions that would help accomplish the task, even if specific technologies differ"
        ),
        "troubleshooting": (
            "From a troubleshooting perspective, determine if this experience provides "
            "diagnostic approaches, common failure modes, or resolution strategies applicable "
            "to the described problem"
        ),
        "design": (
            "From a system designer's perspective, determine if this experience illustrates "
            "design decisions, trade-offs, or architectural patterns relevant to the design challenge"
        ),
        "general": (
            "Determine if this experience or manual provides practical knowledge, guidance, "
            "or context that would help accomplish the given task, accounting for transferable "
            "patterns even if terminology differs"
        ),
    }
    return INSTRUCTIONS.get(task_type, INSTRUCTIONS["general"])
```

**Alternative Approach - LLM-Generated Instructions:**

Instead of hardcoded instructions per task type, leverage the Generator LLM's understanding:

```python
# In generator.md, teach the LLM to optionally provide instruction:
# [SEARCH] schema migration patterns
# [TASK] Migrate PostgreSQL schema with zero downtime
# [INSTRUCT] From an infrastructure engineer's perspective, determine if this provides applicable migration strategies

# Parser extracts: (search, task, instruction)
# If instruction not provided, use default based on task_type inference
```

**⚠️ Validation Required**: Test whether hardcoded vs LLM-generated instructions perform better with real queries.

**Challenges:**
- Validate llama-cpp-python + GGUF support for logprobs; may need different model format
- Map token strings "yes"/"no" to token IDs via tokenizer
- Handle cases where model doesn't output clean yes/no
- Performance: generation is ~5-10x slower than embeddings (~500ms-2s for 40 docs)
- Fall back to embedding cosine if logprobs unavailable

#### 2.3 Update _rerank_candidates

```python
def _rerank_candidates(self, session, query, candidates, instruction=None, task_type="general"):
    """Rerank candidates using instruction-aware scoring."""
    texts = []
    for candidate in candidates:
        entity = self._fetch_entity(session, candidate["entity_id"], candidate["entity_type"])
        if entity:
            if candidate["entity_type"] == "experience":
                text = f"{entity.title}\n\n{entity.playbook}"
            else:
                text = entity.content or entity.title
            texts.append(text)
        else:
            texts.append("")

    # Use instruction-aware reranking
    reranked_scores = self.reranker_client.rerank_with_instruction(
        query, texts, instruction=instruction, task_type=task_type
    )

    # ... rest unchanged ...
```

**Note on Instruction Source:**

The instruction can come from:
1. **Hardcoded by task type** (initial implementation)
2. **LLM-generated per query** (preferred long-term approach)

For LLM-generated instructions, the Generator LLM (following generator.md) would craft context-specific instructions based on:
- The user's task description
- The category/domain being searched
- The type of guidance needed (implementation, troubleshooting, design)

This approach leverages the Generator LLM's intelligence rather than hardcoding instruction templates. The Generator LLM understands the nuanced requirements better than predefined rules.

**Example query format with LLM-generated instruction:**
```
[SEARCH] schema migration patterns
[TASK] Migrate PostgreSQL schema with zero downtime for production system
[INSTRUCT] From an infrastructure engineer's perspective, determine if this experience provides migration strategies that minimize downtime and ensure data consistency
```

This should be validated against simpler approaches (hardcoded instructions) with real data.

#### 2.4 Performance Considerations

- **Before**: Embedding-based reranking on 40 candidates is fast (~50-100ms)
- **After**: Generation-based reranking on 40 candidates may take ~500ms-2s
- **Mitigation**:
  - Only rerank top-K (configurable, default 20 instead of 40)
  - Batch if possible
  - Consider async/parallel processing
  - Short-circuit to embedding rerank when logprobs are unavailable or latency budget exceeded

#### 2.5 Configuration

Add config options in `src/common/config/config.py` and surface in runtime wiring:
```python
self.reranker_mode = os.getenv("CHL_RERANKER_MODE", "embedding")  # "embedding" or "instruction"
self.reranker_instruction = os.getenv("CHL_RERANKER_INSTRUCTION", None)  # Custom instruction (optional)
self.reranker_task_type_detection = os.getenv("CHL_RERANKER_TASK_TYPE_DETECTION", "false")  # Enable task type inference
```
Propagate through `runtime.py` so `VectorFAISSProvider` can branch without code edits; log the active mode at startup.

**Note**: These config options support experimentation with different instruction strategies (hardcoded vs LLM-generated vs task-type-based) without code changes.

### Phase 3: Advanced Features (Future)

#### 3.1 Dynamic Instructions by Entity Type

Different instructions for experiences vs manuals:

```python
RERANKER_INSTRUCTIONS = {
    "experience": (
        "Determine if this experience demonstrates patterns, strategies, or lessons "
        "applicable to the given task, even if the specific technology differs"
    ),
    "manual": (
        "Determine if this manual provides conceptual background, terminology, or "
        "process guidance necessary to understand or complete the given task"
    ),
}
```

#### 3.2 Query Expansion

For Phase 1 FAISS search, expand the search phrase with synonyms:

```python
# If search phrase is "authentication implementation"
# Expand to include: "auth setup", "login system design", "user authentication"
# Generate multiple embeddings and aggregate results
```

#### 3.3 Hybrid Ranking
Combine multiple signals:
- FAISS similarity score
- Reranker score
- Recency (updated_at)
- User feedback (if collected)

Weighted combination:
```python
final_score = 0.6 * reranker_score + 0.3 * faiss_score + 0.1 * recency_score
```

#### 3.4 Duplicate Rerank Mode
- Decide whether duplicate detection should also use instruction-aware scoring; if enabled, reuse the same mode/config but keep the option to force embedding rerank for latency.

## Testing Strategy

### Unit Tests

1. **Query Parsing**
   - Test `[SEARCH]/[TASK]` format parsing
   - Test pipe delimiter format
   - Test fallback behavior
   - Test edge cases (empty strings, missing sections)
   - Assert empty parse parts fall back to original query

2. **Search Provider**
   - Mock embedding client and verify `search_phrase` is passed to FAISS
   - Mock reranker and verify `full_context` is used
   - Test backward compatibility with old query format
   - Verify category filtering still works after rerank ordering

### Integration Tests

1. **End-to-End Search**
   - Insert test experiences with known patterns
   - Query with two-phase format
   - Verify correct results are returned and ranked appropriately

2. **Generator Workflow**
   - Test LLM following new generator.md guidance
   - Verify queries are formatted correctly
   - Measure search quality improvements
   - Ensure warning/reformulation guidance triggers when top score <0.50

### Performance Tests

1. **Latency Measurement**
   - Baseline: Current embedding-based approach
   - Phase 1 implementation: Two-phase parsing with embedding reranking
   - Phase 2 implementation: Instruction-aware reranking
   - Target: <500ms p95 for Phase 1, <2s p95 for Phase 2

2. **Quality Metrics**
   - Create evaluation dataset with known relevant/irrelevant pairs
   - Measure nDCG@10, MRR, Precision@K
   - Compare old vs new approach
   - Track adoption: % of queries using two-phase format

3. **Phase 2 Validation (Instruction-Aware Reranking)**
   - **Critical**: All Phase 2 enhancements (instruction templates, task-type detection, LLM-generated instructions) must be validated with real data before implementation
   - Test hardcoded instructions vs LLM-generated instructions
   - Measure impact of `<think>` reasoning on result quality
   - Compare embedding reranking vs instruction-aware reranking performance
   - Validate that proper Qwen3 template structure improves results over concatenation approach
   - Test different instruction phrasings and perspectives
   - **Success criteria**: Instruction-aware approach shows >10% improvement in nDCG@10 over embedding-based Phase 1

## Decisions Made

1. **Query format**: `[SEARCH]/[TASK]` required (strict, no fallback)
   - ✓ Keeps guidance simple and clear
   - ✓ Easier to parse with minimal edge cases
   - ✓ Helpful error messages guide LLM to correct format
   - ✓ No backward compatibility - fail fast with clear errors

2. **Reranking input format**: `"{task}\n\nRelevant concepts: {search}"`
   - ✓ Includes both task context and search concepts
   - ✓ Can be refined based on Phase 2 experiments

3. **Implementation sequence**: Phase 1 first, Phase 2 later
   - ✓ Phase 1: Query parsing with embedding-based reranking (validate architecture)
   - ✓ Phase 2: Instruction-aware reranking (after seeing real usage patterns)

4. **Error handling**: Strict validation with helpful messages
   - ✓ Invalid queries raise ValueError immediately
   - ✓ Error messages include format examples and user's query
   - ✓ Guides LLM to correct format without debugging
   - ✓ No silent fallbacks - fail fast and clearly

5. **Token limits**: Trust LLM, validate later if needed
   - ✓ generator.md instructs "one sentence" for TASK context
   - ✓ SOTA LLMs follow guidance well
   - ✓ Add truncation helper in Phase 2 if necessary

6. **generator.md style**: Concise, principle-based approach
   - ✓ Basic principle + easy examples
   - ✓ Trust SOTA LLM to extrapolate
   - ✓ ~20 lines, focused on essentials

## Open Questions (Remaining)

1. **Parser location**: `src/api/gpu/search_provider.py` vs `src/api/gpu/query_utils.py`?
   - Trade-off: Simplicity (single file) vs organization (separate module)
   - Recommendation: Start in search_provider.py, refactor later if needed

2. **Logging**: Should we log when fallback is used?
   - Could help monitor adoption rate
   - Recommendation: Add optional debug-level logging in Phase 1

3. **Duplicate search**: Should `find_duplicates` also use two-phase parsing?
   - Currently uses combined title+content as query
   - Recommendation: Keep as-is for Phase 1; evaluate in Phase 2

## Success Metrics

### Immediate (Phase 1)
- ✓ Two-phase query parsing implemented
- ✓ generator.md updated with clear guidance
- ✓ Backward compatibility maintained
- ✓ Tests passing
- 📊 Latency impact: <10% increase vs baseline

### Short-term (Phase 1 in production)
- 📊 LLM adoption: >80% of queries use new format within 2 weeks
- 📊 Search quality: Subjective improvement in result relevance (user feedback/testing)
- 📊 Error rate: <1% parsing failures

### Medium-term (Phase 2)
- ✓ Instruction-aware reranking implemented
- 📊 Search quality: +15% improvement in nDCG@10 vs Phase 1
- 📊 Latency: <2s p95 for search with reranking
- 📊 Query success rate: >0.50 average score for top result

## Timeline Estimate

**Phase 1: Two-Phase Query Parsing**
- Query parser implementation: 2 hours
- VectorFAISSProvider modifications: 2 hours
- generator.md updates: 1 hour
- Unit tests: 2 hours
- Integration testing: 2 hours
- **Total: ~1 day**

**Phase 2: Instruction-Aware Reranking**
- **Prerequisites**: Phase 1 deployed and validated with real usage
- Research llama-cpp-python API: 2-4 hours
- RerankerClient modifications: 4-6 hours
- Instruction template experimentation: 4-6 hours
- Testing and debugging: 4 hours
- Performance optimization: 2-4 hours
- Validation studies (hardcoded vs LLM-generated): 4-8 hours
- **Total: ~3-4 days** (not including validation time)

**Phase 3: Advanced Features**
- Dynamic instructions: 2 hours
- Query expansion: 4-6 hours
- Hybrid ranking: 4-6 hours
- **Total: ~1-2 days** (if pursued)

## Summary: What's Validated, What Needs Validation

### ✅ Validated with Real Data

**Hypothesis: Two-phase query architecture improves relevance**
- **Status**: ✅ CONFIRMED
- **Evidence**: Tested with actual database (14 experiences, 10 manuals)
- **Results**:
  - Task context changed ranking meaningfully in all test cases
  - Logical relevance captured even with embedding-based reranking
  - Examples:
    - "Multi-tenant app" → boosted experiences mentioning `partner_company_id`
    - "Unclear requirements" → brought "Database Schema Cannot Be TBD" to top
    - "Different roles see different data" → prioritized access control guidance

**Conclusion**: Phase 1 implementation is sound and ready to proceed.

### ⚠️ Proposed but NOT Validated

The following Phase 2 enhancements are based on Qwen3 paper analysis but **require empirical validation** before implementation:

1. **Proper Qwen3 template structure** (`<think>` reasoning, explicit Instruct/Query/Document separation)
   - Hypothesis: Will improve over simple concatenation
   - Needs: A/B testing with real queries

2. **Task-type-specific instructions** (implementation vs troubleshooting vs design)
   - Hypothesis: Tailored instructions improve relevance
   - Needs: Compare against single generic instruction

3. **LLM-generated instructions** (Generator crafts custom instruction per query)
   - Hypothesis: Context-aware instructions outperform hardcoded templates
   - Needs: Compare hardcoded vs LLM-generated with evaluation dataset

4. **Instruction perspective** ("From a developer's perspective...")
   - Hypothesis: Role-based framing improves relevance judgment
   - Needs: Test different perspective phrasings

5. **Chain-of-thought reasoning** (model generates `<think>` before yes/no)
   - Hypothesis: Improves logical relevance detection
   - Needs: Compare with/without reasoning step

**Recommendation**: Implement Phase 1 first, collect real usage data, then validate Phase 2 hypotheses systematically before coding.

## References

- **Qwen3-Reranker Paper**: https://arxiv.org/html/2506.05176v3
- **Current Implementation**:
  - `src/api/gpu/reranker_client.py` - RerankerClient class
  - `src/api/gpu/search_provider.py` - VectorFAISSProvider.search and _rerank_candidates
  - `generator.md` - Section 3: Craft high-signal queries
- **Related Config**: `src/common/config/config.py` - reranker_repo, reranker_quant, topk_rerank
