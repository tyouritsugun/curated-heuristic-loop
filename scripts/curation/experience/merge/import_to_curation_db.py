#!/usr/bin/env python3
"""
Import merged CSVs into curation database.

This script reads the merged CSV files (experiences.csv, skills.csv) and
imports them into the curation database. All entries are
marked with embedding_status='pending' for later processing.

Usage:
    # With default paths from scripts_config.yaml:
    python scripts/curation/experience/merge/import_to_curation_db.py

    # With explicit paths:
    python scripts/curation/experience/merge/import_to_curation_db.py \\
        --input data/curation/merged \\
        --db-path data/curation/chl_curation.db
"""

import argparse
import csv
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add project root to sys.path
repo_root = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(repo_root))
prompt_root = Path(__file__).resolve().parents[4]

import yaml
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from src.common.storage.database import Database
from src.common.storage.schema import Category, Experience, CategorySkill
from src.common.config.categories import get_categories
from scripts._config_loader import load_scripts_config
from scripts.curation.agents.autogen_openai_completion_agent import build_llm_config


def parse_args():
    # Load config to get defaults
    try:
        config, _ = load_scripts_config()
        curation_config = config.get("curation", {})
        default_input_dir = curation_config.get("merged_output_dir", "data/curation/merged")
        default_db_path = curation_config.get("curation_db_path", "data/curation/chl_curation.db")
    except Exception:
        # Fallback to hard-coded defaults if config loading fails
        default_input_dir = "data/curation/merged"
        default_db_path = "data/curation/chl_curation.db"

    parser = argparse.ArgumentParser(
        description="Import merged CSVs into curation database"
    )
    parser.add_argument(
        "--input",
        help=f"Input directory containing merged CSVs (default: {default_input_dir})",
        default=default_input_dir,
    )
    parser.add_argument(
        "--db-path",
        default=default_db_path,
        help=f"Path to curation database (default: {default_db_path})",
    )
    return parser.parse_args()


def read_csv(file_path: Path):
    """Read CSV file and return list of dicts."""
    if not file_path.exists():
        return []

    with open(file_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return list(reader)


def parse_datetime(dt_str: str):
    """Parse ISO datetime string, return None if empty. Normalizes to UTC if naive."""
    if not dt_str:
        return None
    try:
        dt = datetime.fromisoformat(dt_str)
        # If datetime is naive (no timezone info), assume UTC
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, AttributeError):
        return None


