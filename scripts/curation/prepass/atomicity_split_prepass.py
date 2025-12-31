#!/usr/bin/env python3
"""LLM-driven atomicity split pre-pass for experiences."""
from __future__ import annotations

import argparse
import json
import logging
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from autogen import AssistantAgent

from scripts.curation.agents.autogen_openai_completion_agent import build_llm_config
from src.common.storage.database import Database
from src.common.storage.repository import generate_experience_id, get_author
from src.common.storage.schema import Experience, ExperienceSplitProvenance, utc_now

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
    parser.add_argument(
        "--log-atomic",
        action="store_true",
        help="Record provenance rows even for atomic decisions",
    )
    parser.add_argument("--sleep", type=float, default=0.0, help="Sleep between calls (seconds)")
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
    for item in splits:
        if not isinstance(item, dict):
            continue
        title = (item.get("title") or "").strip()
        playbook = (item.get("playbook") or "").strip()
        context = (item.get("context") or "").strip() or None
        if not title or not playbook:
            continue
        normalized.append(SplitItem(title=title, playbook=playbook, context=context))
    if not normalized:
        raise ValueError("No valid splits found in response")
    return {"decision": decision, "splits": normalized}


def main() -> int:
    args = parse_args()
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING)

    prompt_path = Path(args.prompt)
    template = load_prompt(prompt_path)

    llm_config, settings, cfg_path = build_llm_config()
    agent = AssistantAgent(name="atomicity_split_agent", llm_config=llm_config)

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
    split_count = 0
    atomic_count = 0
    error_count = 0

    try:
        for exp in q.all():
            total += 1
            messages = build_messages(template, exp)
            raw = agent.generate_reply(messages=messages)
            try:
                payload = validate_payload(extract_json(raw))
            except Exception as exc:
                error_count += 1
                logger.warning("Invalid response for %s: %s", exp.id, exc)
                continue

            decision = payload["decision"]
            if decision == "atomic":
                atomic_count += 1
                if args.log_atomic and not args.dry_run:
                    prov = ExperienceSplitProvenance(
                        source_experience_id=exp.id,
                        split_experience_id=None,
                        split_group_id=str(uuid.uuid4()),
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
            group_id = str(uuid.uuid4())
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
        "Processed={total} split={split} atomic={atomic} errors={errors}"
        .format(total=total, split=split_count, atomic=atomic_count, errors=error_count)
    )
    print(f"[llm] model={settings.model} config={cfg_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
