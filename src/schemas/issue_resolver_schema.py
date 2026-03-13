"""IssueResolver Pydantic schemas."""

from datetime import datetime

from pydantic import BaseModel, Field

from src.schemas.issue_schema import IssueResponse


class IssueResolverCreateRequest(BaseModel):
    issue_id: int
    team_id: int
    team_name: str | None = Field(default=None, max_length=120)


class IssueResolverResponse(BaseModel):
    id: int
    issue_id: int
    team_id: int
    team_name: str | None
    is_active: bool
    created_at: datetime
    issue: IssueResponse | None = None

    model_config = {"from_attributes": True}
