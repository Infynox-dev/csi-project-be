"""Admin conference router - administrative endpoints for conference management."""

from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.common.db import get_async_db
from app.common.security import get_current_user
from app.auth.models import CustomUser, UserType, UnitMembers, ClergyDistrict
from app.conference.models import Conference, ConferenceDelegate
from app.conference.schemas import (
    ConferenceCreate,
    ConferenceUpdate,
    ConferenceResponse,
    DistrictOfficialCreate,
    DistrictOfficialUpdate,
    ConferenceSettingsResponse,
    ConferenceSettingsUpdate,
)
from app.conference import service as conference_service

router = APIRouter()


async def get_admin_user(
    current_user: CustomUser = Depends(get_current_user),
) -> CustomUser:
    """Dependency to ensure user is an admin."""
    if current_user.user_type != UserType.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied. Admin privileges required."
        )
    return current_user


@router.get("/settings", response_model=ConferenceSettingsResponse)
async def get_conference_settings(
    current_user: CustomUser = Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Get conference module settings (delegate fee)."""
    return await conference_service.get_or_create_conference_settings(db)


@router.put("/settings", response_model=ConferenceSettingsResponse)
async def update_conference_settings(
    data: ConferenceSettingsUpdate,
    current_user: CustomUser = Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Update conference module settings (delegate fee)."""
    return await conference_service.update_conference_settings(db, data)


@router.get("/home", response_model=List[ConferenceResponse])
async def conference_home(
    current_user: CustomUser = Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Get conference dashboard with list of conferences."""
    stmt = select(Conference)
    result = await db.execute(stmt)
    return list(result.scalars().all())


@router.post("", response_model=ConferenceResponse)
async def create_conference(
    data: ConferenceCreate,
    current_user: CustomUser = Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Create a new conference."""
    return await conference_service.create_conference(db, data)


@router.put("/{conference_id}", response_model=ConferenceResponse)
async def update_conference(
    conference_id: int,
    data: ConferenceUpdate,
    current_user: CustomUser = Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Update a conference."""
    return await conference_service.update_conference(db, conference_id, data)


@router.delete("/{conference_id}", response_model=dict)
async def delete_conference(
    conference_id: int,
    current_user: CustomUser = Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Delete a conference."""
    await conference_service.delete_conference(db, conference_id)
    return {"message": "Conference deleted successfully"}


@router.get("/{conference_id}/info", response_model=dict)
async def get_conference_info(
    conference_id: int,
    current_user: CustomUser = Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Get all conference information aggregated by district."""
    district_info = await conference_service.get_all_conference_info(db, conference_id)
    return {
        "conference_id": conference_id,
        "district_info": district_info,
    }


@router.post("/{conference_id}/info/export", response_model=dict)
async def export_conference_info(
    conference_id: int,
    current_user: CustomUser = Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Export conference information to Excel (placeholder)."""
    district_info = await conference_service.get_all_conference_info(db, conference_id)
    return {
        "message": "Excel export functionality to be implemented",
        "conference_id": conference_id,
        "data": district_info,
    }


@router.get("/{conference_id}/payment-info", response_model=dict)
async def get_payment_info(
    conference_id: int,
    current_user: CustomUser = Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Get payment information aggregated by district."""
    payment_info = await conference_service.get_payment_info(db, conference_id)
    fee = await conference_service.get_delegate_fee(db)
    return {
        "conference_id": conference_id,
        "delegate_fee": fee,
        "district_info": payment_info,
    }


@router.post("/{conference_id}/payment-info/export", response_model=dict)
async def export_payment_info(
    conference_id: int,
    current_user: CustomUser = Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Export payment information to Excel (placeholder)."""
    payment_info = await conference_service.get_payment_info(db, conference_id)
    return {
        "message": "Excel export functionality to be implemented",
        "conference_id": conference_id,
        "data": payment_info,
    }


@router.get("/officials", response_model=List[dict])
async def list_district_officials(
    current_user: CustomUser = Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_db),
):
    """List all district officials."""
    stmt = select(CustomUser).where(
        CustomUser.user_type == UserType.DISTRICT_OFFICIAL
    ).options(selectinload(CustomUser.clergy_district))
    result = await db.execute(stmt)
    officials = list(result.scalars().all())
    
    return [
        {
            "id": official.id,
            "name": official.first_name,
            "phone": official.phone_number,
            "district": official.clergy_district.name if official.clergy_district else None,
            "conference_id": official.conference_id,
            "conference_official_count": official.conference_official_count,
            "conference_member_count": official.conference_member_count,
        }
        for official in officials
    ]


@router.post("/officials", response_model=dict)
async def add_district_official(
    data: DistrictOfficialCreate,
    current_user: CustomUser = Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Add a district official (create delegate account).
    
    The username will be the DISTRICT NAME (e.g., 'THIRUVALLA', 'ADOOR').
    This enables district-wise login for Kalamela and Conference modules.
    
    Default password is the official's phone number.
    """
    official = await conference_service.add_conference_delegate_official(
        db, data.conference_id, data
    )
    return {
        "message": "District official added successfully",
        "official_id": official.id,
        "username": official.username,  # This is now the district name
        "district": official.username,
        "default_password": "Phone number of the official",
    }


@router.put("/officials/{official_id}", response_model=dict)
async def update_district_official(
    official_id: int,
    data: DistrictOfficialUpdate,
    current_user: CustomUser = Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Update district official and propagate counts to all district users."""
    official = await conference_service.update_district_official(
        db,
        official_id,
        data.conference_official_count,
        data.conference_member_count,
    )
    return {
        "message": "District official updated successfully",
        "official_id": official.id,
    }


@router.delete("/officials/{official_id}", response_model=dict)
async def delete_district_official(
    official_id: int,
    current_user: CustomUser = Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Delete a district official."""
    await conference_service.delete_district_official(db, official_id)
    return {"message": "District official deleted successfully"}


@router.get("/{conference_id}/districts/{district_id}/members", response_model=List[dict])
async def view_district_members(
    conference_id: int,
    district_id: int,
    current_user: CustomUser = Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_db),
):
    """View all members from a district for conference registration."""
    # Get members from the district
    from app.units.member_serialization import MEMBER_RESIDENCE_LOAD_OPTIONS, serialize_member

    stmt = (
        select(UnitMembers)
        .join(CustomUser, UnitMembers.registered_user_id == CustomUser.id)
        .options(*MEMBER_RESIDENCE_LOAD_OPTIONS)
        .where(CustomUser.unit_name.has(clergy_district_id=district_id))
        .order_by(UnitMembers.name)
    )
    result = await db.execute(stmt)
    members = list(result.scalars().all())
    return [serialize_member(member) for member in members]

