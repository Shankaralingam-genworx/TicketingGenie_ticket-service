"""Pydantic schemas for severity keyword admin API."""

from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from src.constants.sla_constants import Severity


class SeverityKeywordCreate(BaseModel):
    keyword:  str      = Field(..., min_length=1, max_length=200,
                               description="Keyword or phrase (stored lowercase)")
    severity: Severity = Field(..., description="Severity bucket: critical | high | medium | low")
    weight:   float    = Field(default=1.0, ge=0.1, le=5.0,
                               description="Scoring weight — higher = stronger signal")

    @field_validator("keyword")
    @classmethod
    def normalise(cls, v: str) -> str:
        return v.lower().strip()


class SeverityKeywordUpdate(BaseModel):
    keyword:   str | None      = Field(default=None, min_length=1, max_length=200)
    severity:  Severity | None = None
    weight:    float | None    = Field(default=None, ge=0.1, le=5.0)
    is_active: bool | None     = None

    @field_validator("keyword")
    @classmethod
    def normalise(cls, v: str | None) -> str | None:
        return v.lower().strip() if v else v


class SeverityKeywordResponse(BaseModel):
    id:         int
    keyword:    str
    severity:   Severity
    weight:     float
    is_active:  bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class SeverityKeywordBulkCreate(BaseModel):
    """Create multiple keywords in one request."""
    keywords: list[SeverityKeywordCreate] = Field(..., min_length=1, max_length=500)


class SeverityKeywordBulkResponse(BaseModel):
    inserted: int
    message:  str
