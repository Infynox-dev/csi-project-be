"""Admin site settings router - site settings, notices, quick links."""

from datetime import datetime
from typing import List

from app.common.datetime_utils import now_ist

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile, status
from sqlalchemy.orm import Session

from app.common.db import get_db
from app.common.storage import save_upload_file, get_file_url
from app.common.config import get_settings
from app.common.security import get_current_user_sync
from app.common.cache import get_cache, set_cache, clear_cache
from app.auth.models import CustomUser, UserType
from app.admin.models import SiteSettings, Notice, QuickLink
from app.admin.schemas import (
    SiteSettingsResponse,
    SiteSettingsUpdate,
    LogoUploadResponse,
    NoticeCreate,
    NoticeUpdate,
    NoticeResponse,
    NoticeReorderRequest,
    QuickLinkCreate,
    QuickLinkUpdate,
    QuickLinkResponse,
)

router = APIRouter()
settings = get_settings()

# Cache configuration for site settings
SITE_SETTINGS_CACHE_KEY = "site_settings_v1"
SITE_SETTINGS_CACHE_TTL = 1800  # 30 minutes


def get_public_file_url(object_key: str | None) -> str | None:
    """Convert object key to API proxy URL for file access."""
    if not object_key:
        return None
    # Use the API proxy endpoint to serve files
    # Remove the prefix since the proxy will add it
    prefix = settings.storage_key_prefix or ""
    path = object_key[len(prefix):] if object_key.startswith(prefix) else object_key
    return f"/api/files/{path}"


def get_admin_user(
    current_user: CustomUser = Depends(get_current_user_sync),
) -> CustomUser:
    """Dependency to ensure user is an admin."""
    if current_user.user_type != UserType.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied. Admin privileges required."
        )
    return current_user


def get_or_create_site_settings(db: Session) -> SiteSettings:
    """Get or create the singleton site settings row."""
    settings = db.query(SiteSettings).first()
    if not settings:
        settings = SiteSettings(app_name="CSI MKD YOUTH MOVEMENT")
        db.add(settings)
        db.commit()
        db.refresh(settings)
    return settings


# ============ SITE SETTINGS ENDPOINTS ============

@router.get("/site-settings", response_model=dict)
def get_site_settings(db: Session = Depends(get_db), response: Response = None):
    """Get all site settings (public endpoint)."""
    
    # Check in-memory cache first
    cached = get_cache(SITE_SETTINGS_CACHE_KEY)
    if cached:
        if response:
            response.headers["X-Cache"] = "HIT"
            response.headers["Cache-Control"] = "public, s-maxage=1800, stale-while-revalidate=3600"
            response.headers["CDN-Cache-Control"] = "public, max-age=1800"
        return cached
    
    # Cache miss - fetch from database
    site_settings = get_or_create_site_settings(db)
    
    # Also fetch quick links for the response
    quick_links = db.query(QuickLink).filter(QuickLink.enabled == True).order_by(QuickLink.display_order).all()
    
    site_response = SiteSettingsResponse.from_orm_with_nested(site_settings)
    response_dict = site_response.model_dump()
    
    # Convert logo object keys to full public URLs
    response_dict["logo_primary_url"] = get_public_file_url(site_settings.logo_primary_url)
    response_dict["logo_secondary_url"] = get_public_file_url(site_settings.logo_secondary_url)
    response_dict["logo_tertiary_url"] = get_public_file_url(site_settings.logo_tertiary_url)
    response_dict["payment_qr_url"] = get_public_file_url(site_settings.payment_qr_url)
    
    response_dict["quick_links"] = [
        {"id": ql.id, "label": ql.label, "url": ql.url, "enabled": ql.enabled}
        for ql in quick_links
    ]
    
    # Cache the result
    set_cache(SITE_SETTINGS_CACHE_KEY, response_dict, ttl_seconds=SITE_SETTINGS_CACHE_TTL)
    
    # Add Vercel Edge + Browser caching headers
    if response:
        response.headers["X-Cache"] = "MISS"
        response.headers["Cache-Control"] = "public, s-maxage=1800, stale-while-revalidate=3600"
        response.headers["CDN-Cache-Control"] = "public, max-age=1800"
    
    return response_dict


