"""Markdown rendering utilities for CHL web UI."""

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
import markdown
import logging
from pathlib import Path
import os

logger = logging.getLogger(__name__)

router = APIRouter()


def _templates_dir() -> str:
    # Base directory for API templates
    here = Path(__file__).resolve()
    return str(here.parents[2] / "api" / "templates")


templates = Jinja2Templates(directory=_templates_dir())


def find_project_root(current_path: Path) -> Path:
    """Traverse up the directory tree to find the project root (marked by pyproject.toml)."""
    for path in [current_path] + list(current_path.parents):
        if (path / "pyproject.toml").exists():
            return path
    raise RuntimeError("Project root (pyproject.toml) not found.")


try:
    PROJECT_ROOT = find_project_root(Path(__file__).resolve())
    DOCS_DIR = PROJECT_ROOT / "doc"
except RuntimeError as e:
    logger.error(f"Error finding project root: {e}")
    DOCS_DIR = Path(__file__).resolve().parent.parent.parent / "doc"

logger.info("Resolved DOCS_DIR: %s", DOCS_DIR)

DOC_MAPPING = {
    "concept": "concept",
    "architecture": "architecture",
    "manual": "manual",
}


@router.get("/docs/{doc_name}", response_class=HTMLResponse)
async def read_doc(request: Request, doc_name: str):
    mapped_doc_name = DOC_MAPPING.get(doc_name)
    if not mapped_doc_name:
        raise HTTPException(status_code=404, detail="Documentation not found")

    file_path = DOCS_DIR / f"{mapped_doc_name}.md"
    logger.info("Attempting to read file: %s", file_path)

    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Documentation file not found on server")

    with open(file_path, "r", encoding="utf-8") as f:
        md_content = f.read()

    html_content = markdown.markdown(md_content, extensions=["fenced_code", "tables"])

    return templates.TemplateResponse(
        "common/doc_viewer.html",
        {"request": request, "title": "", "content": html_content},
    )

