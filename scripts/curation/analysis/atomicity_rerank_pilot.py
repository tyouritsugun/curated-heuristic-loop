#!/usr/bin/env python3
"""Pilot: reranker-based atomicity scoring for experiences."""
from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import sqlite3

# Add project root to sys.path
REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from scripts._config_loader import load_scripts_config  # noqa: E402
from src.api.gpu.reranker_client import RerankerClient  # noqa: E402
from src.common.config.config import get_config  # noqa: E402

logger = logging.getLogger(__name__)

DEFAULT_INSTRUCTION = (
    "Decide if the Document should be split. Answer only 'yes' or 'no'."
)
DEFAULT_SEARCH = ""
DEFAULT_TASK = (
    "Answer yes if the document is non-atomic (bundles multiple tactics, steps, or distinct goals) "
    "and should be split into separate experiences. Answer no if it is atomic."
)
DEFAULT_EXAMPLES = (
    "Examples (should split = yes):\n"
    "- \"Fix timeout by increasing pool AND enabling caching AND adding monitoring\"\n"
    "- \"Deploy to production, monitor error rates, then tune configuration\"\n"
    "- \"Handle database errors: retry logic for timeouts, pooling for scale, logging for debugging\"\n"
    "Examples (atomic = no):\n"
    "- \"Fix Django timeout by increasing connection pool to 100\"\n"
    "- \"Resolve npm install failure by clearing cache\"\n"
    "- \"Debug race condition using thread-safe logger\"\n"
)


def parse_args() -> argparse.Namespace:
    try:
        cfg, _ = load_scripts_config()
        cur = cfg.get("curation", {})
        default_db = cur.get("curation_db_path", "data/curation/chl_curation.db")
    except Exception:
        default_db = "data/curation/chl_curation.db"

    parser = argparse.ArgumentParser(description="Atomicity reranker pilot")
    parser.add_argument("--db-path", default=default_db, help="Path to curation SQLite DB")
    parser.add_argument("--output", default="data/curation/atomicity_scores.jsonl", help="Output JSONL")
    parser.add_argument("--limit", type=int, default=0, help="Limit rows (0 = all)")
    parser.add_argument("--min-score", type=float, default=0.0, help="Filter: keep scores >= min")
    parser.add_argument("--max-score", type=float, default=1.0, help="Filter: keep scores <= max")
    parser.add_argument("--threshold", type=float, default=None, help="Flag non-atomic if score < threshold")
    parser.add_argument("--labels", default=None, help="CSV with columns: id,label (label=1 for non-atomic)")
    parser.add_argument("--precision-target", type=float, default=0.85, help="Target precision for non-atomic")
    parser.add_argument("--instruction", default=DEFAULT_INSTRUCTION, help="Reranker instruction")
    parser.add_argument("--task", default=DEFAULT_TASK, help="Task text (definition)")
    parser.add_argument("--search", default=DEFAULT_SEARCH, help="Search text (optional)")
    parser.add_argument("--examples-file", default=None, help="Optional text file with examples to append")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


def load_experiences(db_path: str, limit: int) -> Iterable[Tuple[str, str, str, str, Optional[str]]]:
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        sql = (
            "SELECT id, category_code, title, playbook, context "
            "FROM experiences ORDER BY created_at"
        )
        if limit and limit > 0:
            sql += f" LIMIT {int(limit)}"
        for row in cur.execute(sql):
            yield row
    finally:
        conn.close()


def build_document(title: str, playbook: str, context: Optional[str]) -> str:
    parts: List[str] = []
    if title:
        parts.append(f"Title: {title}")
    if playbook:
        parts.append(f"Playbook: {playbook}")
    if context:
        parts.append(f"Context: {context}")
    return "\n".join(parts)


def load_labels(path: str) -> Dict[str, int]:
    labels: Dict[str, int] = {}
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            if not row:
                continue
            exp_id = (row.get("id") or "").strip()
            label = row.get("label")
            if not exp_id or label is None:
                continue
            try:
                labels[exp_id] = 1 if str(label).strip().lower() in {"1", "true", "yes"} else 0
            except Exception:
                continue
    return labels


def pick_threshold(scores: Dict[str, float], labels: Dict[str, int], target_precision: float) -> Optional[Dict[str, float]]:
    # Classify non-atomic if score < threshold
    candidates = sorted(set(scores.values()))
    best = None
    for thresh in candidates:
        tp = fp = fn = 0
        for exp_id, label in labels.items():
            score = scores.get(exp_id)
            if score is None:
                continue
            pred_non_atomic = score < thresh
            if pred_non_atomic and label == 1:
                tp += 1
            elif pred_non_atomic and label == 0:
                fp += 1
            elif not pred_non_atomic and label == 1:
                fn += 1
        if tp + fp == 0:
            continue
        precision = tp / (tp + fp)
        recall = 0.0 if tp + fn == 0 else tp / (tp + fn)
        if precision >= target_precision:
            if best is None or recall > best["recall"]:
                best = {"threshold": thresh, "precision": precision, "recall": recall}
    return best


def main() -> int:
    args = parse_args()
    if args.verbose:
        logging.basicConfig(level=logging.INFO)
    else:
        logging.basicConfig(level=logging.WARNING)

    cfg = get_config()
    reranker = RerankerClient(
        model_repo=cfg.reranker_repo,
        quantization=cfg.reranker_quant,
        n_gpu_layers=getattr(cfg, "reranker_n_gpu_layers", 0),
        rerank_instruction=args.instruction,
    )

    task_text = args.task
    if args.examples_file:
        examples_path = Path(args.examples_file)
        if examples_path.exists():
            extra = examples_path.read_text(encoding="utf-8").strip()
            if extra:
                task_text = f"{task_text}\n\n{extra}"
    else:
        task_text = f"{task_text}\n\n{DEFAULT_EXAMPLES}"

    query = {"search": args.search, "task": task_text}

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    scores: Dict[str, float] = {}
    start = time.time()
    total = 0
    kept = 0

    with output_path.open("w", encoding="utf-8") as fh:
        for exp_id, category, title, playbook, context in load_experiences(args.db_path, args.limit):
            doc = build_document(title, playbook, context)
            if not doc.strip():
                continue
            score = reranker.rerank(query=query, documents=[doc])[0]
            total += 1
            scores[exp_id] = score
            if score < args.min_score or score > args.max_score:
                continue
            kept += 1
            rec = {
                "id": exp_id,
                "category": category,
                "title": title,
                "score": score,
            }
            if args.threshold is not None:
                rec["flag_non_atomic"] = score < args.threshold
            fh.write(json.dumps(rec) + "\n")

    elapsed = time.time() - start
    print(f"Scored {total} experiences in {elapsed:.1f}s; wrote {kept} records to {output_path}")

    if args.labels:
        labels = load_labels(args.labels)
        if labels:
            best = pick_threshold(scores, labels, args.precision_target)
            if best:
                print(
                    "Recommended threshold (score < threshold => non-atomic): "
                    f"{best['threshold']:.4f} | precision={best['precision']:.2%} recall={best['recall']:.2%}"
                )
            else:
                print("No threshold met the target precision.")
        else:
            print("Labels file loaded, but no usable labels found.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
