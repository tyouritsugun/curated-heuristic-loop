"""Pydantic models for API request/response schemas."""

from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime


# Category models
class CategoryResponse(BaseModel):
    """Response model for a single category."""
    code: str
    name: str
    description: Optional[str] = None
    created_at: Optional[str] = None


class ListCategoriesResponse(BaseModel):
    """Response model for listing categories."""
    categories: List[CategoryResponse]


# Entry models
class ReadEntriesRequest(BaseModel):
    """Request model for reading entries."""
    entity_type: str = Field(..., description="'experience' or 'manual'")
    category_code: str
    query: Optional[str] = None
    ids: Optional[List[str]] = None
    limit: Optional[int] = None


class WriteEntryRequest(BaseModel):
    """Request model for creating an entry."""
    entity_type: str = Field(..., description="'experience' or 'manual'")
    category_code: str
    data: Dict[str, Any]


class UpdateEntryRequest(BaseModel):
    """Request model for updating an entry."""
    entity_type: str = Field(..., description="'experience' or 'manual'")
    category_code: str
    entry_id: str
    updates: Dict[str, Any]
    force_contextual: bool = False


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


class WriteEntryResponse(BaseModel):
    """Response model for creating an entry."""
    success: bool
    entry_id: str
    # Full entry payload for read-after-write flows
    entry: Optional[Dict[str, Any]] = None
    # Potential duplicates surfaced at write-time with guidance
    duplicates: Optional[List[Dict[str, Any]]] = None
    recommendation: Optional[str] = None
    # Optional human-readable notes (e.g., context ignored for non-contextual sections)
    warnings: Optional[List[str]] = None
    message: Optional[str] = None


class UpdateEntryResponse(BaseModel):
    """Response model for updating an entry."""
    success: bool
    entry_id: str
    # Return the updated entry for better UX
    entry: Optional[Dict[str, Any]] = None
    message: Optional[str] = None


# Search models
class SearchRequest(BaseModel):
    """Request model for search."""
    entity_type: str = Field(..., description="'experience' or 'manual'")
    category_code: str
    query: str
    limit: Optional[int] = 10


class SearchResponse(BaseModel):
    """Response model for search."""
    results: List[Dict[str, Any]]
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
