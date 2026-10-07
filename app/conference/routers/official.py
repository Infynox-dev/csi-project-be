"""Conference official router - endpoints for district officials."""

from typing import Optional
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.common.db import get_async_db
from app.common.security import get_current_user
from app.auth.models import CustomUser, UnitMembers, UnitName, UserType
from app.conference.models import ConferenceDelegate, FoodPreference
from app.conference.schemas import (
    AttendeeAdd,
    AttendeePreferenceUpdate,
    FoodPreferenceCreate,
    FoodPreferenceResponse,
)
from app.conference import service as conference_service

router = APIRouter()


async def get_current_official(
    current_user: CustomUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> CustomUser:
    """Dependency to ensure user is a district official and eagerly load relationships."""
    if current_user.user_type != UserType.DISTRICT_OFFICIAL:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied. District official required."
        )
    
    # Eagerly load clergy_district relationship
    stmt = select(CustomUser).where(CustomUser.id == current_user.id).options(
        selectinload(CustomUser.clergy_district)
    )
    result = await db.execute(stmt)
    user_with_relations = result.scalar_one_or_none()
    
    return user_with_relations if user_with_relations else current_user


@router.get("/view", response_model=dict)
async def view_conference(
    current_user: CustomUser = Depends(get_current_official),
    db: AsyncSession = Depends(get_async_db),
):
    """View conference details and available members for the official's district."""
    if not current_user.conference_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No conference assigned"
        )
    
    # Get conference
    conference = await conference_service.get_conference_by_id(db, current_user.conference_id)
    
    member_limit = current_user.conference_member_count or 0
    official_limit = current_user.conference_official_count or 0
    official_count = 0
    member_count = 0
    if current_user.clergy_district_id:
        await conference_service.ensure_district_official_rows(
            db, current_user.conference_id, current_user.clergy_district_id
        )
        official_count, member_count = await conference_service.district_role_counts(
            db, current_user.conference_id, current_user.clergy_district_id
        )

    added_rows = await db.execute(
        select(
            ConferenceDelegate.members_id,
            ConferenceDelegate.attendee_role,
            CustomUser.phone_number,
        )
        .join(CustomUser, ConferenceDelegate.officials_id == CustomUser.id)
        .where(
            ConferenceDelegate.conference_id == current_user.conference_id,
            CustomUser.clergy_district_id == current_user.clergy_district_id,
        )
    )
    added_by_member: dict[int, str] = {}
    official_phones: set[str] = set()
    for members_id, role, phone in added_rows.all():
        kind = role if role in ("official", "delegate") else (
            "official" if members_id is None else "delegate"
        )
        if members_id is not None:
            added_by_member[members_id] = kind
        elif phone:
            official_phones.add(phone.strip())

    stmt = (
        select(
            UnitMembers.id,
            UnitMembers.name,
            UnitMembers.number,
            UnitMembers.gender,
            UnitName.name.label("unit_name"),
        )
        .join(CustomUser, UnitMembers.registered_user_id == CustomUser.id)
        .outerjoin(UnitName, CustomUser.unit_name_id == UnitName.id)
        .where(CustomUser.unit_name.has(clergy_district_id=current_user.clergy_district_id))
        .order_by(UnitMembers.name)
    )
    result = await db.execute(stmt)
    unit_members = result.all()

    rem_count = max(0, member_limit - member_count)
    official_rem_count = max(0, official_limit - official_count)

    return {
        "conference": {
            "id": conference.id,
            "title": conference.title,
            "details": conference.details,
            "status": conference.status,
        },
        "rem_count": rem_count,
        "max_count": member_limit,
        "allowed_count": member_limit,
        "member_count": member_count,
        "official_limit": official_limit,
        "official_count": official_count,
        "official_rem_count": official_rem_count,
        "district": current_user.clergy_district.name if current_user.clergy_district else None,
        "unit_members": [
            {
                "id": m.id,
                "name": m.name,
                "number": m.number,
                "gender": m.gender,
                "unit_name": m.unit_name,
                "registered_as": added_by_member.get(m.id)
                or ("official" if (m.number or "").strip() in official_phones else None),
            }
            for m in unit_members
        ],
    }


@router.post("/delegates/{member_id}", response_model=dict)
async def add_delegate(
    member_id: int,
    data: AttendeeAdd,
    current_user: CustomUser = Depends(get_current_official),
    db: AsyncSession = Depends(get_async_db),
):
    """Add a district member as an official or a delegate."""
    if not current_user.conference_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No conference assigned"
        )

    await conference_service.add_conference_delegate_member(
        db,
        current_user.conference_id,
        member_id,
        current_user.id,
        role=data.role,
        food_preference=data.food_preference,
        accommodation_required=data.accommodation_required,
    )

    label = "Official" if data.role == "official" else "Delegate"
    return {"message": f"{label} added successfully"}