@router.put("/site-settings", response_model=dict)
def update_site_settings(
    data: SiteSettingsUpdate,
    current_user: CustomUser = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Update site settings (admin only)."""
    settings = get_or_create_site_settings(db)
    
    # Update flat fields (includes member_min_age / member_max_age when provided)
    update_data = data.model_dump(exclude_unset=True, exclude={"contact", "social_links"})
    for field, value in update_data.items():
        setattr(settings, field, value)
    
    # Update nested contact info
    if data.contact:
        if data.contact.address is not None:
            settings.contact_address = data.contact.address
        if data.contact.email is not None:
            settings.contact_email = data.contact.email
        if data.contact.phone is not None:
            settings.contact_phone = data.contact.phone
    
    # Update nested social links
    if data.social_links:
        if data.social_links.facebook is not None:
            settings.social_facebook = data.social_links.facebook
        if data.social_links.instagram is not None:
            settings.social_instagram = data.social_links.instagram
        if data.social_links.youtube is not None:
            settings.social_youtube = data.social_links.youtube
    
    settings.updated_at = now_ist()
    db.commit()
    db.refresh(settings)
    
    # Invalidate cache after update
    clear_cache(SITE_SETTINGS_CACHE_KEY)
    
    site_response = SiteSettingsResponse.from_orm_with_nested(settings).model_dump()
    site_response["payment_qr_url"] = get_public_file_url(settings.payment_qr_url)
    return {
        "message": "Site settings updated successfully",
        **site_response
    }


@router.post("/site-settings/logo", response_model=LogoUploadResponse)
def upload_logo(
    logo_type: str = Form(..., description="Logo type: primary, secondary, or tertiary"),
    file: UploadFile = File(..., description="Image file to upload"),
    current_user: CustomUser = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Upload a logo image (admin only)."""
    # Validate logo_type
    valid_types = ["primary", "secondary", "tertiary"]
    if logo_type not in valid_types:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid logo_type. Must be one of: {', '.join(valid_types)}"
        )
    
    settings = get_or_create_site_settings(db)
    
    # Upload to object storage
    object_key, _ = save_upload_file(file, subdir="site/logos")
    
    # Update the appropriate logo field
    logo_field = f"logo_{logo_type}_url"
    setattr(settings, logo_field, object_key)
    settings.updated_at = now_ist()
    db.commit()
    
    # Invalidate cache after logo update
    clear_cache(SITE_SETTINGS_CACHE_KEY)
    
    return LogoUploadResponse(
        logo_type=logo_type,
        url=get_public_file_url(object_key) or object_key,
        filename=file.filename or "unknown"
    )


# ============ FILE PROXY ENDPOINT ============

@router.get("/files/{file_path:path}")
def get_file(file_path: str):
    """
    Proxy endpoint to serve files from object storage.
    This is needed because presigned URLs don't work well with restricted storage keys.
    """
    from fastapi.responses import StreamingResponse
    from app.common.storage import get_s3_client
    from botocore.exceptions import ClientError
    
    # Add the required prefix if not present
    prefix = settings.storage_key_prefix or ""
    if not file_path.startswith(prefix):
        file_path = f"{prefix}{file_path}"
    
    try:
        s3_client = get_s3_client()
        response = s3_client.get_object(
            Bucket=settings.storage_bucket,
            Key=file_path
        )
        
        return StreamingResponse(
            response['Body'],
            media_type=response.get('ContentType', 'application/octet-stream'),
            headers={
                'Cache-Control': 'public, max-age=86400',
                'Content-Length': str(response.get('ContentLength', 0)),
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Methods': 'GET, OPTIONS',
                'Access-Control-Allow-Headers': '*',
                'Vary': 'Origin',
            }
        )
    except ClientError as e:
        if e.response['Error']['Code'] == 'NoSuchKey':
            raise HTTPException(status_code=404, detail="File not found")
        raise HTTPException(status_code=500, detail="Failed to retrieve file")


