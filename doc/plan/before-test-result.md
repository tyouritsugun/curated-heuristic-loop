# Before: reranker (cosine embedding) behavior

Captured from MCP `/entries/read` (provider=`vector_faiss`) before changing the reranker implementation. Default `topk_rerank=40`, reranker uses cosine embeddings (not yes/no logits).

## Query: "handoff checklist" (limit 5)
- 0: EXP-PGS-20251102-054036591751 "Numbered Headings for Stable Links" score 0.6494719
- 1: EXP-PGS-20251102-054036591751 "Numbered Headings for Stable Links" score 0.6494719 (duplicate)
- 2: EXP-PGS-20251101-111753085067 "Always Ask for Figma Link Before Starting" score 0.6267666
- 3: EXP-PGS-20251101-111932682268 "Nested Numbering for Main and Detail Images" score 0.6173438
- 4: EXP-PGS-20251101-111932682268 "Nested Numbering for Main and Detail Images" score 0.6173438 (duplicate)

## Query: "copy checklist before handoff" (limit 5)
- 0: EXP-PGS-20251102-054036591751 "Numbered Headings for Stable Links" score 0.7562444
- 1: EXP-PGS-20251102-054036591751 "Numbered Headings for Stable Links" score 0.7562444 (duplicate)
- 2: EXP-PGS-20251101-111753085067 "Always Ask for Figma Link Before Starting" score 0.7439434
- 3: EXP-PGS-20251101-111730660501 "Replacing SQL with Natural Language in Query Documentation" score 0.7166162
- 4: EXP-PGS-20251101-111932682268 "Nested Numbering for Main and Detail Images" score 0.7143351

## Query: "table columns numbered main view detail view images" (limit 10)
- 0: EXP-PGS-20251102-054036591751 "Numbered Headings for Stable Links" score 0.8244393
- 1: EXP-PGS-20251102-054036591751 "Numbered Headings for Stable Links" score 0.8244393 (duplicate)
- 2: EXP-PGS-20251101-111932682268 "Nested Numbering for Main and Detail Images" score 0.7986370
- 3: EXP-PGS-20251101-111932682268 "Nested Numbering for Main and Detail Images" score 0.7986370 (duplicate)
- 4: EXP-PGS-20251101-111730660501 "Replacing SQL with Natural Language in Query Documentation" score 0.7940882
- 5: EXP-PGS-20251101-111753085067 "Always Ask for Figma Link Before Starting" score 0.7923920
- 6: EXP-PGS-20251101-111907381986 "Use Comparison Tables for Similar Pages" score 0.7747520
- 7: EXP-PGS-20251101-111907381986 "Use Comparison Tables for Similar Pages" score 0.7747520 (duplicate)
- 8: EXP-PGS-20251101-111842385970 "Document Access Control Early in Overview" score 0.7710855
- 9: EXP-PGS-20251101-112001656881 "Database Schema Cannot Be To Be Implemented" score 0.7656739

## Query: "what are necessary when create a page specification?" (limit 5)
- 0: EXP-PGS-20251102-054036591751 "Numbered Headings for Stable Links" score 0.8590573
- 1: EXP-PGS-20251102-054036591751 "Numbered Headings for Stable Links" score 0.8590573 (duplicate)
- 2: EXP-PGS-20251101-111730660501 "Replacing SQL with Natural Language in Query Documentation" score 0.8550133
- 3: EXP-PGS-20251101-111753085067 "Always Ask for Figma Link Before Starting" score 0.8543436
- 4: EXP-PGS-20251101-111842385970 "Document Access Control Early in Overview" score 0.8513438

# After: reranker with yes/no logprobs (paper-style)

Captured after restarting the API server with the updated reranker.

## Query: "handoff checklist" (limit 5)
- 0: EXP-PGS-20251101-111842385970 "Document Access Control Early in Overview" score 0.0014450
- 1: EXP-PGS-20251102-054036591751 "Numbered Headings for Stable Links" score 0.0008140
- 2: EXP-PGS-20251101-111817806263 "Confirm Database Logic Before Writing Specifications" score 0.0006238
- 3: EXP-PGS-20251101-111753085067 "Always Ask for Figma Link Before Starting" score 0.0005174
- 4: EXP-PGS-20251101-112001656881 "Database Schema Cannot Be To Be Implemented" score 0.0003323

## Query: "copy checklist before handoff" (limit 5)
- 0: EXP-PGS-20251101-111753085067 "Always Ask for Figma Link Before Starting" score 0.0045658
- 1: EXP-PGS-20251101-111817806263 "Confirm Database Logic Before Writing Specifications" score 0.0044739
- 2: EXP-PGS-20251101-111842385970 "Document Access Control Early in Overview" score 0.0030083
- 3: EXP-PGS-20251102-054036591751 "Numbered Headings for Stable Links" score 0.0026537
- 4: EXP-PGS-20251101-111907381986 "Use Comparison Tables for Similar Pages" score 0.0017349

## Query: "table columns numbered main view detail view images" (limit 10)
- 0: EXP-PGS-20251101-111932682268 "Nested Numbering for Main and Detail Images" score 0.9994824
- 1: EXP-PGS-20251102-054036591751 "Numbered Headings for Stable Links" score 0.0092592
- 2: EXP-PGS-20251101-112001656881 "Database Schema Cannot Be To Be Implemented" score 0.0044309
- 3: EXP-PGS-20251101-111907381986 "Use Comparison Tables for Similar Pages" score 0.0035680
- 4: EXP-PGS-20251101-111817806263 "Confirm Database Logic Before Writing Specifications" score 0.0025731
- 5: EXP-PGS-20251101-111842385970 "Document Access Control Early in Overview" score 0.0002597
(*6 unique hits returned after dedup.*)

## Query: "what are necessary when create a page specification?" (limit 5)
- 0: EXP-PGS-20251101-111817806263 "Confirm Database Logic Before Writing Specifications" score 0.9693690
- 1: EXP-PGS-20251101-112001656881 "Database Schema Cannot Be To Be Implemented" score 0.8126781
- 2: EXP-PGS-20251101-111753085067 "Always Ask for Figma Link Before Starting" score 0.7743348
- 3: EXP-PGS-20251101-111842385970 "Document Access Control Early in Overview" score 0.5072684
- 4: EXP-PGS-20251101-111907381986 "Use Comparison Tables for Similar Pages" score 0.0190027

# Notes for CUDA/GPU developers (testing & debug)

- Backends now HF-first on Apple: default embedding Qwen/Qwen3-Embedding-0.6B (HF) and reranker Qwen/Qwen3-Reranker-0.6B (HF) on mps. GGUF embeddings remain optional if you install llama-cpp-python and pick a -GGUF repo.
- The 4B HF reranker is slow on mps; use 0.6B for responsiveness. For CUDA, the 4B may be fine; set CHL_TOPK_RERANK high only if latency allows.
- Environment hints:
  - Set `CHL_TOPK_RERANK` to control rerank fanout (e.g., 10–40).
  - For OpenMP clashes on mac: `KMP_DUPLICATE_LIB_OK=TRUE` and optionally `OMP_NUM_THREADS=1`.
- HF reranker install (Apple Metal example):
  - Use `requirements_apple.txt` (torch + transformers stack; llama-cpp optional).
  - Run `python scripts/check_api_env.py` then `python scripts/setup-gpu.py --download-models` to cache `Qwen/Qwen3-Reranker-0.6B` (HF) and the chosen embedding GGUF.
- Runtime logging: HF reranker load line shows device and yes/no token ids; confirms model is on CUDA/mps.