@router.get("/delegates", response_model=dict)
async def view_delegates(
    current_user: CustomUser = Depends(get_current_official),
    db: AsyncSession = Depends(get_async_db),
):
    """View all delegates (officials + members) for this district."""
    if not current_user.conference_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No conference assigned"
        )
    
    if not current_user.clergy_district_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="District official has no district assigned.",
        )

    delegate_officials, delegate_members = await conference_service.list_district_attendees(
        db, current_user.conference_id, current_user.clergy_district_id
    )
    delegates_count = len(delegate_members) + len(delegate_officials)
    official_limit = current_user.conference_official_count or 0
    member_limit = current_user.conference_member_count or 0
    max_count = official_limit + member_limit
    veg_count = sum(
        1 for row in (*delegate_officials, *delegate_members) if row["food_preference"] == "veg"
    )
    non_veg_count = sum(
        1
        for row in (*delegate_officials, *delegate_members)
        if row["food_preference"] == "non-veg"
    )
    ledger = None
    if current_user.clergy_district_id:
        ledger = await conference_service.get_district_payment_ledger(
            db, current_user.conference_id, current_user.clergy_district_id
        )
        await db.commit()

    return {
        "delegate_members": delegate_members,
        "delegate_officials": delegate_officials,
        "delegates_count": delegates_count,
        "max_count": max_count,
        "official_count": len(delegate_officials),
        "official_limit": official_limit,
        "member_count": len(delegate_members),
        "member_limit": member_limit,
        "payment_status": (
            conference_service.overall_status_legacy(ledger["overall_status"])
            if ledger
            else None
        ),
        "amount_to_pay": ledger["fee_owed"] if ledger else 0,
        "total_paid": ledger["total_paid"] if ledger else 0,
        "balance_due": ledger["balance_due"] if ledger else 0,
        "overall_status": ledger["overall_status"] if ledger else "not_submitted",
        "food_preference": {
            "veg_count": veg_count,
            "non_veg_count": non_veg_count,
        },
    }


@router.patch("/delegates/{delegate_id}", response_model=dict)
async def update_delegate_preferences(
    delegate_id: int,
    data: AttendeePreferenceUpdate,
    current_user: CustomUser = Depends(get_current_official),
    db: AsyncSession = Depends(get_async_db),
):
    """Set food and accommodation for an official or delegate in this district."""
    if not current_user.conference_id or not current_user.clergy_district_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No conference assigned",
        )
    await conference_service.update_attendee_preferences(
        db,
        delegate_id,
        current_user.conference_id,
        current_user.clergy_district_id,
        food_preference=data.food_preference,
        accommodation_required=data.accommodation_required,
    )
    return {"message": "Preferences updated"}


@router.delete("/delegates/{delegate_id}", response_model=dict)
async def remove_attendee(
    delegate_id: int,
    current_user: CustomUser = Depends(get_current_official),
    db: AsyncSession = Depends(get_async_db),
):
    """Remove an added official or delegate. The district login account stays."""
    if not current_user.conference_id or not current_user.clergy_district_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No conference assigned",
        )
    await conference_service.remove_conference_attendee(
        db,
        delegate_id,
        current_user.conference_id,
        current_user.clergy_district_id,
    )
    return {"message": "Removed successfully"}


@router.delete("/delegates/members/{member_id}", response_model=dict)
async def remove_delegate_member(
    member_id: int,
    current_user: CustomUser = Depends(get_current_official),
    db: AsyncSession = Depends(get_async_db),
):
    """Remove a member from delegates."""
    if not current_user.conference_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No conference assigned"
        )
    
    await conference_service.remove_conference_delegate_member(
        db, member_id, current_user.conference_id
    )
    
    return {"message": "Delegate member removed successfully"}


@router.get("/payment", response_model=dict)
async def get_district_payment(
    current_user: CustomUser = Depends(get_current_official),
    db: AsyncSession = Depends(get_async_db),
):
    """District conference ledger plus the shared Units UPI QR."""
    if not current_user.conference_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No conference assigned",
        )
    if not current_user.clergy_district_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="District official has no district assigned.",
        )

    ledger = await conference_service.get_district_payment_ledger(
        db, current_user.conference_id, current_user.clergy_district_id
    )
    await db.commit()
    return conference_service.ledger_api_payload(ledger)


@router.post("/payment", response_model=dict)
async def make_payment(
    file: UploadFile = File(..., description="Payment proof screenshot or PDF"),
    payment_reference: Optional[str] = Form(None),
    current_user: CustomUser = Depends(get_current_official),
    db: AsyncSession = Depends(get_async_db),
):
    """Upload a district payment proof against the shared Units QR."""
    if not current_user.conference_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No conference assigned",
        )

    payment = await conference_service.create_conference_payment(
        db,
        current_user.conference_id,
        current_user,
        file,
        payment_reference=payment_reference,
    )
    return {
        "message": "Payment proof submitted successfully. Awaiting admin review.",
        "payment_id": payment.id,
        "status": payment.status.value if payment.status else None,
    }


@router.post("/food-preference", response_model=FoodPreferenceResponse)
async def set_food_preference(
    data: FoodPreferenceCreate,
    current_user: CustomUser = Depends(get_current_official),
    db: AsyncSession = Depends(get_async_db),
):
    """Set food preferences for the district."""
    if not current_user.conference_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No conference assigned"
        )
    
    # Set conference_id from current user
    data.conference_id = current_user.conference_id
    
    preference = await conference_service.set_food_preference(
        db, current_user.conference_id, current_user.id, data
    )
    
    return preference


@router.get("/export-excel", response_model=dict)
async def export_conference_data(
    current_user: CustomUser = Depends(get_current_official),
    db: AsyncSession = Depends(get_async_db),
):
    """Export district conference data to Excel (placeholder for actual Excel generation)."""
    if not current_user.conference_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No conference assigned"
        )
    
    # Get conference info for this district only
    all_info = await conference_service.get_all_conference_info(db, current_user.conference_id)
    
    district_name = current_user.clergy_district.name if current_user.clergy_district else None
    district_data = all_info.get(district_name, {})
    
    return {
        "message": "Excel export functionality to be implemented",
        "district": district_name,
        "data": district_data,
    }

