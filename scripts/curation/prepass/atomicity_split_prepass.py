#!/usr/bin/env python3
"""LLM-driven atomicity split pre-pass for experiences."""
from __future__ import annotations

import argparse
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional
import sys

import yaml

# Add project root to sys.path
REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

try:
    from tqdm import tqdm  # type: ignore
except Exception:  # pragma: no cover - optional
    tqdm = None

from autogen import AssistantAgent

from scripts.curation.agents.autogen_openai_completion_agent import build_llm_config
from src.common.storage.database import Database
from src.common.storage.repository import generate_experience_id, get_author
from src.common.storage.schema import Experience, ExperienceSplitProvenance, utc_now
from src.common.config.config import get_config

logger = logging.getLogger(__name__)


@dataclass
class SplitItem:
    title: str
    playbook: str
    context: Optional[str] = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="LLM atomicity split pre-pass")
    parser.add_argument(
        "--db-path",
        default="data/curation/chl_curation.db",
        help="Path to curation SQLite DB",
    )
    parser.add_argument(
        "--prompt",
        default="scripts/curation/agents/prompts/atomicity_split.yaml",
        help="YAML prompt template",
    )
    parser.add_argument("--limit", type=int, default=0, help="Limit rows (0 = all)")
    parser.add_argument(
        "--only-pending",
        action="store_true",
        help="Only process experiences with sync_status=0 (default)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Process all experiences except rejected (sync_status != 2)",
    )
    parser.add_argument("--sleep", type=float, default=1.0, help="Sleep between calls (seconds)")
    parser.add_argument(
        "--no-progress",
        action="store_true",
        help="Disable progress bar output",
    )
    parser.add_argument("--dry-run", action="store_true", help="Do not write DB changes")
    parser.add_argument("--verbose", action="store_true")
    parser.set_defaults(only_pending=True)
    return parser.parse_args()


def load_prompt(path: Path) -> Dict[str, str]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "system" not in data or "user" not in data:
        raise ValueError(f"Prompt must have system/user keys: {path}")
    return {"system": data["system"], "user": data["user"]}


def escape_braces(text: Optional[str]) -> str:
    if not text:
        return ""
    return text.replace("{", "{{").replace("}", "}}")


def build_messages(template: Dict[str, str], exp: Experience) -> List[Dict[str, str]]:
    values = {
        "id": exp.id,
        "category": exp.category_code,
        "section": exp.section,
        "title": escape_braces(exp.title),
        "playbook": escape_braces(exp.playbook),
        "context": escape_braces(exp.context or ""),
    }
    system = template["system"].format(**values)
    user = template["user"].format(**values)
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def extract_json(raw: Any) -> Dict[str, Any]:
    if raw is None:
        raise ValueError("Empty LLM response")
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        raise ValueError(f"Unexpected LLM response type: {type(raw).__name__}")
    text = raw.strip()
    try:
        return json.loads(text)
    except Exception:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError("LLM response does not contain JSON object")
        return json.loads(text[start : end + 1])