# ============ MEMBER AGE LIMITS ENDPOINT ============

@router.get("/member-age-limits", response_model=dict)
def get_member_age_limits(db: Session = Depends(get_db)):
    """
    Public endpoint: returns the configured min/max DOB for unit member registration.
    """
    site = get_or_create_site_settings(db)
    return {
        "min_dob": site.member_min_dob.isoformat() if site.member_min_dob else "1990-01-01",
        "max_dob": site.member_max_dob.isoformat() if site.member_max_dob else "2011-12-31",
    }


# ============ NOTICES ENDPOINTS ============

@router.get("/notices", response_model=List[NoticeResponse])
def get_notices(
    active_only: bool = False,
    db: Session = Depends(get_db),
):
    """Get all notices (public endpoint)."""
    query = db.query(Notice)
    
    if active_only:
        now = now_ist()
        query = query.filter(
            Notice.is_active == True,
            (Notice.start_date == None) | (Notice.start_date <= now),
            (Notice.end_date == None) | (Notice.end_date >= now),
        )
    
    notices = query.order_by(Notice.display_order).all()
    return notices


@router.post("/notices", response_model=NoticeResponse, status_code=status.HTTP_201_CREATED)
def create_notice(
    data: NoticeCreate,
    current_user: CustomUser = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Create a new notice (admin only)."""
    notice = Notice(**data.model_dump())
    db.add(notice)
    db.commit()
    db.refresh(notice)
    return notice


@router.put("/notices/{notice_id}", response_model=NoticeResponse)
def update_notice(
    notice_id: int,
    data: NoticeUpdate,
    current_user: CustomUser = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Update a notice (admin only)."""
    notice = db.query(Notice).filter(Notice.id == notice_id).first()
    if not notice:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notice not found")
    
    update_data = data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(notice, field, value)
    
    db.commit()
    db.refresh(notice)
    return notice


@router.delete("/notices/{notice_id}")
def delete_notice(
    notice_id: int,
    current_user: CustomUser = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Delete a notice (admin only)."""
    notice = db.query(Notice).filter(Notice.id == notice_id).first()
    if not notice:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notice not found")
    
    db.delete(notice)
    db.commit()
    return {"message": "Notice deleted successfully"}


@router.put("/notices/reorder")
def reorder_notices(
    data: NoticeReorderRequest,
    current_user: CustomUser = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Reorder notices (admin only)."""
    for item in data.order:
        notice = db.query(Notice).filter(Notice.id == item.id).first()
        if notice:
            notice.display_order = item.display_order
    
    db.commit()
    return {"message": "Notices reordered successfully"}


# ============ QUICK LINKS ENDPOINTS ============

@router.get("/quick-links", response_model=List[QuickLinkResponse])
def get_quick_links(db: Session = Depends(get_db)):
    """Get all quick links (public endpoint)."""
    links = db.query(QuickLink).order_by(QuickLink.display_order).all()
    return links


@router.post("/quick-links", response_model=QuickLinkResponse, status_code=status.HTTP_201_CREATED)
def create_quick_link(
    data: QuickLinkCreate,
    current_user: CustomUser = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Create a new quick link (admin only)."""
    link = QuickLink(**data.model_dump())
    db.add(link)
    db.commit()
    db.refresh(link)
    return link


@router.put("/quick-links/{link_id}", response_model=QuickLinkResponse)
def update_quick_link(
    link_id: int,
    data: QuickLinkUpdate,
    current_user: CustomUser = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Update a quick link (admin only)."""
    link = db.query(QuickLink).filter(QuickLink.id == link_id).first()
    if not link:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Quick link not found")
    
    update_data = data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(link, field, value)
    
    db.commit()
    db.refresh(link)
    return link


@router.delete("/quick-links/{link_id}")
def delete_quick_link(
    link_id: int,
    current_user: CustomUser = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Delete a quick link (admin only)."""
    link = db.query(QuickLink).filter(QuickLink.id == link_id).first()
    if not link:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Quick link not found")
    
    db.delete(link)
    db.commit()
    return {"message": "Quick link deleted successfully"}

