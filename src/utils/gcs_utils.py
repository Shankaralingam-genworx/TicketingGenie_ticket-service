
import logging
import uuid
from functools import lru_cache
from pathlib import PurePosixPath
from datetime import timedelta

from fastapi import UploadFile

from src.config.settings import settings
from src.schemas.attachment_schema import UploadValidationError, validate_upload
from src.schemas.ticket_schema import TicketResponse


logger = logging.getLogger("ticket.gcs")

_STORAGE_SCOPES = ("https://www.googleapis.com/auth/devstorage.read_write",)

# Sub-folder names inside the bucket (after the optional prefix)
TICKET_PREFIX  = "tickets"
COMMENT_PREFIX = "comments"


# ── Client / bucket (module-level singleton, built once per process) ──────────

@lru_cache(maxsize=1)
def _get_bucket():
    try:
        import google.auth
        from google.auth import impersonated_credentials
        from google.cloud import storage
    except ImportError as exc:
        raise RuntimeError(
            "Missing dependency 'google-cloud-storage'. "
            "Add google-cloud-storage to requirements.txt and redeploy."
        ) from exc

    # Step 1 — obtain the base ADC credentials (Cloud Run identity / Workload Identity)
    source_credentials, detected_project = google.auth.default(scopes=list(_STORAGE_SCOPES))

    # Step 2 — resolve the active GCP project
    active_project = (settings.GCS_PROJECT_ID or "").strip() or detected_project
    if not active_project:
        raise RuntimeError(
            "Cannot determine the GCP project. "
            "Set GCS_PROJECT_ID in your environment."
        )

    # Step 3 — optionally impersonate the company-provided service account
    target_sa = (settings.GCS_TARGET_SERVICE_ACCOUNT or "").strip()
    if target_sa:
        credentials = impersonated_credentials.Credentials(
            source_credentials=source_credentials,
            target_principal=target_sa,
            target_scopes=list(_STORAGE_SCOPES),
            lifetime=3600,
        )
        logger.info(f"[GCS] Using impersonated credentials → {target_sa}")
    else:
        credentials = source_credentials
        logger.info("[GCS] Using ADC credentials directly (no impersonation).")

    client = storage.Client(project=active_project, credentials=credentials)
    bucket = client.bucket(settings.GCS_BUCKET_NAME)
    logger.info(
        f"[GCS] Client ready | project={active_project} "
        f"bucket={settings.GCS_BUCKET_NAME} prefix='{settings.GCS_BUCKET_PREFIX}'"
    )
    return bucket


# ── Object path helpers ───────────────────────────────────────────────────────

def _object_name(area: str, filename: str) -> str:

    prefix = (settings.GCS_BUCKET_PREFIX or "").strip("/")
    parts = [p for p in [prefix, area, filename] if p]
    # Validate — no path-traversal, no empty segments
    joined = "/".join(parts)
    path = PurePosixPath(joined)
    if any(part in (".", "..") for part in path.parts):
        raise ValueError(f"Unsafe GCS object path: {joined}")
    return joined


def _public_url(object_name: str) -> str:
    return f"https://storage.googleapis.com/{settings.GCS_BUCKET_NAME}/{object_name}"


# ── Upload helpers ────────────────────────────────────────────────────────────

async def upload_image(
    file: UploadFile,
    prefix: str = TICKET_PREFIX,
) -> dict:

    data = await file.read()
    size_bytes = len(data)

    validate_upload(file.filename or "upload", file.content_type or "", size_bytes)

    original_name = file.filename or "upload"
    ext = original_name.rsplit(".", 1)[-1].lower() if "." in original_name else "bin"
    stored_name = f"{uuid.uuid4()}.{ext}"
    obj_name = _object_name(prefix, stored_name)

    bucket = _get_bucket()
    blob = bucket.blob(obj_name)
    blob.cache_control = "private, max-age=3600"
    blob.upload_from_string(data, content_type=file.content_type)

    url = _public_url(obj_name)

    logger.info(
        f"[GCS] Uploaded '{original_name}' → gs://{settings.GCS_BUCKET_NAME}/{obj_name} "
        f"({size_bytes} bytes, {file.content_type})"
    )

    return {
        "original_name": original_name,
        "stored_name":   stored_name,
        "content_type":  file.content_type,
        "size_bytes":    size_bytes,
        "path":          obj_name,    # full GCS object path (includes prefix)
        "url":           url,
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
            meta = await upload_image(file, prefix=prefix)
            results.append(meta)
        except UploadValidationError as exc:
            # Invalid file type / size — skip and warn, don't abort the request
            logger.warning(f"[GCS] Skipped '{file.filename}' — {exc}")
        except Exception as exc:
            logger.error(
                f"[GCS] Upload failed for '{file.filename}': {exc}",
                exc_info=True,
            )
            raise

    return results


def delete_object(object_path: str) -> None:
    """
    Delete a GCS object by its full object path (e.g. 'ticketing/tickets/abc.png').
    Silently ignores objects that no longer exist.
    """
    try:
        bucket = _get_bucket()
        blob = bucket.blob(object_path)
        blob.delete()
        logger.info(f"[GCS] Deleted gs://{settings.GCS_BUCKET_NAME}/{object_path}")
    except Exception as exc:
        # Log but don't raise — a missing file on delete is non-fatal
        logger.warning(f"[GCS] Delete failed for '{object_path}': {exc}")



def generate_signed_url(object_path: str, expiry_minutes: int = 60) -> str:
    """Generate a temporary signed URL for a private GCS object."""
    try:
        bucket = _get_bucket()
        blob = bucket.blob(object_path)
        url = blob.generate_signed_url(
            expiration=timedelta(minutes=expiry_minutes),
            method="GET",
            version="v4",
        )
        return url
    except Exception as exc:
        logger.warning(f"[GCS] Failed to generate signed URL for '{object_path}': {exc}")
        return f"https://storage.googleapis.com/{settings.GCS_BUCKET_NAME}/{object_path}"
    


def _inject_signed_urls(response: TicketResponse) -> TicketResponse:
    """Replace GCS attachment URLs with signed URLs valid for 60 minutes."""
    if not response.attachments:
        return response
    signed = []
    for att in response.attachments:
        signed.append(att.model_copy(update={
            "url": generate_signed_url(att.path, expiry_minutes=60)
        }))
    return response.model_copy(update={"attachments": signed})