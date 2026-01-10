#!/usr/bin/env python3
"""LLM-driven atomicity split pre-pass for skills."""
from __future__ import annotations

import argparse
import json
import logging
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
from scripts._config_loader import load_scripts_config
from src.common.storage.database import Database
from src.common.storage.repository import generate_skill_id, get_author
from src.common.storage.schema import CategorySkill, SkillSplitProvenance, utc_now
from src.common.config.config import get_config

logger = logging.getLogger(__name__)


@dataclass
class SplitItem:
    name: str
    description: str
    content: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="LLM atomicity split pre-pass for skills")
    parser.add_argument(
        "--db-path",
        default="data/curation/chl_curation.db",
        help="Path to curation SQLite DB",
    )
    parser.add_argument(
        "--prompt",
        default="scripts/curation/agents/prompts/skill_atomicity_split.yaml",
        help="YAML prompt template",
    )
    parser.add_argument(
        "--outline-prompt",
        default="scripts/curation/agents/prompts/skill_outline_generation.yaml",
        help="YAML prompt for outline generation",
    )
    parser.add_argument("--limit", type=int, default=0, help="Limit rows (0 = all)")
    parser.add_argument(
        "--only-pending",
        action="store_true",
        help="Only process skills with sync_status=0 (default)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Process all skills except superseded (sync_status != 2)",
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


def extract_outline(metadata_json: str | None) -> str:
    if not metadata_json:
        return ""
    try:
        data = json.loads(metadata_json)
    except json.JSONDecodeError:
        return ""
    outline = data.get("chl.outline")
    return outline if isinstance(outline, str) else ""


def merge_metadata(metadata_json: str | None, updates: dict) -> str:
    data = {}
    if metadata_json:
        try:
            data = json.loads(metadata_json) or {}
        except json.JSONDecodeError:
            data = {}
    data.update(updates)
    return json.dumps(data, ensure_ascii=False)


def slugify(value: str) -> str:
    import re
    text = re.sub(r"[^a-z0-9]+", "-", value.strip().lower())
    text = re.sub(r"-{2,}", "-", text).strip("-")
    return text


def build_messages(template: Dict[str, str], skill: CategorySkill) -> List[Dict[str, str]]:
    outline = extract_outline(skill.metadata_json)
    values = {
        "id": skill.id,
        "category": skill.category_code,
        "name": escape_braces(skill.name),
        "description": escape_braces(skill.description),
        "outline": escape_braces(outline),
        "content": escape_braces(skill.content),
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
        name = (item.get("name") or "").strip()
        description = (item.get("description") or "").strip()
        content = (item.get("content") or "").strip()
        if not name or not description or not content:
            invalid_items += 1
            continue
        normalized.append(SplitItem(name=name, description=description, content=content))
    if invalid_items:
        raise ValueError("Invalid or incomplete split items in response")
    if len(normalized) < 2:
        raise ValueError("Split decision must include at least 2 valid splits")
    return {"decision": decision, "splits": normalized}


def load_retry_settings() -> tuple[int, str, list[float]]:
    cfg, _ = load_scripts_config()
    llm_cfg = cfg.get("curation_llm", {})
    return (
        int(llm_cfg.get("max_retries", 0)),
        llm_cfg.get("retry_backoff", "exponential"),
        list(llm_cfg.get("retry_delays", [])),
    )


def delay_for(attempt_index: int, backoff: str, delays: list[float]) -> float:
    if attempt_index <= len(delays):
        return float(delays[attempt_index - 1])
    base = 2.0
    if backoff == "linear":
        return base * attempt_index
    return base * (2 ** (attempt_index - 1))


def generate_outline_with_llm(title: str, content: str, prompt_path: Path) -> str:
    template = load_prompt(prompt_path)
    user_msg = template["user"].format(title=title, content=content, tags="")
    messages = []
    if template["system"]:
        messages.append({"role": "system", "content": template["system"]})
    messages.append({"role": "user", "content": user_msg})

    llm_config, settings, cfg_path = build_llm_config()
    agent = AssistantAgent(name="outline_generator", llm_config=llm_config)
    reply = agent.generate_reply(messages=messages)
    outline = reply if isinstance(reply, str) else json.dumps(reply)
    outline = outline.strip()
    if not outline:
        raise ValueError("LLM returned empty outline")
    logger.info("[outline] %s using %s (%s)", title, settings.model, cfg_path)
    return outline


def main() -> int:
    args = parse_args()
    config = get_config()
    if not bool(getattr(config, "skills_enabled", True)):
        print("Skills are disabled; skipping skill atomicity prepass.")
        return 0
    log_level = getattr(logging, config.log_level, logging.INFO)
    level = log_level
    if args.verbose and level > logging.INFO:
        level = logging.INFO
    logging.basicConfig(level=level)
    logging.getLogger("httpx").setLevel(log_level)
    logging.getLogger("httpcore").setLevel(log_level)

    prompt_path = Path(args.prompt)
    template = load_prompt(prompt_path)
    outline_prompt = Path(args.outline_prompt)

    llm_config, settings, cfg_path = build_llm_config()
    agent = AssistantAgent(
        name="skill_atomicity_agent",
        llm_config=llm_config,
        max_consecutive_auto_reply=100000,
    )

    db = Database(args.db_path)
    db.init_database()
    session = db.get_session()

    q = session.query(CategorySkill)
    if args.all:
        q = q.filter(CategorySkill.sync_status != 2)
    else:
        q = q.filter(CategorySkill.sync_status == 0)
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
            iterable = tqdm(items, desc="Skill atomicity split", unit="skill")
        for skill in iterable:
            existing = (
                session.query(SkillSplitProvenance)
                .filter(SkillSplitProvenance.source_skill_id == skill.id)
                .first()
            )
            if existing:
                continue
            total += 1
            group_id = str(uuid.uuid4())
            messages = build_messages(template, skill)
            max_retries, backoff, delays = load_retry_settings()
            raw = None
            for attempt in range(1, max_retries + 2):
                try:
                    raw = agent.generate_reply(messages=messages)
                    llm_calls += 1
                    break
                except Exception as exc:
                    logger.warning("LLM call failed for %s on attempt %s: %s", skill.id, attempt, exc)
                    if attempt <= max_retries:
                        time.sleep(delay_for(attempt, backoff, delays))
                        continue
                    error_count += 1
                    raw = None
                    break
            if raw is None:
                continue
            try:
                payload = validate_payload(extract_json(raw))
            except Exception as exc:
                error_count += 1
                logger.warning("Invalid response for %s: %s", skill.id, exc)
                if not args.dry_run:
                    prov = SkillSplitProvenance(
                        source_skill_id=skill.id,
                        split_skill_id=None,
                        split_group_id=group_id,
                        decision="error",
                        decision_id=None,
                        curator=get_author(),
                        model=settings.model,
                        prompt_path=str(prompt_path),
                        raw_response=(f"ERROR: {exc}\nRAW: {raw}")[:8000],
                        timestamp=utc_now(),
                    )
                    session.add(prov)
                    session.commit()
                continue

            decision = payload["decision"]
            if decision == "atomic":
                atomic_count += 1
                if not args.dry_run:
                    prov = SkillSplitProvenance(
                        source_skill_id=skill.id,
                        split_skill_id=None,
                        split_group_id=group_id,
                        decision="atomic",
                        decision_id=None,
                        curator=get_author(),
                        model=settings.model,
                        prompt_path=str(prompt_path),
                        raw_response=str(raw)[:8000],
                        timestamp=utc_now(),
                    )
                    session.add(prov)
                    session.commit()
                if args.sleep:
                    time.sleep(args.sleep)
                continue

            splits: List[SplitItem] = payload["splits"]
            if args.dry_run:
                print(f"  [dry-run] split skill {skill.id} -> {len(splits)} parts")
                for idx, item in enumerate(splits, start=1):
                    preview = (item.description or "").strip()
                    if len(preview) > 120:
                        preview = preview[:117] + "..."
                    print(f"    {idx}. {item.name}: {preview}")
                split_count += 1
                if args.sleep:
                    time.sleep(args.sleep)
                continue
            if not args.dry_run:
                existing_names = {row.name for row in session.query(CategorySkill.name).all()}
                for item in splits:
                    base_name = slugify(item.name) or slugify(skill.id) or skill.id.lower()
                    name = base_name
                    counter = 2
                    while name in existing_names:
                        name = f"{base_name}-{counter}"
                        counter += 1
                    existing_names.add(name)

                    new_id = generate_skill_id(skill.category_code)
                    outline = generate_outline_with_llm(item.name, item.content, outline_prompt)
                    metadata_json = merge_metadata(
                        skill.metadata_json,
                        {
                            "chl.outline": outline,
                            "chl.split.group_id": group_id,
                            "chl.split.source_id": skill.id,
                        },
                    )
                    new_skill = CategorySkill(
                        id=new_id,
                        category_code=skill.category_code,
                        name=name,
                        description=item.description,
                        content=item.content,
                        license=skill.license,
                        compatibility=skill.compatibility,
                        metadata_json=metadata_json,
                        allowed_tools=skill.allowed_tools,
                        model=skill.model,
                        source="atomic_split",
                        sync_status=0,
                        author=get_author(),
                        embedding_status="pending",
                        created_at=utc_now(),
                        updated_at=utc_now(),
                    )
                    session.add(new_skill)
                    prov = SkillSplitProvenance(
                        source_skill_id=skill.id,
                        split_skill_id=new_id,
                        split_group_id=group_id,
                        decision="split",
                        decision_id=None,
                        curator=get_author(),
                        model=settings.model,
                        prompt_path=str(prompt_path),
                        raw_response=str(raw)[:8000],
                        timestamp=utc_now(),
                    )
                    session.add(prov)

                skill.sync_status = 2
                skill.updated_at = utc_now()
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
    print(f"[llm] model={settings.model} config={cfg_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
