"""Conference official router - endpoints for district officials."""

from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, and_, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.common.db import get_async_db
from app.common.security import get_current_user
from app.auth.models import CustomUser, UnitMembers, UserType
from app.conference.models import ConferenceDelegate, ConferencePayment, FoodPreference
from app.conference.schemas import (
    FoodPreferenceCreate,
    FoodPreferenceResponse,
    ConferencePaymentCreate,
    ConferencePaymentResponse,
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
    
    # Get max count
    max_count = current_user.conference_member_count
    
    # Get already delegated member IDs from this district
    stmt = select(ConferenceDelegate.members_id).where(
        and_(
            ConferenceDelegate.conference_id == current_user.conference_id,
            ConferenceDelegate.officials_id == current_user.id,
            ConferenceDelegate.members_id.isnot(None)
        )
    )
    result = await db.execute(stmt)
    delegated_member_ids = [row[0] for row in result.all()]
    
    # Get available unit members from the district (excluding official's phone)
    stmt = (
        select(
            UnitMembers.id,
            UnitMembers.name,
            UnitMembers.number,
            UnitMembers.gender,
        )
        .join(CustomUser, UnitMembers.registered_user_id == CustomUser.id)
        .where(
            and_(
                CustomUser.unit_name.has(clergy_district_id=current_user.clergy_district_id),
                UnitMembers.number != current_user.phone_number,
            )
        )
        .order_by(UnitMembers.name)
    )
    result = await db.execute(stmt)
    unit_members = result.all()
    
    # Calculate remaining count
    rem_count = max_count - len(delegated_member_ids)
    
    return {
        "conference": {
            "id": conference.id,
            "title": conference.title,
            "details": conference.details,
            "status": conference.status,
        },
        "rem_count": rem_count,
        "max_count": max_count,
        "allowed_count": current_user.conference_member_count,
        "member_count": len(delegated_member_ids),
        "district": current_user.clergy_district.name if current_user.clergy_district else None,
        "unit_members": [
            {
                "id": m.id,
                "name": m.name,
                "number": m.number,
                "gender": m.gender,
            }
            for m in unit_members
        ],
    }


@router.post("/delegates/{member_id}", response_model=dict)
async def add_delegate(
    member_id: int,
    current_user: CustomUser = Depends(get_current_official),
    db: AsyncSession = Depends(get_async_db),
):
    """Add a member as a delegate."""
    if not current_user.conference_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No conference assigned"
        )
    
    await conference_service.add_conference_delegate_member(
        db, current_user.conference_id, member_id, current_user.id
    )
    
    return {"message": "Delegate added successfully"}


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
    
    # Single JOIN to get delegate members directly instead of two round-trips
    stmt = (
        select(
            UnitMembers.id,
            UnitMembers.name,
            UnitMembers.number,
            UnitMembers.gender,
        )
        .join(ConferenceDelegate, ConferenceDelegate.members_id == UnitMembers.id)
        .where(
            and_(
                ConferenceDelegate.conference_id == current_user.conference_id,
                ConferenceDelegate.officials_id == current_user.id,
                ConferenceDelegate.members_id.isnot(None),
            )
        )
    )
    result = await db.execute(stmt)
    delegate_members = result.all()
    
    # Get delegate officials (all officials from this district)
    stmt = select(CustomUser).where(
        and_(
            CustomUser.clergy_district_id == current_user.clergy_district_id,
            CustomUser.user_type == UserType.DISTRICT_OFFICIAL
        )
    ).order_by(CustomUser.first_name)
    result = await db.execute(stmt)
    delegate_officials = list(result.scalars().all())
    
    delegates_count = len(delegate_members) + len(delegate_officials)
    max_count = current_user.conference_official_count + current_user.conference_member_count
    fee = await conference_service.get_delegate_fee(db)
    # Fee = per-delegate rate × (district officials + members added as delegates)
    amount_to_pay = delegates_count * fee
    
    # Get payment status (get the latest payment)
    stmt = select(ConferencePayment).where(
        and_(
            ConferencePayment.conference_id == current_user.conference_id,
            ConferencePayment.uploaded_by_id == current_user.id
        )
    ).order_by(ConferencePayment.date.desc()).limit(1)
    result = await db.execute(stmt)
    payment = result.scalar_one_or_none()
    
    # Get food preference
    stmt = select(FoodPreference).where(
        and_(
            FoodPreference.conference_id == current_user.conference_id,
            FoodPreference.uploaded_by_id == current_user.id
        )
    )
    result = await db.execute(stmt)
    food_preference = result.scalar_one_or_none()
    
    return {
        "delegate_members": [
            {
                "id": m.id,
                "name": m.name,
                "number": m.number,
                "gender": m.gender,
            }
            for m in delegate_members
        ],
        "delegate_officials": [
            {
                "id": o.id,
                "name": o.first_name,
                "phone": o.phone_number,
            }
            for o in delegate_officials
        ],
        "delegates_count": delegates_count,
        "max_count": max_count,
        "payment_status": payment.status if payment else None,
        "amount_to_pay": amount_to_pay,
        "food_preference": {
            "veg_count": food_preference.veg_count if food_preference else 0,
            "non_veg_count": food_preference.non_veg_count if food_preference else 0,
        } if food_preference else None,
    }


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


@router.post("/payment", response_model=dict)
async def make_payment(
    data: ConferencePaymentCreate,
    current_user: CustomUser = Depends(get_current_official),
    db: AsyncSession = Depends(get_async_db),
):
    """Upload payment proof."""
    if not current_user.conference_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No conference assigned"
        )
    
    # Set conference_id from current user
    data.conference_id = current_user.conference_id
    
    payment = await conference_service.create_conference_payment(
        db, current_user.conference_id, current_user.id, data
    )
    
    return {"message": "Payment data uploaded successfully", "payment_id": payment.id}


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

