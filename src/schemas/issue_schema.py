"""Issue Pydantic schemas."""

from datetime import datetime

from pydantic import BaseModel, Field

from src.constants.issue_constants import IssueCategory


class IssueCreateRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=120)
    category: IssueCategory
    description: str | None = None
    is_active: bool = True


class IssueUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    category: IssueCategory | None = None
    description: str | None = None
    is_active: bool | None = None


class IssueResponse(BaseModel):
    id: int
    name: str
    category: IssueCategory
    description: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
