"""Pydantic models for API request/response schemas."""

from pydantic import BaseModel, Field, field_validator
from typing import Optional, List, Dict, Any
from datetime import datetime


def normalize_entity_type(value: str) -> str:
    """Normalize entity_type for current API usage."""
    if value == "manual":
        raise ValueError("entity_type 'manual' is deprecated; use 'skill'")
    return value


# Category models
class CategoryResponse(BaseModel):
    """Response model for a single category."""
    code: str
    name: str
    description: Optional[str] = None
    created_at: Optional[str] = None
    experience_count: Optional[int] = Field(None, description="Number of experiences in this category")
    skill_count: Optional[int] = Field(None, description="Number of skills in this category")
    total_count: Optional[int] = Field(None, description="Total entries (experiences + skills)")


class ListCategoriesResponse(BaseModel):
    """Response model for listing categories."""
    categories: List[CategoryResponse]


# Entry models
class ReadEntriesRequest(BaseModel):
    """Request model for reading entries."""
    entity_type: str = Field(..., description="'experience' or 'skill'")
    category_code: Optional[str] = Field(default=None, description="Category code to filter by (None for global search)")
    query: Optional[str] = None
    ids: Optional[List[str]] = None
    limit: Optional[int] = None
    # v1.1 additions (backward compatible)
    fields: Optional[List[str]] = Field(default=None, description="Field filter: 'preview' for snippets, specific fields for allowlist")
    snippet_len: Optional[int] = Field(default=None, ge=80, le=640, description="Snippet length if fields=['preview']")
    session_id: Optional[str] = Field(default=None, description="Session ID for tracking (prefer X-CHL-Session header)")

    @field_validator('entity_type')
    @classmethod
    def normalize_entity_type_field(cls, v: str) -> str:
        return normalize_entity_type(v)


class WriteEntryRequest(BaseModel):
    """Request model for creating an entry."""
    entity_type: str = Field(..., description="'experience' or 'skill'")
    category_code: str
    data: Dict[str, Any]

    @field_validator('entity_type')
    @classmethod
    def normalize_entity_type_field(cls, v: str) -> str:
        return normalize_entity_type(v)


class UpdateEntryRequest(BaseModel):
    """Request model for updating an entry."""
    entity_type: str = Field(..., description="'experience' or 'skill'")
    category_code: str
    entry_id: str
    updates: Dict[str, Any]
    force_contextual: bool = False

    @field_validator('entity_type')
    @classmethod
    def normalize_entity_type_field(cls, v: str) -> str:
        return normalize_entity_type(v)


class EntryResponse(BaseModel):
    """Response model for a single entry."""
    id: str
    entity_type: str
    category_code: str
    data: Dict[str, Any]
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class ReadEntriesResponse(BaseModel):
    """Response model for reading entries."""
    entries: List[Dict[str, Any]]
    count: int
    meta: Optional[Dict[str, Any]] = None


class WriteEntryResponse(BaseModel):
    """Response model for creating an entry."""
    success: bool
    entry_id: str
    # Full entry payload for read-after-write flows
    entry: Optional[Dict[str, Any]] = None
    # Potential duplicates surfaced at write-time with guidance
    duplicates: Optional[List[Dict[str, Any]]] = None
    recommendation: Optional[str] = None
    # Optional human-readable notes about processing.
    warnings: Optional[List[str]] = None
    message: Optional[str] = None


class UpdateEntryResponse(BaseModel):
    """Response model for updating an entry."""
    success: bool
    entry_id: str
    # Return the updated entry for better UX
    entry: Optional[Dict[str, Any]] = None
    message: Optional[str] = None


# Search models (unified search)
class DuplicateCheckRequest(BaseModel):
    """Request model for duplicate detection."""

    entity_type: str = Field(..., description="'experience' or 'skill'")
    category_code: Optional[str] = None
    title: str
    content: str
    limit: Optional[int] = 1
    threshold: Optional[float] = None

    @field_validator('entity_type')
    @classmethod
    def normalize_entity_type_field(cls, v: str) -> str:
        return normalize_entity_type(v)


class DuplicateCandidateResponse(BaseModel):
    """Single duplicate candidate returned by duplicate check."""

    entity_id: str
    entity_type: str
    score: float
    reason: str
    provider: str
    title: str
    summary: Optional[str] = None


class DuplicateCheckResponse(BaseModel):
    """Response model for duplicate detection."""

    candidates: List[DuplicateCandidateResponse]
    count: int


