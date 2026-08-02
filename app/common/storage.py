"""File storage on any S3-compatible provider (OCI Object Storage, Backblaze B2, ...)."""

import uuid
import logging
from pathlib import Path
from typing import Tuple
from functools import lru_cache

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from fastapi import HTTPException, UploadFile, status

from app.common.config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)


@lru_cache
def get_s3_client():
    """Get or create the S3 client for the configured object-storage provider."""
    if not settings.storage_access_key_id or not settings.storage_secret_access_key:
        raise ValueError(
            "Object storage not configured. Set STORAGE_ENDPOINT, STORAGE_BUCKET, "
            "STORAGE_ACCESS_KEY_ID and STORAGE_SECRET_ACCESS_KEY in environment."
        )

    return boto3.client(
        's3',
        endpoint_url=settings.storage_endpoint,
        aws_access_key_id=settings.storage_access_key_id,
        aws_secret_access_key=settings.storage_secret_access_key,
        region_name=settings.storage_region,
        config=Config(
            # OCI's S3-compat API rejects AWS SDK default streaming checksums
            # (boto3 >= 1.36); B2 tolerates when_required too.
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
            s3={"addressing_style": "path"},
        ),
    )


def _validate_upload(file: UploadFile, max_size_mb: int | None = None) -> None:
    """Validate uploaded file before saving."""
    if not file.filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing filename")
    
    suffix = Path(file.filename).suffix.lower()
    if settings.allowed_upload_extensions and suffix not in settings.allowed_upload_extensions:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, 
            detail=f"Unsupported file type: {suffix or 'unknown'}"
        )
    
    limit = max_size_mb or settings.max_upload_size_mb

    file.file.seek(0, 2)
    file_size = file.file.tell()
    file.file.seek(0)
    
    if file_size > limit * 1024 * 1024:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, 
            detail=f"File too large. Maximum size: {limit}MB"
        )


def save_upload_file(file: UploadFile, subdir: str = "", max_size_mb: int | None = None) -> Tuple[str, str]:
    """
    Upload file to object storage.
    
    Args:
        file: FastAPI UploadFile object
        subdir: Subdirectory path within bucket (e.g., "units/proofs", "kalamela/payments")
        max_size_mb: Override the global max upload size for this call
    
    Returns:
        Tuple of (object_key, object_key) - key is the storage object identifier
    
    Raises:
        HTTPException: If validation fails or upload fails
    """
    _validate_upload(file, max_size_mb=max_size_mb)
    
    # Generate unique object key
    suffix = Path(file.filename or "").suffix
    filename = f"{uuid.uuid4().hex}{suffix}"
    
    # Build full object key with subdirectory and required prefix
    # Legacy key-layout prefix retained so existing DB object keys keep resolving
    prefix = settings.storage_key_prefix or ""
    if subdir:
        object_key = f"{prefix}{subdir}/{filename}"
    else:
        object_key = f"{prefix}{filename}"
    
    try:
        # Read file content
        file.file.seek(0)
        file_content = file.file.read()
        
        # Upload to object storage
        s3_client = get_s3_client()
        s3_client.put_object(
            Bucket=settings.storage_bucket,
            Key=object_key,
            Body=file_content,
            ContentType=file.content_type or 'application/octet-stream',
        )
        
        logger.info(f"Successfully uploaded file to object storage: {object_key}")
        
        # Return object key twice (for backward compatibility with code expecting (key, path))
        return object_key, object_key
        
    except ClientError as e:
        logger.error(f"Failed to upload file to object storage: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to upload file to cloud storage"
        ) from e
    except Exception as e:
        logger.error(f"Unexpected error uploading file: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to save file"
        ) from e
    finally:
        file.file.close()


def delete_file(object_key: str) -> bool:
    """
    Delete file from object storage.
    
    Args:
        object_key: storage object key to delete (should already include prefix if saved via save_upload_file)
    
    Returns:
        True if deleted successfully, False otherwise
    """
    try:
        s3_client = get_s3_client()
        # Object key should already include prefix from save_upload_file
        s3_client.delete_object(
            Bucket=settings.storage_bucket,
            Key=object_key,
        )
        logger.info(f"Successfully deleted file from object storage: {object_key}")
        return True
    except ClientError as e:
        logger.error(f"Failed to delete file from object storage: {e}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error deleting file: {e}")
        return False


def get_file_url(object_key: str, expires_in: int = 3600) -> str:
    """
    Generate a pre-signed URL for accessing a private file in object storage.
    
    Args:
        object_key: storage object key
        expires_in: URL expiration time in seconds (default: 1 hour)
    
    Returns:
        Pre-signed URL string
    """
    try:
        s3_client = get_s3_client()
        url = s3_client.generate_presigned_url(
            'get_object',
            Params={
                'Bucket': settings.storage_bucket,
                'Key': object_key,
            },
            ExpiresIn=expires_in,
        )
        return url
    except ClientError as e:
        logger.error(f"Failed to generate presigned URL: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate file access URL"
        ) from e


def get_file_bytes(object_key: str | None) -> bytes | None:
    """Fetch raw file bytes from object storage."""
    if not object_key:
        return None

    try:
        s3_client = get_s3_client()
        response = s3_client.get_object(
            Bucket=settings.storage_bucket,
            Key=object_key,
        )
        return response["Body"].read()
    except ClientError as e:
        logger.warning("Failed to fetch file from object storage (%s): %s", object_key, e)
        return None
    except Exception as e:
        logger.warning("Unexpected error fetching file (%s): %s", object_key, e)
        return None


def ensure_dir(path: Path) -> Path:
    """
    Legacy function for local storage compatibility.
    Not used with remote object storage, but kept for backward compatibility with exporter.
    """
    path.mkdir(parents=True, exist_ok=True)
    return path