def slugify(value: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", value.strip().lower())
    text = re.sub(r"-{2,}", "-", text).strip("-")
    return text


def load_category_prompt(prompt_path: Path) -> dict:
    with open(prompt_path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def build_category_list(categories: list[Category]) -> str:
    lines = []
    for cat in categories:
        desc = (cat.description or "").strip()
        if desc:
            lines.append(f"- {cat.code}: {cat.name} — {desc}")
        else:
            lines.append(f"- {cat.code}: {cat.name}")
    return "\n".join(lines)


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


def map_category_with_llm(
    title: str,
    content: str,
    outline: str,
    category_list: str,
    prompt_path: Path,
    *,
    llm_config=None,
    settings=None,
    cfg_path=None,
) -> tuple[str | None, float | None]:
    prompt = load_category_prompt(prompt_path)
    system_msg = prompt.get("system", "")
    user_msg = prompt.get("user", "")
    user_msg = user_msg.format(
        title=title,
        content=content,
        outline=outline or "",
        category_list=category_list,
    )
    messages = []
    if system_msg:
        messages.append({"role": "system", "content": system_msg})
    messages.append({"role": "user", "content": user_msg})

    if llm_config is None or settings is None or cfg_path is None:
        llm_config, settings, cfg_path = build_llm_config()
    try:
        from autogen import AssistantAgent
    except Exception as exc:  # pragma: no cover - dependency issue
        raise RuntimeError(f"autogen is required to call LLM: {exc}")
    agent = AssistantAgent(name="category_mapper", llm_config=llm_config)

    reply = agent.generate_reply(messages=messages)
    raw = reply if isinstance(reply, str) else json.dumps(reply)
    try:
        data = reply if isinstance(reply, dict) else json.loads(raw)
    except json.JSONDecodeError:
        # Best-effort JSON extraction
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            raise ValueError("LLM returned non-JSON response")
        data = json.loads(match.group(0))

    code = (data.get("category_code") or "").strip()
    confidence = data.get("confidence")
    if not code:
        raise ValueError("LLM response missing category_code")
    try:
        confidence_val = float(confidence) if confidence is not None else None
    except (TypeError, ValueError):
        confidence_val = None
    return code, confidence_val


def generate_outline_with_llm(
    title: str,
    content: str,
    tags: str,
    prompt_path: Path,
    *,
    llm_config=None,
    settings=None,
    cfg_path=None,
) -> str:
    prompt = load_category_prompt(prompt_path)
    system_msg = prompt.get("system", "")
    user_msg = prompt.get("user", "")
    user_msg = user_msg.format(
        title=title,
        content=content,
        tags=tags or "",
    )
    messages = []
    if system_msg:
        messages.append({"role": "system", "content": system_msg})
    messages.append({"role": "user", "content": user_msg})

    if llm_config is None or settings is None or cfg_path is None:
        llm_config, settings, cfg_path = build_llm_config()
    try:
        from autogen import AssistantAgent
    except Exception as exc:  # pragma: no cover - dependency issue
        raise RuntimeError(f"autogen is required to call LLM: {exc}")
    agent = AssistantAgent(name="outline_generator", llm_config=llm_config)

    reply = agent.generate_reply(messages=messages)
    outline = reply if isinstance(reply, str) else json.dumps(reply)
    outline = outline.strip()
    if not outline:
        raise ValueError("LLM returned empty outline")
    return outline


def main():
    args = parse_args()

    input_dir = Path(args.input)
    db_path = Path(args.db_path)
    curation_root = db_path.parent

    # Validate inputs
    if not input_dir.exists():
        print(f"❌ Error: Input directory does not exist: {input_dir}", file=sys.stderr)
        sys.exit(1)

    # Reset curation DB and related artifacts by default
    if db_path.exists():
        db_path.unlink()
        print(f"✓ Deleted curation DB: {db_path}")

    artifacts = [
        curation_root / "faiss_index",
        curation_root / "neighbors.jsonl",
        curation_root / "similarity_graph.pkl",
        curation_root / "communities.json",
        curation_root / "communities_rerank.json",
        curation_root / "merge_audit.csv",
        curation_root / "evaluation_log.csv",
        curation_root / "morning_report.md",
        curation_root / ".curation_state.json",
        curation_root / ".curation_state_loop.json",
        curation_root / "rerank_cache",
    ]
    for path in artifacts:
        if path.exists():
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
            print(f"✓ Removed artifact: {path}")

    # Read CSVs
    print(f"Reading merged CSVs from: {input_dir}")
    print()

    experiences_data = read_csv(input_dir / "experiences.csv")

    # Try to find skills file (try new name first, then legacy names)
    skills_path = input_dir / "skills.csv"
    if not skills_path.exists():
        skills_path = input_dir / "Skills.csv"
    if not skills_path.exists():
        skills_path = input_dir / "manuals.csv"
    if not skills_path.exists():
        skills_path = input_dir / "Manuals.csv"

    skills_data = read_csv(skills_path)

    print("  Categories: canonical (code-defined)")
    print(f"  Experiences: {len(experiences_data)} rows")
    print(f"  Skills: {len(skills_data)} rows")
    print()

    # Create database session (ensure schema exists)
    db = Database(str(db_path))
    db.init_database()
    engine = create_engine(f"sqlite:///{db_path}")
    Session = sessionmaker(bind=engine)
    session = Session()

    try:
        # Import categories (canonical taxonomy only)
        print("Importing categories...")
        categories = get_categories()
        for cat in categories:
            session.add(
                Category(
                    code=cat["code"],
                    name=cat["name"],
                    description=cat["description"],
                    created_at=datetime.now(timezone.utc),
                )
            )
        session.commit()
        print(f"✓ Seeded {len(categories)} categories from canonical taxonomy")
        print()

        # Load categories for validation + LLM mapping
        category_rows = session.query(Category).all()
        category_codes = {cat.code for cat in category_rows}
        category_list_text = build_category_list(category_rows)
        category_prompt_path = prompt_root / "scripts/curation/agents/prompts/skill_category_mapping.yaml"
        outline_prompt_path = prompt_root / "scripts/curation/agents/prompts/skill_outline_generation.yaml"

        # Import experiences
        print("Importing experiences...")
        skipped_experiences = 0
        for row in experiences_data:
            exp_id = (row.get("id") or "").strip()
            category_code = (row.get("category_code") or "").strip()
            section = (row.get("section") or "").strip()
            title = (row.get("title") or "").strip()
            playbook = (row.get("playbook") or "").strip()
            if not exp_id or not category_code or not section or not title or not playbook:
                skipped_experiences += 1
                continue
            experience = Experience(
                id=exp_id,
                category_code=category_code,
                section=section,
                title=title,
                playbook=playbook,
                context=row.get("context") or None,
                source=row.get("source") or "local",
                sync_status=0,  # Always set to 0 (PENDING) for curation regardless of source value
                author=row.get("author") or None,
                embedding_status="pending",  # Always mark as pending for curation
                created_at=parse_datetime(row.get("created_at")) or datetime.now(timezone.utc),
                updated_at=parse_datetime(row.get("updated_at")) or datetime.now(timezone.utc),
                synced_at=parse_datetime(row.get("synced_at")),
                exported_at=parse_datetime(row.get("exported_at")),
            )
            session.add(experience)

        session.commit()
        print(f"✓ Imported {len(experiences_data) - skipped_experiences} experiences")
        print(f"  All marked as embedding_status='pending'")
        if skipped_experiences:
            print(f"  Skipped {skipped_experiences} empty/invalid experience rows")
        print()

        # Import skills
        print("Importing skills...")
        skipped_skills = 0
        used_names = set()
        outline_generated = 0
        category_mapped = 0
        normalized_skipped = 0
        llm_config = None
        settings = None
        cfg_path = None
        try:
            llm_config, settings, cfg_path = build_llm_config()
        except Exception:
            pass
        for row in skills_data:
            skill_id = (row.get("id") or "").strip()
            category_code = (row.get("category_code") or "").strip()
            name_raw = (row.get("name") or row.get("title") or "").strip()
            description = (row.get("description") or row.get("summary") or "").strip()
            content = (row.get("content") or "").strip()
            if not skill_id or not name_raw or not description or not content:
                skipped_skills += 1
                continue

            metadata_json = row.get("metadata") or None
            outline = extract_outline(metadata_json)
            if not outline:
                try:
                    outline = generate_outline_with_llm(
                        title=name_raw,
                        content=content,
                        tags="",
                        prompt_path=outline_prompt_path,
                        llm_config=llm_config,
                        settings=settings,
                        cfg_path=cfg_path,
                    )
                    metadata_json = merge_metadata(metadata_json, {"chl.outline": outline})
                    outline_generated += 1
                except Exception as exc:
                    print(f"  ❌ Outline generation failed for skill {skill_id}: {exc}")
                    skipped_skills += 1
                    continue
            if not category_code:
                try:
                    category_code, confidence = map_category_with_llm(
                        title=name_raw,
                        content=content,
                        outline=outline,
                        category_list=category_list_text,
                        prompt_path=category_prompt_path,
                        llm_config=llm_config,
                        settings=settings,
                        cfg_path=cfg_path,
                    )
                    metadata_json = merge_metadata(
                        metadata_json,
                        {
                            "chl.category_code": category_code,
                            "chl.category_confidence": confidence,
                        },
                    )
                    category_mapped += 1
                except Exception as exc:
                    print(f"  ❌ Category mapping failed for skill {skill_id}: {exc}")
                    skipped_skills += 1
                    continue
            if outline and category_code:
                if (outline_generated + category_mapped) == 0:
                    normalized_skipped += 1

            if category_code not in category_codes:
                print(f"  ❌ Invalid category_code for skill {skill_id}: {category_code}")
                skipped_skills += 1
                continue
            name = slugify(name_raw)
            if not name:
                name = slugify(skill_id) or skill_id.lower()
            base_name = name
            counter = 2
            while name in used_names:
                name = f"{base_name}-{counter}"
                counter += 1
            used_names.add(name)
            skill = CategorySkill(
                id=skill_id,
                category_code=category_code,
                name=name,
                description=description,
                content=content,
                license=row.get("license") or None,
                compatibility=row.get("compatibility") or None,
                metadata_json=metadata_json,
                allowed_tools=row.get("allowed_tools") or None,
                model=row.get("model") or None,
                source=row.get("source") or "local",
                sync_status=0,  # Always set to 0 (PENDING) for curation regardless of source value
                author=row.get("author") or None,
                embedding_status="pending",  # Always mark as pending for curation
                created_at=parse_datetime(row.get("created_at")) or datetime.now(timezone.utc),
                updated_at=parse_datetime(row.get("updated_at")) or datetime.now(timezone.utc),
                synced_at=parse_datetime(row.get("synced_at")),
                exported_at=parse_datetime(row.get("exported_at")),
            )
            session.add(skill)

        session.commit()
        print(f"✓ Imported {len(skills_data) - skipped_skills} skills")
        if outline_generated:
            model_name = settings.model if settings else "LLM"
            print(f"  LLM outline generation: {model_name} for {outline_generated} skills")
        if category_mapped:
            model_name = settings.model if settings else "LLM"
            print(f"  LLM category mapping: {model_name} for {category_mapped} skills")
        if normalized_skipped:
            print(f"  LLM skipped: {normalized_skipped} skills already normalized")
        print(f"  All marked as embedding_status='pending'")
        if skipped_skills:
            print(f"  Skipped {skipped_skills} empty/invalid skill rows")
        print()

        print("✅ Import complete!")
        print()

    except Exception as e:
        session.rollback()
        print(f"❌ Error during import: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        session.close()


if __name__ == "__main__":
    main()
