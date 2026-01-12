#!/usr/bin/env python3
"""Analyze skill candidate pairs with LLM and auto-apply merge/split decisions."""
from __future__ import annotations

import argparse
import csv
import json
import time
from pathlib import Path
import shutil
from typing import Any, Dict, List, Optional, Tuple

import yaml
try:
    from tqdm import tqdm
except Exception:  # pragma: no cover - optional dependency
    tqdm = None
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from scripts._config_loader import load_scripts_config
from scripts.curation.agents.autogen_openai_completion_agent import build_llm_config
from src.common.config.config import get_config
from src.common.storage.database import Database
from src.common.storage.repository import generate_skill_id, get_author
from src.common.storage.schema import CategorySkill, SkillSplitProvenance, SkillCurationDecision, utc_now


def parse_args() -> argparse.Namespace:
    try:
        cfg, _ = load_scripts_config()
        cur = cfg.get("curation", {})
        default_db = cur.get("curation_db_path", "data/curation/chl_curation.db")
    except Exception:
        default_db = "data/curation/chl_curation.db"

    parser = argparse.ArgumentParser(description="LLM relationship analysis for skill candidates")
    parser.add_argument("--db-path", default=default_db, help="Path to curation SQLite DB")
    parser.add_argument("--candidates", default="data/curation/skill_candidates.jsonl", help="Input candidates JSONL")
    parser.add_argument("--prompt", default="scripts/curation/agents/prompts/skill_relationship_analysis.yaml", help="Prompt template")
    parser.add_argument("--outline-prompt", default="scripts/curation/agents/prompts/skill_outline_generation.yaml", help="Outline prompt")
    parser.add_argument("--decision-log", default="data/curation/skill_decisions_log.csv", help="Decision log CSV")
    parser.add_argument("--auto-threshold", type=float, default=0.85, help="Auto-analyze threshold")
    parser.add_argument("--review-threshold", type=float, default=0.70, help="Review threshold")
    parser.add_argument("--limit", type=int, default=0, help="Limit pairs (0=all)")
    parser.add_argument("--sleep", type=float, default=0.5, help="Sleep between LLM calls")
    parser.add_argument("--dry-run", action="store_true", help="Do not write DB changes")
    return parser.parse_args()


def load_prompt(path: Path) -> Dict[str, str]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "system" not in data or "user" not in data:
        raise ValueError(f"Prompt must have system/user keys: {path}")
    return {"system": data["system"], "user": data["user"]}


def parse_metadata(raw: Optional[str]) -> dict:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def merge_metadata(metadata_json: Optional[str], updates: dict) -> str:
    data = parse_metadata(metadata_json)
    data.update(updates)
    return json.dumps(data, ensure_ascii=False)


def extract_outline(metadata_json: Optional[str]) -> str:
    data = parse_metadata(metadata_json)
    outline = data.get("chl.outline")
    return outline if isinstance(outline, str) else ""


def slugify(value: str) -> str:
    import re
    text = re.sub(r"[^a-z0-9]+", "-", value.strip().lower())
    text = re.sub(r"-{2,}", "-", text).strip("-")
    return text


def validate_name(value: str) -> str:
    name = slugify(value)
    if not name:
        raise ValueError("Invalid empty skill name")
    return name


def clamp_description(desc: str) -> str:
    desc = (desc or "").strip()
    if not desc:
        return "Merged skill."
    if len(desc) > 1024:
        return desc[:1024]
    return desc


