from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

import yaml

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.common.storage.schema import Experience


def load_community(communities_path: Path, community_id: str) -> Dict[str, Any]:
    """Load a single community by id from the communities JSON."""
    data = json.loads(Path(communities_path).read_text(encoding="utf-8"))
    for comm in data.get("communities", []):
        if comm.get("id") == community_id:
            return comm
    raise KeyError(f"Community {community_id} not found in {communities_path}")


def fetch_member_records(db_path: Path, member_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    """Fetch member metadata from the DB; missing rows are simply absent."""
    engine = create_engine(f"sqlite:///{db_path}")
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        rows = session.query(Experience).filter(Experience.id.in_(member_ids)).all()
        return {
            row.id: {
                "title": row.title,
                "playbook": row.playbook,
                "context": row.context,
                "category": row.category_code,
                "sync_status": row.sync_status,
            }
            for row in rows
        }
    finally:
        session.close()


def load_prompt_template(prompt_path: Path) -> Dict[str, str]:
    """Load YAML prompt template with 'system' and 'user' keys."""
    data = yaml.safe_load(prompt_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Prompt template must be a mapping: {prompt_path}")
    if "system" not in data or "user" not in data:
        raise ValueError(f"Prompt template missing 'system' or 'user': {prompt_path}")
    return {"system": data["system"], "user": data["user"]}


def build_prompt_messages(
    community: Dict[str, Any],
    members: Dict[str, Dict[str, Any]],
    round_index: int = 1,
    top_k_edges: int = 20,
    prompt_path: Path | None = None,
) -> List[Dict[str, str]]:
    """Render system/user messages for the LLM based on community context using a YAML template."""
    prompt_path = prompt_path or Path("scripts/curation/agents/prompts/curation_prompt.yaml")
    template = load_prompt_template(prompt_path)

    members_lines: List[str] = []
    for mid in community.get("members", []):
        rec = members.get(mid, {})
        title = rec.get("title") or "<missing title>"
        playbook = rec.get("playbook") or ""
        context = rec.get("context") or ""
        members_lines.append(
            f"- {mid}: {title}\n  Playbook: {playbook}\n  Context: {context}"
        )

    edges = community.get("edges", [])
    top_edges = sorted(edges, key=lambda e: e[2] if len(e) > 2 else 0, reverse=True)[:top_k_edges]
    edge_lines = [
        f"- {src} ↔ {dst}: weight={weight:.3f}"
        for src, dst, weight in top_edges
    ]

    format_vars = {
        "community_id": community.get("id"),
        "category": community.get("category"),
        "size": community.get("size"),
        "round_index": round_index,
        "priority_score": community.get("priority_score"),
        "oversized": community.get("oversized"),
        "members_block": "\n".join(members_lines),
        "edges_block": "\n".join(edge_lines),
    }

    try:
        system = template["system"].format(**format_vars)
        user = template["user"].format(**format_vars)
    except KeyError as exc:
        missing = exc.args[0] if exc.args else "<unknown>"
        raise ValueError(
            f"Prompt template references missing variable '{missing}'. "
            f"Available keys: {sorted(format_vars.keys())}"
        ) from exc

    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def validate_response(raw: str, allowed_ids: List[str]) -> Tuple[bool, List[str], List[str], Dict[str, Any]]:
    """Validate an LLM raw string reply against the contract.

    Returns:
        ok: True if no hard errors
        errors: list of fatal validation errors
        warnings: list of non-fatal issues (e.g., downgraded merges)
        normalized: possibly adjusted payload (decision/merges)
    """
    errors: List[str] = []
    warnings: List[str] = []
    try:
        data = json.loads(raw)
    except Exception as exc:
        return False, [f"Response is not valid JSON: {exc}"], [], {}

    if not isinstance(data, dict):
        return False, ["Response JSON must be an object"], [], {}

    decision = data.get("decision")
    merges = data.get("merges")

    valid_decisions = {"merge_all", "merge_subset", "keep_separate", "manual_review"}
    if decision not in valid_decisions:
        errors.append(f"Invalid decision '{decision}' (must be one of {sorted(valid_decisions)})")

    normalized_merges: List[List[str]] = []
    if decision in {"merge_all", "merge_subset"}:
        if merges is None:
            errors.append("Missing 'merges' for merge decision")
        elif not isinstance(merges, list):
            errors.append("'merges' must be a list")
        elif not merges:
            warnings.append("Empty 'merges' for merge decision; downgrading to keep_separate")
            decision = "keep_separate"
        else:
            for pair in merges:
                if not isinstance(pair, list) or len(pair) != 2:
                    warnings.append(f"Invalid merge pair shape (skipped): {pair}")
                    continue
                a, b = pair
                if a not in allowed_ids or b not in allowed_ids:
                    warnings.append(f"Merge pair contains unknown id (skipped): {pair}")
                    continue
                normalized_merges.append([a, b])
            if not normalized_merges:
                warnings.append("All merge pairs invalid or filtered; downgrading to keep_separate")
                decision = "keep_separate"
    else:
        normalized_merges = []

    normalized = dict(data)
    normalized["decision"] = decision
    normalized["merges"] = normalized_merges

    return len(errors) == 0, errors, warnings, normalized


__all__ = [
    "load_community",
    "fetch_member_records",
    "load_prompt_template",
    "build_prompt_messages",
    "validate_response",
]
