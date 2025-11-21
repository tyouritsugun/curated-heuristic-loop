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

    # Convert markdown to HTML with proper extensions
    # Use 'extra' which includes fenced_code_blocks, or use fenced_code_blocks directly
    # The extensions should preserve code blocks with language classes
    try:
        md = markdown.Markdown(
            extensions=["extra", "tables"],
            output_format='html'
        )
        html_content = md.convert(md_content)
    except Exception as e:
        logger.error(f"Error converting markdown with 'extra': {e}")
        # Fallback to basic fenced_code
        md = markdown.Markdown(
            extensions=["fenced_code", "tables"],
            output_format='html'
        )
        html_content = md.convert(md_content)

    # Debug: Log a snippet of the HTML to check code block structure
    import re
    mermaid_blocks = re.findall(r'<pre>.*?</pre>', html_content, re.DOTALL)
    if mermaid_blocks:
        logger.info(f"Found {len(mermaid_blocks)} <pre> blocks")
        if mermaid_blocks:
            snippet = mermaid_blocks[0][:200] if len(mermaid_blocks[0]) > 200 else mermaid_blocks[0]
            logger.info(f"First <pre> block: {snippet}")

    return templates.TemplateResponse(
        "common/doc_viewer.html",
        {"request": request, "title": "", "content": html_content},
    )