def build_messages(template: Dict[str, str], a: CategorySkill, b: CategorySkill) -> List[Dict[str, str]]:
    values = {
        "skill_a_id": a.id,
        "skill_a_category": a.category_code,
        "skill_a_name": a.name,
        "skill_a_description": a.description,
        "skill_a_outline": extract_outline(a.metadata_json),
        "skill_a_content": a.content,
        "skill_b_id": b.id,
        "skill_b_category": b.category_code,
        "skill_b_name": b.name,
        "skill_b_description": b.description,
        "skill_b_outline": extract_outline(b.metadata_json),
        "skill_b_content": b.content,
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


def load_retry_settings() -> Tuple[int, str, List[float]]:
    cfg, _ = load_scripts_config()
    llm_cfg = cfg.get("curation_llm", {})
    return (
        int(llm_cfg.get("max_retries", 0)),
        llm_cfg.get("retry_backoff", "exponential"),
        list(llm_cfg.get("retry_delays", [])),
    )


def delay_for(attempt_index: int, backoff: str, delays: List[float]) -> float:
    if attempt_index <= len(delays):
        return float(delays[attempt_index - 1])
    base = 2.0
    if backoff == "linear":
        return base * attempt_index
    return base * (2 ** (attempt_index - 1))


def call_llm(messages: List[Dict[str, str]]) -> Any:
    llm_config, settings, cfg_path = build_llm_config()
    from autogen import AssistantAgent
    agent = AssistantAgent(name="skill_relationship_agent", llm_config=llm_config)
    max_retries, backoff, delays = load_retry_settings()
    last_exc = None
    for attempt in range(1, max_retries + 2):
        try:
            reply = agent.generate_reply(messages=messages)
            return reply
        except Exception as exc:
            last_exc = exc
            if attempt <= max_retries:
                time.sleep(delay_for(attempt, backoff, delays))
                continue
            raise RuntimeError(f"LLM call failed after {attempt} attempts: {exc}") from exc
    raise RuntimeError(f"LLM call failed: {last_exc}")


def generate_outline_with_llm(title: str, content: str, prompt_path: Path) -> str:
    template = load_prompt(prompt_path)
    user_msg = template["user"].format(title=title, content=content, tags="")
    messages = []
    if template["system"]:
        messages.append({"role": "system", "content": template["system"]})
    messages.append({"role": "user", "content": user_msg})
    reply = call_llm(messages)
    outline = reply if isinstance(reply, str) else json.dumps(reply)
    outline = outline.strip()
    if not outline:
        raise ValueError("LLM returned empty outline")
    return outline


def write_log_header(path: Path) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            [
                "timestamp",
                "src_id",
                "dst_id",
                "weight",
                "embed_score",
                "rerank_score",
                "relationship",
                "action",
                "confidence",
                "reasoning",
                "model",
                "raw_response",
            ]
        )


