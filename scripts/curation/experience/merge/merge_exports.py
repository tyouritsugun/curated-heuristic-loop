#!/usr/bin/env python3
"""
Merge multiple member CSV exports into a single dataset.

This script:
1. Reads member exports from data/curation/members/{username}/ directories
2. Merges categories (validates uniqueness by code)
3. Merges experiences (handles ID collisions by appending _{username} suffix)
4. Merges skills (handles ID collisions by appending _{username} suffix)
5. Outputs merged CSVs to data/curation/merged/
6. Logs merge audit trail to data/curation/merge_audit.csv

Usage:
    # With explicit paths:
    python scripts/curation/experience/merge/merge_exports.py \\
        --inputs data/curation/members/alice data/curation/members/bob \\
        --output data/curation/merged

    # With defaults from scripts_config.yaml:
    python scripts/curation/experience/merge/merge_exports.py
"""

import argparse
import csv
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Set, Tuple

# Add project root to sys.path for config loading
repo_root = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(repo_root))

from scripts._config_loader import load_scripts_config


def parse_args():
    # Load config to get defaults
    try:
        config, _ = load_scripts_config()
        curation_config = config.get("curation", {})
        default_members_dir = curation_config.get("members_dir", "data/curation/members")
        default_output_dir = curation_config.get("merged_output_dir", "data/curation/merged")
    except Exception:
        # Fallback to hard-coded defaults if config loading fails
        default_members_dir = "data/curation/members"
        default_output_dir = "data/curation/merged"

    parser = argparse.ArgumentParser(
        description="Merge member CSV exports for team curation workflow"
    )
    parser.add_argument(
        "--inputs",
        nargs="*",
        help="List of member directories (default: scan default members directory)",
        default=None,
    )
    parser.add_argument(
        "--output",
        help=f"Output directory for merged CSVs (default: {default_output_dir})",
        default=default_output_dir,
    )

    args = parser.parse_args()

    # If no inputs specified, scan the default members directory for subdirectories
    # that contain the expected CSV files (valid member export directories)
    if args.inputs is None:
        members_path = Path(default_members_dir)
        if members_path.exists():
            valid_members = []
            for sub in members_path.iterdir():
                if sub.is_dir():
                    # Check if this subdirectory has the expected CSV files (lowercase or capitalized)
                    lowercase_base = all((sub / csv_file).exists() for csv_file in ["categories.csv", "experiences.csv"])
                    capitalized_base = all((sub / csv_file).exists() for csv_file in ["Categories.csv", "Experiences.csv"])
                    lowercase_skills = (sub / "skills.csv").exists() or (sub / "manuals.csv").exists()
                    capitalized_skills = (sub / "Skills.csv").exists() or (sub / "Manuals.csv").exists()

                    # Check if all base files exist and a skills/manuals file is present
                    lowercase_exist = lowercase_base and lowercase_skills
                    capitalized_exist = capitalized_base and capitalized_skills

                    if lowercase_exist or capitalized_exist:
                        valid_members.append(str(sub))

            if valid_members:
                args.inputs = valid_members
            else:
                parser.error(
                    f"No valid member directories found in {default_members_dir} "
                    "(directories must contain categories.csv, experiences.csv, and skills.csv). "
                    "Please specify --inputs explicitly."
                )
        else:
            parser.error(f"Default members directory {default_members_dir} does not exist. Please specify --inputs explicitly.")

    return args


MIN_CATEGORY_COLUMNS = {"code", "name", "description"}
MIN_EXPERIENCE_COLUMNS = {"id", "category_code", "section", "title", "playbook"}
MIN_SKILL_COLUMNS = {"id", "category_code", "title", "content"}

OPTIONAL_EXPERIENCE_COLUMNS = {"context", "expected_action"}
OPTIONAL_SKILL_COLUMNS = {"summary"}


def default_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


