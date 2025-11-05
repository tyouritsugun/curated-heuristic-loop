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


class DeleteEntryRequest(BaseModel):
    """Request model for deleting an entry."""
    entity_type: str = Field(..., description="'experience' or 'manual'")
    category_code: str
    entry_id: str


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
    message: Optional[str] = None


class UpdateEntryResponse(BaseModel):
    """Response model for updating an entry."""
    success: bool
    entry_id: str
    message: Optional[str] = None


class DeleteEntryResponse(BaseModel):
    """Response model for deleting an entry."""
    success: bool
    entry_id: str
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
