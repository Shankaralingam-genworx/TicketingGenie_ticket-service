"""SLA Pydantic schemas.
File: src/schemas/sla_schema.py
"""

from datetime import datetime
from pydantic import BaseModel, Field
from src.constants.sla_constants import CustomerTier, Severity


class SLACreateRequest(BaseModel):
    name:                      str   = Field(..., min_length=2, max_length=120)
    customer_tier:             CustomerTier
    severity:                  Severity
    response_time_mins:        float = Field(..., gt=0)
    resolution_time_mins:      float = Field(..., gt=0)
    # Escalation windows — optional, default 0 means "same as normal SLA"
    additional_response_mins:   float = Field(default=0, ge=0)
    additional_resolution_mins: float = Field(default=0, ge=0)
    is_active:                 bool  = True


class SLAUpdateRequest(BaseModel):
    name:                      str | None   = None
    response_time_mins:        float | None = Field(default=None, gt=0)
    resolution_time_mins:      float | None = Field(default=None, gt=0)
    additional_response_mins:   float | None = Field(default=None, ge=0)
    additional_resolution_mins: float | None = Field(default=None, ge=0)
    is_active:                 bool | None  = None


class SLAResponse(BaseModel):
    id:                        int
    name:                      str
    customer_tier:             CustomerTier
    severity:                  Severity
    response_time_mins:        float
    resolution_time_mins:      float
    additional_response_mins:   float
    additional_resolution_mins: float
    is_active:                 bool
    created_at:                datetime
    updated_at:                datetime

    model_config = {"from_attributes": True}