def append_log(path: Path, row: dict) -> None:
    def normalize(value: Any) -> str:
        if value is None:
            return ""
        text = str(value)
        return text.replace("\n", " ").strip()

    values = [
        row.get("timestamp", ""),
        row.get("src_id", ""),
        row.get("dst_id", ""),
        row.get("weight", ""),
        row.get("embed_score", ""),
        row.get("rerank_score", ""),
        row.get("relationship", ""),
        row.get("action", ""),
        row.get("confidence", ""),
        normalize(row.get("reasoning", "")),
        row.get("model", ""),
        normalize(row.get("raw_response", "")),
    ]
    with path.open("a", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(values)


def main() -> int:
    args = parse_args()
    config = get_config()
    if not bool(getattr(config, "skills_enabled", True)):
        print("Skills are disabled; skipping relationship analysis.")
        return 0

    # Snapshot before auto-apply
    if not args.dry_run:
        snapshot_dir = Path("data/curation/snapshots")
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        ts = utc_now().strftime("%Y%m%d-%H%M%S")
        snap_path = snapshot_dir / f"skills_autoapply_{ts}.db"
        shutil.copy(Path(args.db_path), snap_path)
        snapshots = sorted(snapshot_dir.glob("skills_autoapply_*.db"))
        if len(snapshots) > 3:
            for old in snapshots[:-3]:
                try:
                    old.unlink()
                except Exception:
                    pass

    candidates_path = Path(args.candidates)
    if not candidates_path.exists():
        print(f"❌ Candidates file not found: {candidates_path}")
        return 1

    prompt_path = Path(args.prompt)
    outline_prompt = Path(args.outline_prompt)
    template = load_prompt(prompt_path)

    db = Database(args.db_path)
    db.init_database()
    engine = create_engine(f"sqlite:///{args.db_path}")
    Session = sessionmaker(bind=engine)
    session = Session()

    decision_log_path = Path(args.decision_log)
    write_log_header(decision_log_path)
    state_path = Path("data/curation/skill_relationship_state.json")
    state_path.parent.mkdir(parents=True, exist_ok=True)
    processed_pairs: set[str] = set()
    if state_path.exists():
        try:
            state_data = json.loads(state_path.read_text(encoding="utf-8"))
            processed_pairs = set(state_data.get("processed_pairs", []))
        except Exception:
            processed_pairs = set()

    try:
        # Load candidates
        candidates = []
        with candidates_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                candidates.append(json.loads(line))
        candidates.sort(key=lambda x: float(x.get("weight", 0)), reverse=True)

        processed = set()
        count = 0
        flagged = 0
        kept = 0
        merged = 0
        split = 0
        conflict = 0

        iterable = candidates
        if tqdm is not None:
            iterable = tqdm(candidates, desc="Skill relationship analysis", unit="pair")

        for rec in iterable:
            if args.limit and count >= args.limit:
                break
            weight = float(rec.get("weight", 0))
            if weight < args.review_threshold:
                continue
            if weight < args.auto_threshold:
                append_log(
                    Path(args.decision_log),
                    {
                        "timestamp": utc_now().isoformat(),
                        "src_id": rec.get("src"),
                        "dst_id": rec.get("dst"),
                        "weight": weight,
                        "embed_score": rec.get("embed_score"),
                        "rerank_score": rec.get("rerank_score"),
                        "relationship": "",
                        "action": "flag_for_review",
                        "confidence": "",
                        "reasoning": "Below auto threshold; manual review required",
                        "model": "",
                        "raw_response": "",
                    },
                )
                flagged += 1
                continue

            src_id = rec["src"]
            dst_id = rec["dst"]
            pair_key = "||".join(sorted([src_id, dst_id]))
            if pair_key in processed_pairs:
                continue
            if src_id in processed or dst_id in processed:
                continue
            a = session.query(CategorySkill).filter(CategorySkill.id == src_id).one_or_none()
            b = session.query(CategorySkill).filter(CategorySkill.id == dst_id).one_or_none()
            if not a or not b:
                continue

            messages = build_messages(template, a, b)
            raw = call_llm(messages)
            data = extract_json(raw)

            relationship = data.get("relationship", "")
            action = data.get("action", "")
            confidence = data.get("confidence", "")
            reasoning = data.get("reasoning", "")

            model_name = build_llm_config()[1].model
            log_row = {
                "timestamp": utc_now().isoformat(),
                "src_id": src_id,
                "dst_id": dst_id,
                "weight": weight,
                "embed_score": rec.get("embed_score"),
                "rerank_score": rec.get("rerank_score"),
                "relationship": relationship,
                "action": action,
                "confidence": confidence,
                "reasoning": reasoning,
                "model": model_name,
                "raw_response": json.dumps(data, ensure_ascii=False),
            }
            append_log(decision_log_path, log_row)
            if not args.dry_run:
                decision_row = SkillCurationDecision(
                    skill_a_id=src_id,
                    skill_b_id=dst_id,
                    relationship=relationship or "",
                    action=action or "",
                    confidence=str(confidence) if confidence is not None else None,
                    curator=get_author(),
                    timestamp=utc_now(),
                    model=model_name,
                    prompt_path=str(prompt_path),
                    raw_response=json.dumps(data, ensure_ascii=False)[:8000],
                    status="auto",
                    conflict_flag=1 if action == "flag_conflict" else 0,
                    resolution_notes=None,
                )
                session.add(decision_row)
                processed_pairs.add(pair_key)
                state_path.write_text(
                    json.dumps({"processed_pairs": sorted(processed_pairs)}, ensure_ascii=False),
                    encoding="utf-8",
                )

            if args.dry_run:
                count += 1
                continue

            if action == "merge":
                merged += 1
                merge = data.get("merge") or {}
                merged_name = validate_name(merge.get("name") or f"{a.name}-{b.name}")
                merged_desc = clamp_description(merge.get("description") or "")
                merged_content = (merge.get("content") or "").strip()
                if not merged_content:
                    merged_content = f"{a.content}\n\n{b.content}"

                # Avoid name collisions
                existing_names = {row.name for row in session.query(CategorySkill.name).all()}
                base_name = merged_name
                counter = 2
                while merged_name in existing_names:
                    merged_name = f"{base_name}-{counter}"
                    counter += 1

                outline = generate_outline_with_llm(merged_name, merged_content, outline_prompt)
                field_conflicts = {}
                for field in ("license", "compatibility", "allowed_tools", "model"):
                    av = getattr(a, field)
                    bv = getattr(b, field)
                    if av and bv and av != bv:
                        field_conflicts[field] = {"a": av, "b": bv}

                merged_metadata = merge_metadata(
                    a.metadata_json,
                    {
                        "chl.outline": outline,
                        "chl.merge.from_ids": [a.id, b.id],
                        "chl.merge.from_authors": [a.author, b.author],
                        "chl.merge.reason": reasoning,
                        "chl.merge.field_conflicts": field_conflicts or None,
                    },
                )
                if a.category_code != b.category_code:
                    merged_metadata = merge_metadata(
                        merged_metadata,
                        {"chl.merge.category_conflict": [a.category_code, b.category_code]},
                    )

                new_skill = CategorySkill(
                    id=generate_skill_id(a.category_code),
                    category_code=a.category_code,
                    name=merged_name,
                    description=merged_desc,
                    content=merged_content,
                    license=a.license or b.license,
                    compatibility=a.compatibility or b.compatibility,
                    metadata_json=merged_metadata,
                    allowed_tools=a.allowed_tools or b.allowed_tools,
                    model=a.model or b.model,
                    source="auto_merge",
                    sync_status=1,
                    author=get_author(),
                    embedding_status="pending",
                    created_at=utc_now(),
                    updated_at=utc_now(),
                )
                session.add(new_skill)
                a.sync_status = 2
                b.sync_status = 2
                a.updated_at = utc_now()
                b.updated_at = utc_now()
                session.commit()
                processed.update({a.id, b.id})
                processed_pairs.add(pair_key)
                state_path.write_text(
                    json.dumps({"processed_pairs": sorted(processed_pairs)}, ensure_ascii=False),
                    encoding="utf-8",
                )

            elif action == "split":
                split += 1
                splits = data.get("splits") or []
                if not splits:
                    count += 1
                    continue
                group_id = f"split-{a.id}-{int(time.time())}"
                existing_names = {row.name for row in session.query(CategorySkill.name).all()}
                for item in splits:
                    name = validate_name(item.get("name") or f"{a.name}-split")
                    base_name = name
                    counter = 2
                    while name in existing_names:
                        name = f"{base_name}-{counter}"
                        counter += 1
                    existing_names.add(name)
                    desc = clamp_description(item.get("description") or "")
                    content = (item.get("content") or "").strip()
                    outline = generate_outline_with_llm(name, content, outline_prompt)
                    metadata_json = merge_metadata(
                        a.metadata_json,
                        {
                            "chl.outline": outline,
                            "chl.split.group_id": group_id,
                            "chl.split.source_id": a.id,
                        },
                    )
                    new_skill = CategorySkill(
                        id=generate_skill_id(a.category_code),
                        category_code=a.category_code,
                        name=name,
                        description=desc,
                        content=content,
                        license=a.license,
                        compatibility=a.compatibility,
                        metadata_json=metadata_json,
                        allowed_tools=a.allowed_tools,
                        model=a.model,
                        source="auto_split",
                        sync_status=1,
                        author=get_author(),
                        embedding_status="pending",
                        created_at=utc_now(),
                        updated_at=utc_now(),
                    )
                    session.add(new_skill)
                    prov = SkillSplitProvenance(
                        source_skill_id=a.id,
                        split_skill_id=new_skill.id,
                        split_group_id=group_id,
                        decision="split",
                        decision_id=None,
                        curator=get_author(),
                        model=model_name,
                        prompt_path=str(prompt_path),
                        raw_response=json.dumps(data, ensure_ascii=False)[:8000],
                        timestamp=utc_now(),
                    )
                    session.add(prov)

                a.sync_status = 2
                a.updated_at = utc_now()
                session.commit()
                processed.add(a.id)
                processed_pairs.add(pair_key)
                state_path.write_text(
                    json.dumps({"processed_pairs": sorted(processed_pairs)}, ensure_ascii=False),
                    encoding="utf-8",
                )

            elif action == "flag_conflict":
                conflict += 1
                a.sync_status = 0
                b.sync_status = 0
                a.updated_at = utc_now()
                b.updated_at = utc_now()
                session.commit()
                processed.update({a.id, b.id})
                processed_pairs.add(pair_key)
                state_path.write_text(
                    json.dumps({"processed_pairs": sorted(processed_pairs)}, ensure_ascii=False),
                    encoding="utf-8",
                )

            else:
                # keep_separate or unknown: no action
                kept += 1

            count += 1
            if args.sleep:
                time.sleep(args.sleep)

    finally:
        session.close()

    print(
        "Summary: processed={processed} flagged={flagged} merge={merged} split={split} conflict={conflict} keep={kept}".format(
            processed=count,
            flagged=flagged,
            merged=merged,
            split=split,
            conflict=conflict,
            kept=kept,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
