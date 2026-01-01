"""Canonical category taxonomy for CHL.

This module is the single source of truth for category definitions.
Categories are seeded into the database during setup and validated during import.
"""
from __future__ import annotations

from typing import List, Optional, TypedDict


class CategoryDefinition(TypedDict):
    code: str
    name: str
    description: str


CATEGORIES: List[CategoryDefinition] = [
    # Existing baseline categories (keep unchanged)
    {"code": "FPD", "name": "figma_page_design", "description": "Figma page layout and UI composition."},
    {"code": "DSD", "name": "database_schema_design", "description": "Relational schema design and data modeling."},
    {"code": "PGS", "name": "page_specification", "description": "UI spec writing and page requirements."},
    {"code": "TMG", "name": "ticket_management", "description": "Ticket writing, triage, and workflow."},
    {"code": "ADG", "name": "architecture_design", "description": "System architecture planning and diagrams."},
    {"code": "MGC", "name": "migration_code", "description": "Code and database migration planning."},
    {"code": "FTH", "name": "frontend_html", "description": "HTML/CSS implementation details."},
    {"code": "LPW", "name": "laravel_php_web", "description": "Laravel/PHP web development patterns."},
    {"code": "PGT", "name": "python_agent", "description": "Python agent design and orchestration."},
    {"code": "PPT", "name": "playwright_page_test", "description": "Playwright-based UI testing."},
    {"code": "EET", "name": "e2e_test", "description": "End-to-end testing workflows."},
    {"code": "PRQ", "name": "pull_request", "description": "PR creation, review, and merge practices."},
    # Product & planning
    {"code": "REQ", "name": "requirements_specification", "description": "Functional/non-functional requirements capture."},
    {"code": "RMP", "name": "roadmap_planning", "description": "Milestones, sequencing, release planning."},
    # UX & design
    {"code": "DSS", "name": "design_systems", "description": "Component libraries, tokens, style guides."},
    {"code": "ACC", "name": "accessibility", "description": "WCAG, keyboard navigation, screen readers."},
    {"code": "FGD", "name": "figma_design", "description": "Figma workflows, prototyping, handoff."},
    # Frontend engineering
    {"code": "FRA", "name": "frontend_architecture", "description": "State, routing, build structure."},
    {"code": "WPF", "name": "web_performance", "description": "Core Web Vitals, perf budgets."},
    # Backend & data
    {"code": "API", "name": "api_design", "description": "REST/GraphQL design and versioning."},
    {"code": "BEA", "name": "backend_architecture", "description": "Service boundaries and patterns."},
    {"code": "DBM", "name": "database_modeling", "description": "Schema, indexing, query design."},
    # Infrastructure & operations
    {"code": "DEP", "name": "deployment_release", "description": "CI/CD, rollout, rollback."},
    {"code": "OBS", "name": "observability", "description": "Logging, metrics, tracing."},
    {"code": "SRE", "name": "reliability_sre", "description": "SLOs and incident response."},
    {"code": "SEC", "name": "security_review", "description": "Threat modeling and security review."},
    # Testing & quality
    {"code": "TST", "name": "testing", "description": "Unit/integration/QA/test infrastructure."},
    # Engineering process
    {"code": "CRV", "name": "code_review", "description": "PR workflow and feedback practice."},
    {"code": "DOC", "name": "documentation", "description": "READMEs, runbooks, knowledge base."},
    {"code": "RFG", "name": "refactoring", "description": "Tech debt and code cleanup."},
    {"code": "TRS", "name": "technical_research", "description": "Spikes, POCs, vendor evaluation."},
    # AI/agent workflows
    {"code": "PRM", "name": "prompting_workflows", "description": "Prompting patterns and best practices."},
    {"code": "LLM", "name": "llm_tooling", "description": "RAG, fine-tuning, eval tooling."},
    {"code": "AEV", "name": "agent_evaluation", "description": "Agent testing and metrics."},
    # External tool workflows
    {"code": "TKT", "name": "ticket_edit", "description": "Jira/Linear/GitHub issue edits."},
    {"code": "GHF", "name": "github_flow", "description": "Branch/PR flow and GitHub Actions."},
]


def get_category_by_code(code: str) -> Optional[CategoryDefinition]:
    """Lookup a category definition by code."""
    code = code.strip().upper()
    return next((c for c in CATEGORIES if c["code"] == code), None)


def get_all_codes() -> List[str]:
    """Return all valid category codes."""
    return [c["code"] for c in CATEGORIES]


def get_categories() -> List[CategoryDefinition]:
    """Return a copy of the canonical category list."""
    return list(CATEGORIES)


__all__ = ["CategoryDefinition", "CATEGORIES", "get_category_by_code", "get_all_codes", "get_categories"]
