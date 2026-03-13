from pydantic import BaseModel, Field
from datetime import datetime

class EmailConfigCreate(BaseModel):
    name:               str = Field(..., min_length=2, max_length=120)
    password:           str = Field(..., min_length=1, description="App password")
    smtp_from_name:     str = Field(default="Support Team", max_length=120)
    imap_folder:        str = Field(default="INBOX", max_length=100)
    poll_interval_secs: int = Field(default=60, ge=10, le=3600)


class EmailConfigUpdate(BaseModel):
    name:               str | None = None
    password:           str | None = None
    smtp_from_name:     str | None = None
    imap_folder:        str | None = None
    poll_interval_secs: int | None = Field(default=None, ge=10, le=3600)
    is_active:          bool | None = None


class EmailConfigResponse(BaseModel):
    """Password is never returned."""
    id:                 int
    name:               str
    smtp_from_name:     str
    imap_folder:        str
    poll_interval_secs: int
    is_active:          bool
    created_at:         datetime
    updated_at:         datetime

    model_config = {"from_attributes": True}
