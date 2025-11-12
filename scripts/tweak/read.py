#!/usr/bin/env python3
"""CLI wrapper around read_entries for reranking experiments.

Usage examples:
    python scripts/tweak/read.py \
        --entity-type experience \
        --category-code PGS \
        --query "Role: spec author. Task: document access control. Need: heuristics to include."

    python scripts/tweak/read.py --batch cases.yaml

The script initialises the same search pipeline used by the MCP server,
including the FAISS-backed vector provider and optional reranker. You can
toggle fallbacks with the flags below.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import yaml

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.config import get_config  # noqa: E402
from src.storage.database import Database  # noqa: E402
from src.search.service import SearchService  # noqa: E402
from src.search.provider import SearchProvider  # noqa: E402
from src.mcp.handlers_entries import make_read_entries_handler  # noqa: E402

log = logging.getLogger("tweak.read")


def _init_vector_provider(session, config, disable_reranker: bool) -> Optional[SearchProvider]:
    """Best-effort construction of the vector provider with reranking."""
    try:
        from src.embedding.client import EmbeddingClient
        from src.embedding.reranker import RerankerClient, RerankerClientError
        from src.search.faiss_index import FAISSIndexManager
        from src.search.vector_provider import VectorFAISSProvider
    except ImportError as exc:  # ML extras not installed
        log.warning("Vector search unavailable: %s", exc)
        return None

    try:
        embedding_client = EmbeddingClient(
            model_repo=config.embedding_repo,
            quantization=config.embedding_quant,
            n_gpu_layers=0,
        )
    except FileNotFoundError as exc:
        log.warning(
            "Embedding model missing (%s). Run `python scripts/setup-gpu.py --download-models`.",
            exc,
        )
        return None
    except Exception as exc:
        log.warning("Failed to load embedding model: %s", exc)
        return None

    try:
        faiss_manager = FAISSIndexManager(
            index_dir=config.faiss_index_path,
            model_name=config.embedding_model,
            dimension=embedding_client.embedding_dimension,
            session=session,
        )
    except Exception as exc:
        log.warning("Failed to init FAISS index: %s", exc)
        return None

    reranker_client = None
    if not disable_reranker:
        try:
            reranker_client = RerankerClient(
                model_repo=config.reranker_repo,
                quantization=config.reranker_quant,
                n_gpu_layers=0,
            )
        except FileNotFoundError as exc:
            log.warning(
                "Reranker model missing (%s). Run `python scripts/setup-gpu.py --download-models`.",
                exc,
            )
        except RerankerClientError as exc:
            log.warning("Reranker unavailable: %s", exc)
        except Exception as exc:
            log.warning("Failed to load reranker model: %s", exc)

    try:
        vector_provider = VectorFAISSProvider(
            index_manager=faiss_manager,
            embedding_client=embedding_client,
            model_name=config.embedding_model,
            reranker_client=reranker_client,
            topk_retrieve=config.topk_retrieve,
            topk_rerank=config.topk_rerank,
        )
        if not vector_provider.is_available:
            log.warning("Vector provider initialised but not available (index empty?).")
            return None
        return vector_provider
    except Exception as exc:
        log.warning("Failed to initialise vector provider: %s", exc)
        return None


def _bootstrap_search(disable_vector: bool, disable_reranker: bool):
    """Initialise config, database, and search service."""
    config = get_config()
    db = Database(config.database_path, config.database_echo)
    db.init_database()
    session = db.get_session()

    vector_provider = None
    if not disable_vector:
        vector_provider = _init_vector_provider(session, config, disable_reranker)

    primary = "vector_faiss" if vector_provider else "sqlite_text"

    search_service = SearchService(
        session=session,
        primary_provider=primary,
        fallback_enabled=True,
        vector_provider=vector_provider,
    )

    return config, db, session, search_service


def _render_output(payload: Dict[str, Any], output_format: str) -> str:
    if output_format == "json":
        return json.dumps(payload, indent=2, ensure_ascii=False)
    if output_format == "yaml":
        return yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)
    raise ValueError(f"Unknown format '{output_format}'")


def _parse_batch_file(path: Path) -> List[Dict[str, Any]]:
    with path.open("r", encoding="utf-8") as fh:
        # Support either YAML (single doc or list) or JSON lines
        if path.suffix.lower() in {".jsonl", ".ndjson"}:
            return [json.loads(line) for line in fh if line.strip()]
        text = fh.read()
        data = list(yaml.safe_load_all(text))
        if len(data) == 1 and isinstance(data[0], list):
            return data[0]
        return [doc for doc in data if doc is not None]


def _validate_inputs(entity_type: str, category_code: str, query: Optional[str], ids: List[str]) -> None:
    if entity_type not in {"experience", "manual"}:
        raise ValueError("entity_type must be 'experience' or 'manual'")
    if not category_code:
        raise ValueError("category_code is required")
    if not query and not ids:
        raise ValueError("Provide either --query or at least one --id")


def _run_single(
    handler,
    *,
    entity_type: str,
    category_code: str,
    ids: Optional[List[str]],
    limit: Optional[int],
    query: Optional[str],
) -> Dict[str, Any]:
    result = handler(
        entity_type=entity_type,
        category_code=category_code,
        ids=ids,
        limit=limit,
        query=query,
    )
    return result


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect read_entries responses locally.")
    parser.add_argument("--entity-type", required=False, help="experience or manual")
    parser.add_argument("--category-code", required=False, help="Category code (e.g., PGS)")
    parser.add_argument("--query", help="Semantic query")
    parser.add_argument("--id", dest="ids", action="append", help="Specific entry ID (repeatable)")
    parser.add_argument("--limit", type=int, help="Maximum entries to return")
    parser.add_argument("--batch", type=Path, help="YAML/JSONL file with multiple requests")
    parser.add_argument(
        "--format",
        choices=("yaml", "json"),
        default="yaml",
        help="Output format (default: yaml)",
    )
    parser.add_argument(
        "--disable-vector",
        action="store_true",
        help="Skip vector search initialisation and force sqlite fallback.",
    )
    parser.add_argument(
        "--disable-reranker",
        action="store_true",
        help="Initialise vector search without the reranker.",
    )

    args = parser.parse_args(list(argv) if argv is not None else None)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    config, db, session, search_service = _bootstrap_search(
        disable_vector=args.disable_vector,
        disable_reranker=args.disable_reranker,
    )

    handler = make_read_entries_handler(db, config, search_service)

    try:
        responses: List[Dict[str, Any]] = []

        if args.batch:
            batch_requests = _parse_batch_file(args.batch)
            if not batch_requests:
                raise ValueError(f"No requests found in batch file {args.batch}")
            for idx, req in enumerate(batch_requests, start=1):
                try:
                    _validate_inputs(
                        entity_type=req.get("entity_type"),
                        category_code=req.get("category_code"),
                        query=req.get("query"),
                        ids=req.get("ids") or [],
                    )
                except Exception as exc:
                    responses.append(
                        {
                            "error": str(exc),
                            "request_index": idx,
                            "request": req,
                        }
                    )
                    continue
                result = _run_single(
                    handler,
                    entity_type=req["entity_type"],
                    category_code=req["category_code"],
                    ids=req.get("ids"),
                    limit=req.get("limit"),
                    query=req.get("query"),
                )
                responses.append({"request_index": idx, "request": req, "response": result})
            output = responses
        else:
            _validate_inputs(
                entity_type=args.entity_type,
                category_code=args.category_code,
                query=args.query,
                ids=args.ids or [],
            )
            result = _run_single(
                handler,
                entity_type=args.entity_type,
                category_code=args.category_code,
                ids=args.ids,
                limit=args.limit,
                query=args.query,
            )
            output = result

        print(_render_output(output, args.format))
        return 0
    except Exception as exc:
        log.error("read_entries failed: %s", exc)
        return 1
    finally:
        try:
            session.close()
        except Exception:
            pass
        db.close()


if __name__ == "__main__":
    sys.exit(main())