def validate_payload(data: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError("Response JSON must be an object")
    decision = data.get("decision")
    if decision not in {"atomic", "split"}:
        raise ValueError("decision must be 'atomic' or 'split'")
    splits = data.get("splits", [])
    if decision == "atomic":
        if splits not in ([], None):
            raise ValueError("splits must be empty for atomic decision")
        return {"decision": decision, "splits": []}
    if not isinstance(splits, list) or len(splits) == 0:
        raise ValueError("splits must be a non-empty list for split decision")
    normalized: List[SplitItem] = []
    invalid_items = 0
    for item in splits:
        if not isinstance(item, dict):
            invalid_items += 1
            continue
        title = (item.get("title") or "").strip()
        playbook = (item.get("playbook") or "").strip()
        context = (item.get("context") or "").strip() or None
        if not title or not playbook:
            invalid_items += 1
            continue
        normalized.append(SplitItem(title=title, playbook=playbook, context=context))
    if invalid_items:
        raise ValueError("Invalid or incomplete split items in response")
    if len(normalized) < 2:
        raise ValueError("Split decision must include at least 2 valid splits")
    return {"decision": decision, "splits": normalized}


def main() -> int:
    args = parse_args()
    config = get_config()
    log_level = getattr(logging, config.log_level, logging.INFO)
    level = log_level
    if args.verbose and level > logging.INFO:
        level = logging.INFO
    logging.basicConfig(level=level)
    logging.getLogger("httpx").setLevel(log_level)
    logging.getLogger("httpcore").setLevel(log_level)

    prompt_path = Path(args.prompt)
    template = load_prompt(prompt_path)

    llm_config, settings, cfg_path = build_llm_config()
    agent = AssistantAgent(
        name="atomicity_split_agent",
        llm_config=llm_config,
        max_consecutive_auto_reply=100000,  # Effectively unlimited (default is 100)
    )

    db = Database(args.db_path)
    db.init_database()
    session = db.get_session()

    q = session.query(Experience)
    if args.all:
        q = q.filter(Experience.sync_status != 2)
    else:
        q = q.filter(Experience.sync_status == 0)
    if args.limit and args.limit > 0:
        q = q.limit(args.limit)

    total = 0
    llm_calls = 0
    split_count = 0
    atomic_count = 0
    error_count = 0

    try:
        items = q.all()
        iterable = items
        if tqdm is not None and not args.no_progress:
            iterable = tqdm(items, desc="Atomicity split", unit="exp")
        for exp in iterable:
            existing = (
                session.query(ExperienceSplitProvenance)
                .filter(ExperienceSplitProvenance.source_experience_id == exp.id)
                .first()
            )
            if existing:
                continue
            total += 1
            group_id = str(uuid.uuid4())
            messages = build_messages(template, exp)
            raw = agent.generate_reply(messages=messages)
            llm_calls += 1
            try:
                payload = validate_payload(extract_json(raw))
            except Exception as exc:
                error_count += 1
                logger.warning("Invalid response for %s: %s", exp.id, exc)
                if not args.dry_run:
                    prov = ExperienceSplitProvenance(
                        source_experience_id=exp.id,
                        split_experience_id=None,
                        split_group_id=group_id,
                        decision="error",
                        model=settings.model,
                        prompt_path=str(prompt_path),
                        raw_response=(f"ERROR: {exc}\nRAW: {raw}")[:8000],
                        created_at=utc_now(),
                    )
                    session.add(prov)
                    session.commit()
                continue

            decision = payload["decision"]
            if decision == "atomic":
                atomic_count += 1
                if not args.dry_run:
                    prov = ExperienceSplitProvenance(
                        source_experience_id=exp.id,
                        split_experience_id=None,
                        split_group_id=group_id,
                        decision="atomic",
                        model=settings.model,
                        prompt_path=str(prompt_path),
                        raw_response=str(raw)[:8000],
                        created_at=utc_now(),
                    )
                    session.add(prov)
                    session.commit()
                if args.sleep:
                    time.sleep(args.sleep)
                continue

            # split path
            splits: List[SplitItem] = payload["splits"]
            split_ids: List[str] = []
            if not args.dry_run:
                for item in splits:
                    new_id = generate_experience_id(exp.category_code)
                    split_ids.append(new_id)
                    new_exp = Experience(
                        id=new_id,
                        category_code=exp.category_code,
                        section=exp.section,
                        title=item.title,
                        playbook=item.playbook,
                        context=item.context,
                        source="atomic_split",
                        sync_status=0,
                        author=get_author(),
                        embedding_status=None,
                        created_at=utc_now(),
                        updated_at=utc_now(),
                    )
                    session.add(new_exp)
                    prov = ExperienceSplitProvenance(
                        source_experience_id=exp.id,
                        split_experience_id=new_id,
                        split_group_id=group_id,
                        decision="split",
                        model=settings.model,
                        prompt_path=str(prompt_path),
                        raw_response=str(raw)[:8000],
                        created_at=utc_now(),
                    )
                    session.add(prov)

                exp.sync_status = 2
                exp.updated_at = utc_now()
                session.commit()

            split_count += 1
            if args.sleep:
                time.sleep(args.sleep)

    finally:
        session.close()

    print(
        "Processed={total} split={split} atomic={atomic} errors={errors} llm_calls={calls}".format(
            total=total,
            split=split_count,
            atomic=atomic_count,
            errors=error_count,
            calls=llm_calls,
        )
    )
    if not args.dry_run:
        total_exp = session.query(Experience).count()
        pending = session.query(Experience).filter(Experience.sync_status == 0).count()
        inactive = session.query(Experience).filter(Experience.sync_status == 2).count()
        prov_rows = session.query(ExperienceSplitProvenance).count()
        print(
            "DB summary: experiences={total} pending={pending} inactive={inactive} provenance_rows={prov}".format(
                total=total_exp,
                pending=pending,
                inactive=inactive,
                prov=prov_rows,
            )
        )
    print(f"[llm] model={settings.model} config={cfg_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