CATEGORY_DEFAULTS = {
    "created_at": default_timestamp,
}
EXPERIENCE_DEFAULTS = {
    "context": "",
    "source": "local",
    "sync_status": "0",
    "author": "",
    "embedding_status": "pending",
    "created_at": default_timestamp,
    "updated_at": default_timestamp,
    "synced_at": "",
    "exported_at": "",
    "expected_action": "",
}
SKILL_DEFAULTS = {
    "summary": "",
    "source": "local",
    "sync_status": "0",
    "author": "",
    "embedding_status": "pending",
    "created_at": default_timestamp,
    "updated_at": default_timestamp,
    "synced_at": "",
    "exported_at": "",
}


def read_csv(file_path: Path) -> Tuple[List[Dict], List[str]]:
    """Read CSV file and return list of dicts + fieldnames."""
    # Try lowercase first, then capitalized (handle both cases)
    if file_path.exists():
        pass
    elif file_path.with_name(file_path.name.capitalize()).exists():
        file_path = file_path.with_name(file_path.name.capitalize())
    else:
        return [], []

    with open(file_path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        return list(reader), (reader.fieldnames or [])


def validate_columns(
    fieldnames: List[str],
    required: Set[str],
    entity_label: str,
    username: str,
    file_path: Path,
) -> List[str]:
    if not fieldnames:
        return [f"{entity_label}: {file_path} has no header row for user {username}"]

    actual = set(fieldnames)
    missing = required - actual
    errors = []
    if missing:
        errors.append(f"{entity_label}: Missing columns for user {username}: {sorted(missing)}")
    return errors


def normalize_rows(
    rows: List[Dict],
    defaults: Dict[str, object],
) -> List[Dict]:
    normalized = []
    for row in rows:
        updated = dict(row)
        for key, value in defaults.items():
            if updated.get(key) in (None, ""):
                updated[key] = value() if callable(value) else value
        normalized.append(updated)
    return normalized


def write_csv(file_path: Path, rows: List[Dict], fieldnames: List[str]):
    """Write list of dicts to CSV file."""
    file_path.parent.mkdir(parents=True, exist_ok=True)

    # Normalize rows: ensure all fieldnames exist (fill missing with empty string)
    normalized_rows = []
    for row in rows:
        normalized_row = {field: row.get(field, "") for field in fieldnames}
        normalized_rows.append(normalized_row)

    with open(file_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(normalized_rows)


def merge_categories(member_data: Dict[str, List[Dict]]) -> Tuple[List[Dict], List[str]]:
    """
    Merge categories from multiple members.

    Uses category code as unique key. Validates that all members with the same
    code have matching name and description.

    Returns:
        (merged_categories, warnings)
    """
    categories_by_code: Dict[str, Dict] = {}
    warnings = []

    for username, categories in member_data.items():
        for cat in categories:
            code = cat["code"]

            if code in categories_by_code:
                # Validate that name and description match
                existing = categories_by_code[code]
                if existing["name"] != cat["name"]:
                    warnings.append(
                        f"Category code '{code}': name mismatch between users "
                        f"('{existing['name']}' vs '{cat['name']}')"
                    )
                if existing["description"] != cat["description"]:
                    warnings.append(
                        f"Category code '{code}': description mismatch between users"
                    )
            else:
                # First occurrence of this code
                categories_by_code[code] = cat

    # Return sorted by code
    merged = sorted(categories_by_code.values(), key=lambda x: x["code"])
    return merged, warnings


def merge_experiences(
    member_data: Dict[str, List[Dict]]
) -> Tuple[List[Dict], List[str], List[str]]:
    """
    Merge experiences from multiple members.

    Uses experience ID as unique key. On collision, appends _{username} suffix
    to the colliding ID and logs to audit trail.

    Returns:
        (merged_experiences, collision_ids, warnings)
    """
    experiences_by_id: Dict[str, Dict] = {}
    collision_ids = []
    warnings = []

    for username, experiences in member_data.items():
        for exp in experiences:
            original_id = exp["id"]

            if original_id in experiences_by_id:
                # Collision detected - append username suffix
                new_id = f"{original_id}_{username}"
                exp["id"] = new_id
                collision_ids.append(new_id)
                warnings.append(
                    f"Experience ID collision: '{original_id}' from {username} "
                    f"renamed to '{new_id}'"
                )

            # Override author field with folder name (source of truth)
            exp["author"] = username

            experiences_by_id[exp["id"]] = exp

    # Return sorted by id
    merged = sorted(experiences_by_id.values(), key=lambda x: x["id"])
    return merged, collision_ids, warnings


def merge_skills(
    member_data: Dict[str, List[Dict]]
) -> Tuple[List[Dict], List[str], List[str]]:
    """
    Merge skills from multiple members.

    Uses skill ID as unique key. On collision, appends _{username} suffix
    to the colliding ID and logs to audit trail.

    Returns:
        (merged_skills, collision_ids, warnings)
    """
    skills_by_id: Dict[str, Dict] = {}
    collision_ids = []
    warnings = []

    for username, skills in member_data.items():
        for skill in skills:
            original_id = skill["id"]

            if original_id in skills_by_id:
                # Collision detected - append username suffix
                new_id = f"{original_id}_{username}"
                skill["id"] = new_id
                collision_ids.append(new_id)
                warnings.append(
                    f"Skill ID collision: '{original_id}' from {username} "
                    f"renamed to '{new_id}'"
                )

            # Override author field with folder name (source of truth)
            skill["author"] = username

            skills_by_id[skill["id"]] = skill

    # Return sorted by id
    merged = sorted(skills_by_id.values(), key=lambda x: x["id"])
    return merged, collision_ids, warnings


def write_audit_log(
    output_dir: Path,
    usernames: List[str],
    experience_collisions: List[str],
    skill_collisions: List[str],
    warnings: List[str],
):
    """Write merge audit log to CSV."""
    audit_file = output_dir.parent / "merge_audit.csv"
    audit_file.parent.mkdir(parents=True, exist_ok=True)

    run_id = f"mer-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    timestamp = datetime.now(timezone.utc).isoformat()

    # Append to audit log (or create if doesn't exist)
    file_exists = audit_file.exists()

    with open(audit_file, "a", encoding="utf-8", newline="") as f:
        fieldnames = [
            "run_id",
            "timestamp",
            "users",
            "input_files",
            "output_dir",
            "experience_collisions",
            "skill_collisions",
            "collision_count",
            "warnings",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)

        if not file_exists:
            writer.writeheader()

        all_collisions = experience_collisions + skill_collisions

        writer.writerow({
            "run_id": run_id,
            "timestamp": timestamp,
            "users": ",".join(usernames),
            "input_files": ",".join(usernames),
            "output_dir": str(output_dir),
            "experience_collisions": ",".join(experience_collisions),
            "skill_collisions": ",".join(skill_collisions),
            "collision_count": len(all_collisions),
            "warnings": "; ".join(warnings) if warnings else "",
        })

    print(f"✓ Audit log written to: {audit_file}")


def main():
    args = parse_args()

    # Validate inputs
    input_dirs = [Path(p) for p in args.inputs]
    for d in input_dirs:
        if not d.exists():
            print(f"❌ Error: Input directory does not exist: {d}", file=sys.stderr)
            sys.exit(1)

    output_dir = Path(args.output)

    print(f"Merging {len(input_dirs)} member exports...")
    print(f"Inputs: {', '.join(d.name for d in input_dirs)}")
    print()

    # Read all member data
    categories_data = {}
    experiences_data = {}
    skills_data = {}
    schema_errors: List[str] = []

    for input_dir in input_dirs:
        username = input_dir.name

        categories, category_fields = read_csv(input_dir / "categories.csv")
        experiences, experience_fields = read_csv(input_dir / "experiences.csv")

        # Try to find skills file (try new name first, then legacy names)
        skills_path = input_dir / "skills.csv"
        if not skills_path.exists():
            skills_path = input_dir / "Skills.csv"
        if not skills_path.exists():
            skills_path = input_dir / "manuals.csv"
        if not skills_path.exists():
            skills_path = input_dir / "Manuals.csv"

        skills, skill_fields = read_csv(skills_path)

        schema_errors.extend(
            validate_columns(
                category_fields,
                MIN_CATEGORY_COLUMNS,
                "Categories",
                username,
                input_dir / "categories.csv",
            )
        )
        schema_errors.extend(
            validate_columns(
                experience_fields,
                MIN_EXPERIENCE_COLUMNS,
                "Experiences",
                username,
                input_dir / "experiences.csv",
            )
        )
        schema_errors.extend(
            validate_columns(
                skill_fields,
                MIN_SKILL_COLUMNS,
                "Skills",
                username,
                skills_path,
            )
        )

        categories_data[username] = normalize_rows(categories, CATEGORY_DEFAULTS)
        experiences_data[username] = normalize_rows(experiences, EXPERIENCE_DEFAULTS)
        skills_data[username] = normalize_rows(skills, SKILL_DEFAULTS)

        print(f"  {username}: {len(categories)} categories, {len(experiences)} experiences, {len(skills)} skills")

    print()

    if schema_errors:
        print("❌ Schema validation failed:")
        for err in schema_errors:
            print(f"  - {err}")
        sys.exit(1)

    # Merge categories
    merged_categories, cat_warnings = merge_categories(categories_data)

    if cat_warnings:
        print("⚠️  Category warnings:")
        for warning in cat_warnings:
            print(f"  - {warning}")
        print()
        print("❌ Error: Category conflicts detected. Team must align on category definitions.")
        print("   All members should use the same category codes with identical names and descriptions.")
        sys.exit(1)

    print(f"✓ Categories: {len(merged_categories)} unique")
    for cat in merged_categories:
        print(f"  - {cat['code']}: {cat['name']}")
    print()

    # Merge experiences
    merged_experiences, exp_collisions, exp_warnings = merge_experiences(experiences_data)

    total_exp_count = sum(len(exps) for exps in experiences_data.values())
    print(f"✓ Experiences: {len(merged_experiences)} total ({total_exp_count} from members)")
    if exp_collisions:
        print(f"  - {len(exp_collisions)} ID collisions detected and resolved (suffix appended)")
        for collision_id in exp_collisions:
            print(f"    • {collision_id}")
    print()

    # Merge skills
    merged_skills, skill_collisions, skill_warnings = merge_skills(skills_data)

    total_skill_count = sum(len(skills) for skills in skills_data.values())
    print(f"✓ Skills: {len(merged_skills)} total ({total_skill_count} from members)")
    if skill_collisions:
        print(f"  - {len(skill_collisions)} ID collisions detected and resolved (suffix appended)")
        for collision_id in skill_collisions:
            print(f"    • {collision_id}")
    print()

    # Write merged CSVs
    output_dir.mkdir(parents=True, exist_ok=True)

    if merged_categories:
        write_csv(
            output_dir / "categories.csv",
            merged_categories,
            ["code", "name", "description", "created_at"],
        )

    if merged_experiences:
        write_csv(
            output_dir / "experiences.csv",
            merged_experiences,
            [
                "id",
                "category_code",
                "section",
                "title",
                "playbook",
                "context",
                "source",
                "sync_status",
                "author",
                "embedding_status",
                "created_at",
                "updated_at",
                "synced_at",
                "exported_at",
                "expected_action",
            ],
        )

    if merged_skills:
        write_csv(
            output_dir / "skills.csv",
            merged_skills,
            [
                "id",
                "category_code",
                "title",
                "content",
                "summary",
                "source",
                "sync_status",
                "author",
                "embedding_status",
                "created_at",
                "updated_at",
                "synced_at",
                "exported_at",
            ],
        )

    print(f"✓ Output written to: {output_dir}/")
    print(f"  - categories.csv ({len(merged_categories)} rows)")
    print(f"  - experiences.csv ({len(merged_experiences)} rows)")
    print(f"  - skills.csv ({len(merged_skills)} rows)")
    print()

    # Write audit log
    all_warnings = exp_warnings + skill_warnings
    usernames = [d.name for d in input_dirs]
    write_audit_log(
        output_dir,
        usernames,
        exp_collisions,
        skill_collisions,
        all_warnings,
    )

    print("✅ Merge complete!")


if __name__ == "__main__":
    main()