# Health check models
class HealthResponse(BaseModel):
    """Response model for health check."""
    status: str = Field(..., description="'healthy', 'degraded', or 'unhealthy'")
    components: Dict[str, Dict[str, Any]]
    timestamp: str


# Error models
class ErrorResponse(BaseModel):
    """Response model for errors."""
    error: str
    detail: Optional[str] = None
    error_code: Optional[str] = None


# Settings models
class SettingsSnapshotResponse(BaseModel):
    credentials: Optional[Dict[str, Any]]
    sheets: Optional[Dict[str, Any]]
    models: Optional[Dict[str, Any]]
    updated_at: Optional[str]
    mode: Optional[Dict[str, Any]] = None


# Operation models
class OperationRequest(BaseModel):
    payload: Optional[Dict[str, Any]] = None


class OperationResponse(BaseModel):
    job_id: str
    status: str


class JobStatusResponse(BaseModel):
    job_id: str
    job_type: str
    status: str
    requested_by: Optional[str]
    created_at: Optional[str]
    started_at: Optional[str]
    finished_at: Optional[str]
    cancelled_at: Optional[str]
    payload: Optional[Dict[str, Any]] = None
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


# Worker models
class WorkerStatusResponse(BaseModel):
    queue: Dict[str, Any]
    workers: Optional[Dict[str, Any]]


# Telemetry models
class TelemetrySnapshotResponse(BaseModel):
    queue: Optional[Dict[str, Any]]
    worker_pool: Optional[Dict[str, Any]]
    workers: List[Dict[str, Any]]
    jobs: List[Dict[str, Any]]
    meta: Optional[Dict[str, Any]] = None


# Unified search models (v1.1)
class UnifiedSearchRequest(BaseModel):
    """Request model for unified search API v1.1."""
    query: str = Field(..., description="Search query text")
    types: List[str] = Field(
        default=["experience", "skill"],
        description="Entity types to search: 'experience', 'skill'"
    )
    category: Optional[str] = Field(None, description="Filter to specific category code")
    limit: int = Field(10, ge=1, le=25, description="Maximum results to return (capped at 25)")
    offset: int = Field(0, ge=0, description="Pagination offset")
    min_score: Optional[float] = Field(None, ge=0.0, le=1.0, description="Minimum relevance score")
    filters: Optional[Dict[str, Any]] = Field(
        None,
        description="AND-based filters (exact match): author, section. Null values ignored."
    )
    snippet_len: int = Field(320, ge=80, le=640, description="Snippet length in characters")
    fields: Optional[List[str]] = Field(
        None,
        description="Additional fields to include (additive, e.g. ['playbook'] for full body)"
    )
    hide_viewed: bool = Field(False, description="Remove previously viewed entries")
    downrank_viewed: bool = Field(True, description="Apply score penalty (0.5x) to viewed entries")
    session_id: Optional[str] = Field(None, description="Session ID for tracking (prefer X-CHL-Session header)")

    @field_validator('types')
    @classmethod
    def normalize_types(cls, v: List[str]) -> List[str]:
        return [normalize_entity_type(item) for item in v]


class UnifiedSearchResult(BaseModel):
    """Single result from unified search API v1.1."""
    entity_id: str
    entity_type: str  # 'experience' or 'skill'
    title: str
    section: Optional[str] = None
    score: float = Field(..., description="Relevance score (always present for search results)")
    rank: int = Field(..., description="Position in merged result list (0-indexed)")
    reason: str = Field(..., description="How this result was found (semantic_match, text_match, etc)")
    provider: str = Field(..., description="Provider that returned this result")
    degraded: bool = Field(False, description="Whether provider is in fallback/degraded mode")
    hint: Optional[str] = Field(None, description="Provider hint for degraded results")
    heading: Optional[str] = Field(None, description="Display heading (title or extracted)")
    snippet: Optional[str] = Field(None, description="Content snippet")
    author: Optional[str] = None
    updated_at: Optional[str] = None


class UnifiedSearchResponse(BaseModel):
    """Response model for unified search API v1.1."""
    results: List[UnifiedSearchResult]
    count: int = Field(..., description="Number of results in this response")
    total: Optional[int] = Field(
        None,
        description="Total matching results (expensive to compute; may be None)"
    )
    has_more: bool = Field(..., description="Whether more results exist beyond this page")
    top_score: Optional[float] = Field(None, description="Highest score in results")
    warnings: List[str] = Field(default_factory=list, description="Warnings (e.g., low scores, fallback mode)")
    session_applied: bool = Field(False, description="Whether session filtering was applied")
