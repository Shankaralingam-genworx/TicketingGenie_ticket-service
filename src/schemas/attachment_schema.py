"""
Attachment schema with validation.
File: src/schemas/attachment_schema.py

AttachmentMeta         — stored in the DB JSON column, returned in API responses
validate_upload        — call this in the service layer before saving to disk
"""

from typing import Annotated
from pydantic import BaseModel, field_validator, model_validator, Field


# ── Constants (single source of truth) ───────────────────────────────────────

ALLOWED_MIME_TYPES: frozenset[str] = frozenset({
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
    "image/bmp",
})

MAX_FILE_SIZE_BYTES: int = 10 * 1024 * 1024   # 10 MB
MAX_FILENAME_LENGTH: int = 255


# ── Schema stored in DB + returned in API ────────────────────────────────────

class AttachmentMeta(BaseModel):
    """
    One entry per uploaded file, stored as a JSON list in tickets.attachments.

    Fields
    ------
    original_name : filename the user uploaded        e.g. "screenshot.png"
    stored_name   : uuid-renamed file on disk         e.g. "a1b2c3d4.png"
    content_type  : validated MIME type               e.g. "image/png"
    size_bytes    : file size — validated ≤ 10 MB     e.g. 204800
    path          : relative path on disk             e.g. "uploads/tickets/a1b2c3d4.png"
    url           : URL served by FastAPI / S3 later  e.g. "/uploads/tickets/a1b2c3d4.png"
    """

    original_name: str  = Field(..., max_length=MAX_FILENAME_LENGTH)
    stored_name:   str  = Field(..., min_length=1)
    content_type:  str
    size_bytes:    int  = Field(..., gt=0)
    path:          str  = Field(..., min_length=1)
    url:           str  = Field(..., min_length=1)

    model_config = {"from_attributes": True}

    @field_validator("content_type")
    @classmethod
    def must_be_allowed_mime(cls, v: str) -> str:
        if v not in ALLOWED_MIME_TYPES:
            raise ValueError(
                f"Unsupported file type '{v}'. "
                f"Allowed: {', '.join(sorted(ALLOWED_MIME_TYPES))}"
            )
        return v

    @field_validator("size_bytes")
    @classmethod
    def must_be_within_limit(cls, v: int) -> int:
        if v > MAX_FILE_SIZE_BYTES:
            raise ValueError(
                f"File size {v:,} bytes exceeds the {MAX_FILE_SIZE_BYTES // (1024*1024)} MB limit"
            )
        return v


# ── Upload validation helper (call in service before touching disk) ───────────

class UploadValidationError(ValueError):
    """Raised when an uploaded file fails validation before being saved."""
    pass


def validate_upload(
    filename:     str,
    content_type: str,
    size_bytes:   int,
) -> None:
    """
    Validate a file upload BEFORE writing to disk.
    Raises UploadValidationError with a user-friendly message on failure.

    Usage in service layer:
        data = await file.read()
        validate_upload(file.filename, file.content_type, len(data))
        # safe to write to disk now
    """
    if content_type not in ALLOWED_MIME_TYPES:
        raise UploadValidationError(
            f"'{filename}' has unsupported type '{content_type}'. "
            f"Only images are accepted: {', '.join(sorted(ALLOWED_MIME_TYPES))}."
        )

    if size_bytes > MAX_FILE_SIZE_BYTES:
        mb = size_bytes / (1024 * 1024)
        raise UploadValidationError(
            f"'{filename}' is {mb:.1f} MB — maximum allowed size is "
            f"{MAX_FILE_SIZE_BYTES // (1024 * 1024)} MB."
        )

    if size_bytes == 0:
        raise UploadValidationError(f"'{filename}' is empty.")

    if len(filename) > MAX_FILENAME_LENGTH:
        raise UploadValidationError(
            f"Filename too long ({len(filename)} chars, max {MAX_FILENAME_LENGTH})."
        )