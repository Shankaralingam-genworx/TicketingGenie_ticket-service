"""GCS upload, delete, and signed URL utilities."""

import uuid
from datetime import timedelta
from functools import lru_cache
from pathlib import PurePosixPath

from fastapi import UploadFile

from src.config.settings import settings
from src.schemas.attachment_schema import UploadValidationError, validate_upload
from src.schemas.ticket_schema import TicketResponse
from src.observability.logging.logger import get_logger

logger = get_logger(__name__).bind(service="gcs")

_STORAGE_SCOPES = ("https://www.googleapis.com/auth/devstorage.read_write",)

TICKET_PREFIX  = "tickets"
COMMENT_PREFIX = "comments"


# ── Client singleton ──────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _get_bucket():
    """Build GCS client once per process using ADC or impersonated credentials."""
    try:
        import google.auth
        from google.auth import impersonated_credentials
        from google.cloud import storage
    except ImportError as exc:
        raise RuntimeError(
            "Missing dependency 'google-cloud-storage'. "
            "Add google-cloud-storage to requirements.txt and redeploy."
        ) from exc

    source_credentials, detected_project = google.auth.default(scopes=list(_STORAGE_SCOPES))

    active_project = (settings.GCS_PROJECT_ID or "").strip() or detected_project
    if not active_project:
        raise RuntimeError(
            "Cannot determine the GCP project. Set GCS_PROJECT_ID in your environment."
        )

    target_sa = (settings.GCS_TARGET_SERVICE_ACCOUNT or "").strip()
    if target_sa:
        credentials = impersonated_credentials.Credentials(
            source_credentials = source_credentials,
            target_principal   = target_sa,
            target_scopes      = list(_STORAGE_SCOPES),
            lifetime           = 3600,
        )
        logger.info("gcs_using_impersonated_credentials", target_sa=target_sa)
    else:
        credentials = source_credentials
        logger.info("gcs_using_adc_credentials")

    client = storage.Client(project=active_project, credentials=credentials)
    bucket = client.bucket(settings.GCS_BUCKET_NAME)
    logger.info(
        "gcs_client_ready",
        project=active_project,
        bucket=settings.GCS_BUCKET_NAME,
        prefix=settings.GCS_BUCKET_PREFIX,
    )
    return bucket


# ── Path helpers ──────────────────────────────────────────────────────────────

def _object_name(area: str, filename: str) -> str:
    """Build a safe GCS object path, rejecting path-traversal attempts."""
    prefix = (settings.GCS_BUCKET_PREFIX or "").strip("/")
    parts  = [p for p in [prefix, area, filename] if p]
    joined = "/".join(parts)
    if any(part in (".", "..") for part in PurePosixPath(joined).parts):
        raise ValueError(f"Unsafe GCS object path: {joined}")
    return joined


def _public_url(object_name: str) -> str:
    return f"https://storage.googleapis.com/{settings.GCS_BUCKET_NAME}/{object_name}"


# ── Upload ────────────────────────────────────────────────────────────────────

async def upload_image(file: UploadFile, prefix: str = TICKET_PREFIX) -> dict:
    data       = await file.read()
    size_bytes = len(data)

    validate_upload(file.filename or "upload", file.content_type or "", size_bytes)

    original_name = file.filename or "upload"
    ext           = original_name.rsplit(".", 1)[-1].lower() if "." in original_name else "bin"
    stored_name   = f"{uuid.uuid4()}.{ext}"
    obj_name      = _object_name(prefix, stored_name)

    bucket = _get_bucket()
    blob   = bucket.blob(obj_name)
    blob.cache_control = "private, max-age=3600"
    blob.upload_from_string(data, content_type=file.content_type)

    logger.info(
        "gcs_upload_success",
        original_name=original_name,
        object_name=obj_name,
        size_bytes=size_bytes,
        content_type=file.content_type,
    )

    return {
        "original_name": original_name,
        "stored_name":   stored_name,
        "content_type":  file.content_type,
        "size_bytes":    size_bytes,
        "path":          obj_name,
        "url":           _public_url(obj_name),
    }


async def upload_images(
    files: list[UploadFile] | None,
    prefix: str = TICKET_PREFIX,
) -> list[dict]:
    if not files:
        return []

    results = []
    for file in files:
        try:
            results.append(await upload_image(file, prefix=prefix))
        except UploadValidationError as exc:
            # Invalid type/size — skip silently, don't abort the whole request
            logger.warning("gcs_upload_skipped", filename=file.filename, reason=str(exc))
        except Exception as exc:
            logger.error("gcs_upload_failed", filename=file.filename, error=str(exc), exc_info=True)
            raise

    return results


# ── Delete ────────────────────────────────────────────────────────────────────

def delete_object(object_path: str) -> None:
    """Delete a GCS object. Silently ignores objects that no longer exist."""
    try:
        _get_bucket().blob(object_path).delete()
        logger.info("gcs_delete_success", object_path=object_path)
    except Exception as exc:
        # Non-fatal — missing file on delete is acceptable
        logger.warning("gcs_delete_failed", object_path=object_path, error=str(exc))


# ── Signed URLs ───────────────────────────────────────────────────────────────

def generate_signed_url(object_path: str, expiry_minutes: int = 60) -> str:
    """Return a temporary signed URL for a private GCS object. Falls back to public URL on error."""
    try:
        blob = _get_bucket().blob(object_path)
        return blob.generate_signed_url(
            expiration = timedelta(minutes=expiry_minutes),
            method     = "GET",
            version    = "v4",
        )
    except Exception as exc:
        logger.warning("gcs_signed_url_failed", object_path=object_path, error=str(exc))
        return f"https://storage.googleapis.com/{settings.GCS_BUCKET_NAME}/{object_path}"


def _inject_signed_urls(response: TicketResponse) -> TicketResponse:
    """Replace stored GCS paths with 60-min signed URLs."""
    if not response.attachments:
        return response
    signed = [
        att.model_copy(update={"url": generate_signed_url(att.path, expiry_minutes=60)})
        for att in response.attachments
    ]
    return response.model_copy(update={"attachments": signed})