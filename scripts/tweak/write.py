#!/usr/bin/env python3
"""CLI wrapper around write_entry for local experimentation.

Supports single-shot and batch writes using YAML or JSON payloads.
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
from src.mcp.handlers_entries import make_write_entry_handler  # noqa: E402

log = logging.getLogger("tweak.write")


def _init_vector_provider(session, config, disable_reranker: bool) -> Optional[SearchProvider]:
    try:
        from src.embedding.client import EmbeddingClient
        from src.embedding.reranker import RerankerClient, RerankerClientError
        from src.search.faiss_index import FAISSIndexManager
        from src.search.vector_provider import VectorFAISSProvider
    except ImportError as exc:
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


def _load_yaml_or_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        if path.suffix.lower() in {".json", ".jsonl", ".ndjson"}:
            text = fh.read()
            if path.suffix.lower() in {".jsonl", ".ndjson"}:
                return [json.loads(line) for line in text.splitlines() if line.strip()]
            return json.loads(text)
        return yaml.safe_load(fh)


def _load_payload(
    *,
    data_file: Optional[Path],
    data_yaml: Optional[str],
    fields: Optional[List[str]],
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {}

    if data_file:
        loaded = _load_yaml_or_json(data_file)
        if loaded is None:
            raise ValueError(f"Payload file {data_file} is empty")
        if not isinstance(loaded, dict):
            raise ValueError(f"Payload file {data_file} must contain a mapping/object")
        payload.update(loaded)

    if data_yaml:
        loaded = yaml.safe_load(data_yaml)
        if loaded is None:
            raise ValueError("Provided YAML payload is empty")
        if not isinstance(loaded, dict):
            raise ValueError("YAML payload must be a mapping/object")
        payload.update(loaded)

    for field in fields or []:
        if "=" not in field:
            raise ValueError(f"Invalid --field '{field}'. Use key=value format.")
        key, value = field.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError(f"Invalid --field '{field}'. Key cannot be empty.")
        try:
            payload[key] = yaml.safe_load(value)
        except yaml.YAMLError:
            payload[key] = value

    if not payload:
        raise ValueError("No data provided. Use --data-file, --data-yaml or --field.")

    return payload


def _validate_inputs(entity_type: str, category_code: str) -> None:
    if entity_type not in {"experience", "manual"}:
        raise ValueError("entity_type must be 'experience' or 'manual'")
    if not category_code:
        raise ValueError("category_code is required")


def _parse_batch_file(path: Path) -> List[Dict[str, Any]]:
    loaded = _load_yaml_or_json(path)
    if isinstance(loaded, list):
        return loaded
    if isinstance(loaded, dict):
        return [loaded]
    raise ValueError("Batch file must contain a list of requests")


def _run_single(handler, *, entity_type: str, category_code: str, data: Dict[str, Any]) -> Dict[str, Any]:
    return handler(
        entity_type=entity_type,
        category_code=category_code,
        data=data,
    )


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Invoke write_entry locally.")
    parser.add_argument("--entity-type", required=False, help="experience or manual")
    parser.add_argument("--category-code", required=False, help="Category code (e.g., PGS)")
    parser.add_argument("--data-file", type=Path, help="YAML/JSON file with entry payload")
    parser.add_argument("--data-yaml", help="Inline YAML payload")
    parser.add_argument(
        "--field",
        action="append",
        help="Override/add a field using key=value (value parsed as YAML)",
    )
    parser.add_argument("--batch", type=Path, help="YAML/JSON file with multiple requests")
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

    handler = make_write_entry_handler(db, config, search_service)

    try:
        if args.batch:
            batch_requests = _parse_batch_file(args.batch)
            if not batch_requests:
                raise ValueError(f"No requests found in batch file {args.batch}")
            outputs = []
            for idx, req in enumerate(batch_requests, start=1):
                try:
                    entity_type = req.get("entity_type")
                    category_code = req.get("category_code")
                    data = req.get("data")
                    if data is None:
                        raise ValueError("Missing 'data' payload")
                    _validate_inputs(entity_type, category_code)
                    result = _run_single(
                        handler,
                        entity_type=entity_type,
                        category_code=category_code,
                        data=data,
                    )
                    outputs.append({"request_index": idx, "request": req, "response": result})
                except Exception as exc:
                    outputs.append(
                        {
                            "request_index": idx,
                            "request": req,
                            "error": str(exc),
                        }
                    )
            print(_render_output(outputs, args.format))
            return 0

        _validate_inputs(args.entity_type, args.category_code)
        payload = _load_payload(
            data_file=args.data_file,
            data_yaml=args.data_yaml,
            fields=args.field,
        )
        result = _run_single(
            handler,
            entity_type=args.entity_type,
            category_code=args.category_code,
            data=payload,
        )
        print(_render_output(result, args.format))
        return 0
    except Exception as exc:
        log.error("write_entry failed: %s", exc)
        return 1
    finally:
        try:
            session.close()
        except Exception:
            pass
        db.close()


if __name__ == "__main__":
    sys.exit(main())
