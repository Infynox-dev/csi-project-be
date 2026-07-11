"""Units user router - endpoints for registered unit users."""

from datetime import date
from io import BytesIO
from pathlib import Path

from app.common.datetime_utils import now_ist
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.common.db import get_async_db
from app.common.security import get_current_user
from app.common.storage import save_upload_file, get_file_url
from app.auth.models import (
    ClergyDistrict,
    CustomUser,
    ResidenceLocation,
    UnitDetails,
    UnitMembers,
    UnitOfficials,
    UnitCouncilor,
    UnitName,
    UserType,
)
from app.admin.models import SiteSettings
from app.admin.routers.site import get_public_file_url
from app.common.notifications import notify_admin_archived_member_concern
from app.units.models import (
    ArchivedUnitMember,
    UnitTransferRequest,
    UnitMemberChangeRequest,
    UnitOfficialsChangeRequest,
    UnitCouncilorChangeRequest,
    UnitMemberAddRequest,
    UnitRegistrationCycle,
    UnitRegistrationPayment,
    PaymentProofStatus,
)
from app.units import registration_cycle_service as cycle_service
from app.units import service as units_service
from app.units.payment_ocr import extract_amount_from_pdf_bytes
from app.units.schemas import (
    UnitDetailsCreate,
    UnitDetailsResponse,
    UnitMemberCreate,
    UnitMemberUpdate,
    UnitMemberResponse,
    UnitOfficialsUpdate,
    UnitOfficialsResponse,
    UnitCouncilorCreate,
    UnitCouncilorResponse,
    StatusUpdate,
    UnitTransferRequestCreate,
    UnitTransferRequestResponse,
    TransferDestinationUnitResponse,
    UnitMemberChangeRequestCreate,
    UnitMemberChangeRequestResponse,
    UnitOfficialsChangeRequestCreate,
    UnitOfficialsChangeRequestResponse,
    UnitCouncilorChangeRequestCreate,
    UnitCouncilorChangeRequestResponse,
    UnitMemberAddRequestCreate,
    UnitMemberAddRequestResponse,
    ArchivedUnitMemberResponse,
    RecentArchivedMembersResponse,
    ArchivedMemberConcernRequestCreate,
    ArchivedMemberConcernRequestResponse,
    RemovedUnitMemberResponse,
    PendingRemovedMembersResponse,
    AcknowledgeRemovedMembersRequest,
)
from app.units import service as units_service
from app.units import residence_service
from app.units.member_serialization import MEMBER_RESIDENCE_LOAD_OPTIONS

router = APIRouter()

_MEMBER_RESIDENCE_OPTIONS = MEMBER_RESIDENCE_LOAD_OPTIONS


def _serialize_or_none(schema_cls, obj):
    if obj is None:
        return None
    return schema_cls.model_validate(obj).model_dump(mode="json")


def _serialize_list(schema_cls, objs):
    return [schema_cls.model_validate(obj).model_dump(mode="json") for obj in objs]


async def _get_unit_registration_fees(db: AsyncSession) -> tuple[int, int]:
    result = await db.execute(select(SiteSettings).limit(1))
    settings = result.scalar_one_or_none()
    unit_fee = settings.unit_registration_fee if settings and settings.unit_registration_fee is not None else 100
    member_fee = settings.unit_member_fee if settings and settings.unit_member_fee is not None else 10
    return unit_fee, member_fee


async def get_current_unit_user(
    current_user: CustomUser = Depends(get_current_user),
) -> CustomUser:
    """Dependency to ensure user is a registered unit user."""
    if current_user.user_type != UserType.UNIT:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied. Unit user required."
        )
    return current_user


async def _get_wizard_cycle(
    db: AsyncSession,
    user_id: int,
    *,
    require_in_progress: bool = True,
) -> UnitRegistrationCycle:
    cycle = await cycle_service.get_or_create_current_cycle(db, user_id)
    if cycle is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Registration is currently closed for this year.",
        )
    if require_in_progress:
        cycle_service.require_cycle_in_progress(cycle)
    return cycle


@router.get("/application-form", response_model=dict)
async def get_application_form(
    current_user: CustomUser = Depends(get_current_unit_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Get application form data with current registration status."""
    from app.common.cache import get_cache, set_cache

    # These two sub-queries are pure lookups on static data — cache them
    async def _get_fees_cached() -> tuple:
        key = "_fees:registration"
        cached = get_cache(key)
        if cached is not None:
            return cached
        val = await _get_unit_registration_fees(db)
        set_cache(key, val, ttl_seconds=300)
        return val

    async def _get_unit_name_cached(unit_name_id: int) -> tuple:
        key = f"_unit_name:{unit_name_id}"
        cached = get_cache(key)
        if cached is not None:
            return cached
        result = await db.execute(
            select(UnitName)
            .options(selectinload(UnitName.district))
            .where(UnitName.id == unit_name_id)
        )
        obj = result.scalar_one_or_none()
        val = (obj.name if obj else None, obj.district.name if obj and obj.district else None)
        set_cache(key, val, ttl_seconds=3600)
        return val

    async def _get_district_name_cached(district_id: int) -> str | None:
        key = f"_district:{district_id}"
        cached = get_cache(key)
        if cached is not None:
            return cached
        result = await db.execute(
            select(ClergyDistrict).where(ClergyDistrict.id == district_id)
        )
        d = result.scalar_one_or_none()
        val = d.name if d else None
        set_cache(key, val, ttl_seconds=3600)
        return val

    cycle, registration_enabled, has_any_completed_cycle = await cycle_service.resolve_active_cycle(
        db, current_user.id
    )
    unit_details = await cycle_service.get_unit_details_for_user(db, current_user.id)
    unit_officials = await cycle_service.get_unit_officials_for_user(db, current_user.id)

    result = await db.execute(
        select(UnitMembers)
        .options(*_MEMBER_RESIDENCE_OPTIONS)
        .where(UnitMembers.registered_user_id == current_user.id)
        .order_by(UnitMembers.name)
    )
    unit_members = list(result.scalars().all())

    result = await db.execute(
        select(UnitCouncilor).where(UnitCouncilor.registered_user_id == current_user.id)
    )
    unit_councilors = list(result.scalars().all())

    unit_registration_fee, unit_member_fee = await _get_fees_cached()

    unit_name_str, clergy_district_name = (None, None)
    if current_user.unit_name_id:
        unit_name_str, clergy_district_name = await _get_unit_name_cached(current_user.unit_name_id)

    if not clergy_district_name and current_user.clergy_district_id:
        clergy_district_name = await _get_district_name_cached(current_user.clergy_district_id)

    member_count = len(unit_members)
    members_amount = member_count * unit_member_fee
    total_amount = members_amount + unit_registration_fee

    number_of_fields = cycle_service.required_councilor_count(member_count)

    registration_status = cycle.status if cycle else "Not Started"
    registration_year = (
        cycle.registration_year if cycle
        else await cycle_service.get_current_registration_year(db)
    )
    path_type = cycle.path_type if cycle else "fresh"
    is_renewal = path_type == "renewal"
    cycle_id = cycle.id if cycle else None

    unit_details_payload = _serialize_or_none(UnitDetailsResponse, unit_details)
    if unit_details_payload is not None:
        unit_details_payload["registration_year"] = registration_year

    return {
        "user_data": {
            "id": current_user.id,
            "username": current_user.username,
            "email": current_user.email,
            "unit_name": unit_name_str,
            "clergy_district_name": clergy_district_name,
        },
        "registration_status": registration_status,
        "registration_year": registration_year,
        "path_type": path_type,
        "is_renewal": is_renewal,
        "cycle_id": cycle_id,
        "registration_enabled": registration_enabled,
        "has_any_completed_cycle": has_any_completed_cycle,
        "unit_details": unit_details_payload,
        "unit_officials": _serialize_or_none(UnitOfficialsResponse, unit_officials),
        "unit_members": _serialize_list(UnitMemberResponse, unit_members),
        "unit_councilors": _serialize_list(UnitCouncilorResponse, unit_councilors),
        "member_count": member_count,
        "councilor_count": len(unit_councilors),
        "number_of_councilor_fields": number_of_fields,
        "unit_registration_fee": unit_registration_fee,
        "unit_member_fee": unit_member_fee,
        "members_amount": members_amount,
        "total_amount": total_amount,
    }


@router.post("/details", response_model=dict)
async def save_unit_details(
    data: UnitDetailsCreate,
    current_user: CustomUser = Depends(get_current_unit_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Save unit details and president information."""
    cycle = await _get_wizard_cycle(db, current_user.id)
    cycle_service.require_cycle_in_progress(cycle)
    current_year = await cycle_service.get_current_registration_year(db)

    # Create or get unit details
    unit_details = await cycle_service.get_unit_details_for_user(db, current_user.id)
    
    if not unit_details:
        unit_details = UnitDetails(
            registered_user_id=current_user.id,
            registration_year=current_year,
        )
        db.add(unit_details)
    else:
        unit_details.registration_year = current_year
    
    # Create or update officials with president info
    unit_officials = await cycle_service.get_unit_officials_for_user(db, current_user.id)
    
    if not unit_officials:
        unit_officials = UnitOfficials(registered_user_id=current_user.id)
        db.add(unit_officials)
    
    unit_officials.president_designation = data.president_designation
    unit_officials.president_name = data.president_name.upper()
    unit_officials.president_phone = data.president_phone
    
    cycle.status = "Unit Details"
    
    await db.commit()
    
    return {"message": "Unit details saved successfully"}


@router.post("/details/confirm", response_model=dict)
async def confirm_unit_details(
    current_user: CustomUser = Depends(get_current_unit_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Confirm unit details step during renewal without modifying stored data."""
    cycle = await _get_wizard_cycle(db, current_user.id)
    if cycle.path_type != "renewal":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Details confirmation is only used for renewal registrations.",
        )

    current_year = await cycle_service.get_current_registration_year(db)
    unit_details = await cycle_service.get_unit_details_for_user(db, current_user.id)
    if unit_details:
        unit_details.registration_year = current_year

    cycle.status = "Unit Details"
    await db.commit()

    return {"message": "Unit details confirmed successfully"}


@router.post("/members", response_model=dict)
async def add_unit_member(
    data: UnitMemberCreate,
    current_user: CustomUser = Depends(get_current_unit_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Add a unit member."""
    cycle = await _get_wizard_cycle(db, current_user.id)

    # Check for duplicates
    stmt = select(UnitMembers).where(
        and_(
            UnitMembers.registered_user_id == current_user.id,
            UnitMembers.name == data.name.upper()
        )
    )
    result = await db.execute(stmt)
    existing = result.scalar_one_or_none()
    
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A member with the same name already exists"
        )
    
    # Check for duplicate name, dob, number
    if data.dob and data.number:
        stmt = select(UnitMembers).where(
            and_(
                UnitMembers.name == data.name.upper(),
                UnitMembers.dob == data.dob,
                UnitMembers.number == data.number
            )
        )
        result = await db.execute(stmt)
        existing = result.scalar_one_or_none()
        
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A member with the same name, DOB, and phone number already exists"
        )
    
    residence_location, residence_state_id, residence_city_id = await residence_service.apply_residence_fields(
        db,
        residence_location=data.residence_location,
        residence_state_id=data.residence_state_id,
        residence_city_id=data.residence_city_id,
    )

    # Create member
    member = UnitMembers(
        registered_user_id=current_user.id,
        name=data.name.upper(),
        gender=data.gender,
        dob=data.dob,
        number=data.number,
        qualification=data.qualification,
        blood_group=data.blood_group,
        residence_location=residence_location,
        residence_state_id=residence_state_id,
        residence_city_id=residence_city_id,
        added_registration_cycle_id=cycle.id,
    )
    
    db.add(member)
    if cycle.status in (
        cycle_service.DECLARATION_SUBMITTED,
        cycle_service.REGISTRATION_COMPLETED,
    ):
        await cycle_service.adjust_fee_for_member_delta(
            db,
            registered_user_id=current_user.id,
            delta_members=1,
        )
    await db.commit()
    await db.refresh(member)
    
    return {"message": "Unit member added successfully", "member_id": member.id}


@router.post("/members/submit", response_model=dict)
async def submit_unit_members(
    current_user: CustomUser = Depends(get_current_unit_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Mark members section as complete."""
    cycle = await _get_wizard_cycle(db, current_user.id)

    stmt = select(UnitMembers).where(UnitMembers.registered_user_id == current_user.id)
    result = await db.execute(stmt)
    unit_members = list(result.scalars().all())

    if not unit_members:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Add at least one member before continuing",
        )

    missing_location = [
        member.name
        for member in unit_members
        if not residence_service.member_residence_is_complete(
            member.residence_location,
            member.residence_state_id,
            member.residence_city_id,
        )
    ]
    if missing_location:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Living location is required for all members before continuing",
        )

    missing_blood_group = [member.name for member in unit_members if not member.blood_group]
    if missing_blood_group:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Blood group is required for all members before continuing",
        )

    cycle.status = "Unit Members Completed"
    await db.commit()
    
    return {"message": "Members section completed successfully"}


@router.post("/officials", response_model=dict)
async def add_unit_official(
    data: UnitOfficialsUpdate,
    current_user: CustomUser = Depends(get_current_unit_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Add or update unit officials."""
    cycle = await _get_wizard_cycle(db, current_user.id)
    cycle_service.require_cycle_in_progress(cycle)

    officials = await cycle_service.get_unit_officials_for_user(db, current_user.id)
    
    if not officials:
        officials = UnitOfficials(registered_user_id=current_user.id)
        db.add(officials)
    
    position = data.position
    name = data.name.upper()
    phone = data.phone
    
    if position == "President":
        if not data.designation:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Designation is required for President"
            )
        officials.president_designation = data.designation
        officials.president_name = name
        officials.president_phone = phone
    elif position == "Vice President":
        officials.vice_president_name = name
        officials.vice_president_phone = phone
    elif position == "Secretary":
        officials.secretary_name = name
        officials.secretary_phone = phone
    elif position == "Joint Secretary":
        officials.joint_secretary_name = name
        officials.joint_secretary_phone = phone
    elif position == "Treasurer":
        officials.treasurer_name = name
        officials.treasurer_phone = phone
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid position"
        )
    
    await db.commit()
    
    return {"message": f"{position} data added successfully"}


@router.post("/officials/confirm", response_model=dict)
async def confirm_unit_officials(
    current_user: CustomUser = Depends(get_current_unit_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Mark officials section as complete."""
    cycle = await _get_wizard_cycle(db, current_user.id)
    cycle.status = "Unit Officials Completed"
    await db.commit()
    
    return {"message": "Officials section completed successfully"}


@router.post("/councilors", response_model=dict)
async def add_unit_councilor(
    data: UnitCouncilorCreate,
    current_user: CustomUser = Depends(get_current_unit_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Add a unit councilor."""
    cycle = await _get_wizard_cycle(db, current_user.id, require_in_progress=False)
    cycle_service.require_cycle_open_for_councilor_edits(cycle)

    # Verify member exists
    stmt = select(UnitMembers).where(
        and_(
            UnitMembers.id == data.unit_member_id,
            UnitMembers.registered_user_id == current_user.id
        )
    )
    result = await db.execute(stmt)
    member = result.scalar_one_or_none()
    
    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Unit member not found"
        )
    
    # Check if already a councilor
    stmt = select(UnitCouncilor).where(
        and_(
            UnitCouncilor.registered_user_id == current_user.id,
            UnitCouncilor.unit_member_id == data.unit_member_id
        )
    )
    result = await db.execute(stmt)
    existing = result.scalar_one_or_none()
    
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Member is already a councilor"
        )
    
    # Create councilor
    councilor = UnitCouncilor(
        registered_user_id=current_user.id,
        unit_member_id=data.unit_member_id,
    )
    
    db.add(councilor)
    reopened = cycle_service.reopen_councilors_after_roster_change(cycle)
    await db.commit()
    await db.refresh(councilor)

    message = "Member added to unit council successfully"
    if reopened:
        message += ". Declaration was reopened — review councilors and submit again."
    return {"message": message, "councilor_id": councilor.id}


@router.post("/councilors/confirm", response_model=dict)
async def confirm_unit_councilors(
    current_user: CustomUser = Depends(get_current_unit_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Mark councilors section as complete."""
    cycle = await _get_wizard_cycle(db, current_user.id)
    await cycle_service.ensure_councilor_requirement_met(db, current_user.id)
    cycle.status = "Unit Councilors Completed"
    await db.commit()
    
    return {"message": "Councilors section completed successfully"}


@router.delete("/councilors/{councilor_id}", response_model=dict)
async def delete_unit_councilor(
    councilor_id: int,
    current_user: CustomUser = Depends(get_current_unit_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Remove a unit councilor during registration."""
    cycle = await _get_wizard_cycle(db, current_user.id, require_in_progress=False)
    cycle_service.require_cycle_open_for_councilor_edits(cycle)

    stmt = select(UnitCouncilor).where(
        and_(
            UnitCouncilor.id == councilor_id,
            UnitCouncilor.registered_user_id == current_user.id,
        )
    )
    result = await db.execute(stmt)
    councilor = result.scalar_one_or_none()

    if not councilor:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Councilor not found",
        )

    await db.delete(councilor)
    reopened = cycle_service.reopen_councilors_after_roster_change(cycle)
    await db.commit()

    message = "Councilor removed successfully"
    if reopened:
        message += ". Declaration was reopened — review councilors and submit again."
    return {"message": message}


@router.post("/declaration", response_model=dict)
async def complete_declaration(
    current_user: CustomUser = Depends(get_current_unit_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Complete the declaration and finalize registration."""
    cycle = await _get_wizard_cycle(db, current_user.id)

    member_count_result = await db.execute(
        select(UnitMembers).where(UnitMembers.registered_user_id == current_user.id)
    )
    member_count = len(list(member_count_result.scalars().all()))
    await cycle_service.ensure_councilor_requirement_met(
        db, current_user.id, member_count=member_count
    )
    unit_fee, member_fee = await _get_unit_registration_fees(db)
    total_fee = unit_fee + (member_count * member_fee)

    await cycle_service.submit_declaration(db, cycle, member_count, total_fee)
    
    return {"message": "Declaration submitted successfully"}


@router.get("/removed-members/pending", response_model=PendingRemovedMembersResponse)
async def get_pending_removed_members(
    current_user: CustomUser = Depends(get_current_unit_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Get members removed by admin that the unit has not yet acknowledged."""
    data = await units_service.get_pending_removed_members_for_unit(db, current_user.id)
    members_payload = []
    for member in data["members"]:
        payload = RemovedUnitMemberResponse.model_validate(member).model_dump(mode="json")
        payload["removed_at"] = payload.get("archived_at")
        payload["removal_type"] = member.removal_type.value if member.removal_type else None
        members_payload.append(payload)
    return {
        "summary": data["summary"],
        "members": members_payload,
    }


@router.post("/removed-members/acknowledge", response_model=dict)
async def acknowledge_removed_members(
    body: AcknowledgeRemovedMembersRequest,
    current_user: CustomUser = Depends(get_current_unit_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Mark admin-removed member notifications as seen by the unit."""
    count = await units_service.acknowledge_removed_members(
        db,
        current_user.id,
        removed_member_ids=body.removed_member_ids,
    )
    return {"message": f"{count} notification(s) acknowledged", "acknowledged_count": count}


@router.get("/archived-members/recent", response_model=RecentArchivedMembersResponse)
async def get_recent_archived_members(
    current_user: CustomUser = Depends(get_current_unit_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Get the most recent archive batch for the logged-in unit with summary stats."""
    data = await units_service.get_recent_archived_members_for_unit(db, current_user.id)
    return {
        "archive_year": data["archive_year"],
        "archive_reason": data["archive_reason"],
        "summary": data["summary"],
        "members": [
            ArchivedUnitMemberResponse.model_validate(member).model_dump(mode="json")
            for member in data["members"]
        ],
        "pending_concern_member_ids": data["pending_concern_member_ids"],
        "member_concerns": data["member_concerns"],
    }


@router.get("/archived-members", response_model=List[ArchivedUnitMemberResponse])
async def get_archived_members(
    current_user: CustomUser = Depends(get_current_unit_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Get list of archived members (legacy — prefer /archived-members/recent)."""
    stmt = select(ArchivedUnitMember).where(
        ArchivedUnitMember.registered_user_id == current_user.id
    ).order_by(ArchivedUnitMember.name)
    
    result = await db.execute(stmt)
    return list(result.scalars().all())


@router.post("/archived-member-concern-request", response_model=ArchivedMemberConcernRequestResponse)
async def create_archived_member_concern_request(
    data: ArchivedMemberConcernRequestCreate,
    current_user: CustomUser = Depends(get_current_unit_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Raise a concern about a recently archived member for admin review."""
    concern = await units_service.create_archived_member_concern_request(
        db, current_user.id, data
    )

    archived_stmt = select(ArchivedUnitMember).where(
        ArchivedUnitMember.id == data.archived_unit_member_id
    )
    archived_result = await db.execute(archived_stmt)
    archived_member = archived_result.scalar_one_or_none()

    unit_name_str = None
    if current_user.unit_name_id:
        unit_name_result = await db.execute(
            select(UnitName).where(UnitName.id == current_user.unit_name_id)
        )
        unit_name_obj = unit_name_result.scalar_one_or_none()
        unit_name_str = unit_name_obj.name if unit_name_obj else current_user.username

    settings_result = await db.execute(select(SiteSettings).limit(1))
    settings = settings_result.scalar_one_or_none()

    notify_admin_archived_member_concern(
        unit_name=unit_name_str or current_user.username,
        member_name=archived_member.name if archived_member else "Unknown",
        archive_year=archived_member.archive_year if archived_member else None,
        concern_text=data.concern_text,
        recipient_email=settings.contact_email if settings else None,
    )

    enriched = await units_service.get_archived_member_concern_requests(
        db, user_id=current_user.id
    )
    match = next((item for item in enriched if item["id"] == concern.id), None)
    if match:
        return match
    return concern


@router.get("/archived-member-concern-requests")
async def get_archived_member_concern_requests(
    current_user: CustomUser = Depends(get_current_unit_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Get archived member concern requests for the current unit."""
    return await units_service.get_archived_member_concern_requests(
        db, user_id=current_user.id
    )


@router.get("/my-requests")
async def get_my_requests(
    current_user: CustomUser = Depends(get_current_unit_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Aggregate all request types for the logged-in unit user."""
    return await units_service.get_unit_my_requests(db, current_user.id)


@router.get("/{unit_id}/my-requests")
async def get_my_requests_by_unit_id(
    unit_id: int,
    current_user: CustomUser = Depends(get_current_unit_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Aggregate all request types (legacy path with unit id)."""
    if unit_id != current_user.unit_name_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot view requests for another unit",
        )
    return await units_service.get_unit_my_requests(db, current_user.id)


@router.get("/transfer-destinations", response_model=List[TransferDestinationUnitResponse])
async def list_transfer_destinations(
    current_user: CustomUser = Depends(get_current_unit_user),
    db: AsyncSession = Depends(get_async_db),
):
    """List registered units available as transfer destinations."""
    return await units_service.get_transfer_destination_units(db, current_user.id)


@router.post("/transfer-request", response_model=UnitTransferRequestResponse)
async def create_transfer_request(
    unit_member_id: int = Form(...),
    destination_unit_id: int = Form(...),
    reason: str = Form(...),
    proof: UploadFile = File(..., description="Proof document (PDF, PNG, JPG — max 5 MB)"),
    current_user: CustomUser = Depends(get_current_unit_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Create a unit transfer request."""
    if not proof.filename:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Proof document is required",
        )

    proof_path, _ = save_upload_file(proof, subdir="units/transfer-requests")

    data = UnitTransferRequestCreate(
        unit_member_id=unit_member_id,
        destination_unit_id=destination_unit_id,
        reason=reason,
        proof=proof_path,
    )
    return await units_service.create_unit_transfer_request(db, current_user.id, data)


@router.get("/transfer-requests", response_model=List[UnitTransferRequestResponse])
async def get_transfer_requests(
    current_user: CustomUser = Depends(get_current_unit_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Get transfer requests for current user."""
    return await units_service.get_transfer_requests(db, user_id=current_user.id)


@router.post("/member-change-request", response_model=UnitMemberChangeRequestResponse)
async def create_member_change_request(
    unit_member_id: int = Form(...),
    reason: str = Form(...),
    proof: UploadFile = File(..., description="Proof document (PDF, PNG, JPG — max 5 MB)"),
    name: Optional[str] = Form(None),
    gender: Optional[str] = Form(None),
    dob: Optional[str] = Form(None),
    blood_group: Optional[str] = Form(None),
    qualification: Optional[str] = Form(None),
    number: Optional[str] = Form(None),
    residence_location: Optional[str] = Form(None),
    residence_state_id: Optional[str] = Form(None),
    residence_city_id: Optional[str] = Form(None),
    current_user: CustomUser = Depends(get_current_unit_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Create a member information change request."""
    if not proof.filename:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Proof document is required",
        )

    proof_path, _ = save_upload_file(proof, subdir="units/member-change-requests")

    parsed_dob = date.fromisoformat(dob) if dob else None

    residence_enum = None
    if residence_location:
        try:
            residence_enum = ResidenceLocation(residence_location)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Invalid residence location",
            ) from exc

    parsed_state_id = int(residence_state_id) if residence_state_id else None
    parsed_city_id = int(residence_city_id) if residence_city_id else None

    data = UnitMemberChangeRequestCreate(
        unit_member_id=unit_member_id,
        reason=reason,
        name=name or None,
        gender=gender or None,
        dob=parsed_dob,
        blood_group=blood_group or None,
        qualification=qualification or None,
        number=number or None,
        residence_location=residence_enum,
        residence_state_id=parsed_state_id,
        residence_city_id=parsed_city_id,
        proof=proof_path,
    )
    return await units_service.create_member_info_change_request(db, current_user.id, data)


@router.get("/member-change-requests", response_model=List[UnitMemberChangeRequestResponse])
async def get_member_change_requests(
    current_user: CustomUser = Depends(get_current_unit_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Get member change requests for current user."""
    return await units_service.get_member_change_requests(db, user_id=current_user.id)


@router.post("/officials-change-request", response_model=UnitOfficialsChangeRequestResponse)
async def create_officials_change_request(
    unit_official_id: int = Form(...),
    reason: str = Form(...),
    proof: UploadFile = File(..., description="Proof document (PDF, PNG, JPG — max 5 MB)"),
    president_designation: Optional[str] = Form(None),
    president_name: Optional[str] = Form(None),
    president_phone: Optional[str] = Form(None),
    vice_president_name: Optional[str] = Form(None),
    vice_president_phone: Optional[str] = Form(None),
    secretary_name: Optional[str] = Form(None),
    secretary_phone: Optional[str] = Form(None),
    joint_secretary_name: Optional[str] = Form(None),
    joint_secretary_phone: Optional[str] = Form(None),
    treasurer_name: Optional[str] = Form(None),
    treasurer_phone: Optional[str] = Form(None),
    current_user: CustomUser = Depends(get_current_unit_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Create an officials change request."""
    if not proof.filename:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Proof document is required",
        )

    proof_path, _ = save_upload_file(proof, subdir="units/officials-change-requests")

    data = UnitOfficialsChangeRequestCreate(
        unit_official_id=unit_official_id,
        reason=reason,
        president_designation=president_designation or None,
        president_name=president_name or None,
        president_phone=president_phone or None,
        vice_president_name=vice_president_name or None,
        vice_president_phone=vice_president_phone or None,
        secretary_name=secretary_name or None,
        secretary_phone=secretary_phone or None,
        joint_secretary_name=joint_secretary_name or None,
        joint_secretary_phone=joint_secretary_phone or None,
        treasurer_name=treasurer_name or None,
        treasurer_phone=treasurer_phone or None,
        proof=proof_path,
    )
    return await units_service.create_officials_change_request(db, current_user.id, data)


@router.get("/officials-change-requests", response_model=List[UnitOfficialsChangeRequestResponse])
async def get_officials_change_requests(
    current_user: CustomUser = Depends(get_current_unit_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Get officials change requests for current user."""
    return await units_service.get_officials_change_requests(db, user_id=current_user.id)


@router.post("/councilor-change-request", response_model=UnitCouncilorChangeRequestResponse)
async def create_councilor_change_request(
    unit_councilor_id: int = Form(...),
    unit_member_id: int = Form(...),
    reason: str = Form(...),
    proof: UploadFile = File(..., description="Proof document (PDF, PNG, JPG — max 5 MB)"),
    current_user: CustomUser = Depends(get_current_unit_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Create a councilor change request."""
    if not proof.filename:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Proof document is required",
        )

    proof_path, _ = save_upload_file(proof, subdir="units/councilor-change-requests")

    data = UnitCouncilorChangeRequestCreate(
        unit_councilor_id=unit_councilor_id,
        unit_member_id=unit_member_id,
        reason=reason,
        proof=proof_path,
    )
    return await units_service.create_councilor_change_request(db, current_user.id, data)


@router.get("/councilor-change-requests", response_model=List[UnitCouncilorChangeRequestResponse])
async def get_councilor_change_requests(
    current_user: CustomUser = Depends(get_current_unit_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Get councilor change requests for current user."""
    return await units_service.get_councilor_change_requests(db, user_id=current_user.id)


@router.post("/member-add-request", response_model=UnitMemberAddRequestResponse)
async def create_member_add_request(
    name: str = Form(...),
    gender: str = Form(...),
    dob: str = Form(...),
    number: str = Form(...),
    blood_group: str = Form(...),
    reason: str = Form(...),
    residence_location: str = Form(...),
    residence_state_id: Optional[str] = Form(None),
    residence_city_id: Optional[str] = Form(None),
    qualification: Optional[str] = Form(None),
    proof: Optional[UploadFile] = File(None, description="Proof document (PDF, PNG, JPG — max 5 MB)"),
    current_user: CustomUser = Depends(get_current_unit_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Create a request to add a new member."""
    proof_path = None
    if proof and proof.filename:
        proof_path, _ = save_upload_file(proof, subdir="units/member-add-requests")

    try:
        residence_enum = ResidenceLocation(residence_location)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid residence location",
        ) from exc

    parsed_state_id = int(residence_state_id) if residence_state_id else None
    parsed_city_id = int(residence_city_id) if residence_city_id else None

    data = UnitMemberAddRequestCreate(
        name=name,
        gender=gender,
        dob=date.fromisoformat(dob),
        number=number,
        blood_group=blood_group,
        reason=reason,
        qualification=qualification or None,
        proof=proof_path,
        residence_location=residence_enum,
        residence_state_id=parsed_state_id,
        residence_city_id=parsed_city_id,
    )
    return await units_service.create_member_add_request(db, current_user.id, data)


@router.put("/members/{member_id}", response_model=dict)
async def update_member(
    member_id: int,
    data: UnitMemberUpdate,
    current_user: CustomUser = Depends(get_current_unit_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Update a unit member."""
    cycle = await _get_wizard_cycle(db, current_user.id)

    # Get member
    stmt = select(UnitMembers).where(
        and_(
            UnitMembers.id == member_id,
            UnitMembers.registered_user_id == current_user.id
        )
    )
    result = await db.execute(stmt)
    member = result.scalar_one_or_none()
    
    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member not found"
        )

    cycle_service.validate_renewal_member_update(cycle, data, member)
    
    # Update fields
    if data.name is not None:
        member.name = data.name.upper()
    if data.gender is not None:
        member.gender = data.gender
    if data.dob is not None:
        member.dob = data.dob
    if data.number is not None:
        member.number = data.number
    if data.qualification is not None:
        member.qualification = data.qualification
    if data.blood_group is not None:
        member.blood_group = data.blood_group
    if data.residence_location is not None:
        residence_location, residence_state_id, residence_city_id = await residence_service.apply_residence_fields(
            db,
            residence_location=data.residence_location,
            residence_state_id=data.residence_state_id,
            residence_city_id=data.residence_city_id,
        )
        member.residence_location = residence_location
        member.residence_state_id = residence_state_id
        member.residence_city_id = residence_city_id
    
    await db.commit()
    
    return {"message": "Member updated successfully"}


@router.delete("/members/{member_id}", response_model=dict)
async def delete_member(
    member_id: int,
    current_user: CustomUser = Depends(get_current_unit_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Delete a unit member."""
    await _get_wizard_cycle(db, current_user.id)

    # Get member
    stmt = select(UnitMembers).where(
        and_(
            UnitMembers.id == member_id,
            UnitMembers.registered_user_id == current_user.id
        )
    )
    result = await db.execute(stmt)
    member = result.scalar_one_or_none()
    
    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member not found"
        )
    
    member_name = member.name
    member_phone = member.number
    
    # Remove from officials if present
    officials = await cycle_service.get_unit_officials_for_user(db, current_user.id)
    
    if officials:
        if officials.vice_president_name == member_name and officials.vice_president_phone == member_phone:
            officials.vice_president_name = None
            officials.vice_president_phone = None
        if officials.secretary_name == member_name and officials.secretary_phone == member_phone:
            officials.secretary_name = None
            officials.secretary_phone = None
        if officials.joint_secretary_name == member_name and officials.joint_secretary_phone == member_phone:
            officials.joint_secretary_name = None
            officials.joint_secretary_phone = None
        if officials.treasurer_name == member_name and officials.treasurer_phone == member_phone:
            officials.treasurer_name = None
            officials.treasurer_phone = None
    
    # Remove from councilors
    stmt = select(UnitCouncilor).where(
        and_(
            UnitCouncilor.unit_member_id == member_id,
            UnitCouncilor.registered_user_id == current_user.id
        )
    )
    result = await db.execute(stmt)
    councilors = list(result.scalars().all())
    
    for councilor in councilors:
        await db.delete(councilor)

    await units_service.remove_member_dependencies(db, [member_id])

    # Delete member
    await db.delete(member)
    await db.commit()
    
    return {"message": "Member removed successfully"}


@router.put("/officials", response_model=dict)
async def update_officials(
    data: UnitOfficialsUpdate,
    current_user: CustomUser = Depends(get_current_unit_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Update unit officials."""
    cycle = await _get_wizard_cycle(db, current_user.id)
    cycle_service.require_cycle_in_progress(cycle)
    return await add_unit_official(data, current_user, db)


@router.put("/councilors/{councilor_id}", response_model=dict)
async def update_councilor(
    councilor_id: int,
    data: UnitCouncilorCreate,
    current_user: CustomUser = Depends(get_current_unit_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Update a councilor."""
    cycle = await _get_wizard_cycle(db, current_user.id, require_in_progress=False)
    cycle_service.require_cycle_open_for_councilor_edits(cycle)

    # Get councilor
    stmt = select(UnitCouncilor).where(
        and_(
            UnitCouncilor.id == councilor_id,
            UnitCouncilor.registered_user_id == current_user.id
        )
    )
    result = await db.execute(stmt)
    councilor = result.scalar_one_or_none()
    
    if not councilor:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Councilor not found"
        )
    
    # Verify new member exists
    stmt = select(UnitMembers).where(
        and_(
            UnitMembers.id == data.unit_member_id,
            UnitMembers.registered_user_id == current_user.id
        )
    )
    result = await db.execute(stmt)
    member = result.scalar_one_or_none()
    
    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member not found"
        )
    
    councilor.unit_member_id = data.unit_member_id
    reopened = cycle_service.reopen_councilors_after_roster_change(cycle)
    await db.commit()

    message = "Councilor updated successfully"
    if reopened:
        message += ". Declaration was reopened — review councilors and submit again."
    return {"message": message}


@router.get("/finish-registration", response_model=dict)
async def get_finish_registration(
    current_user: CustomUser = Depends(get_current_unit_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Get final registration summary."""
    unit_details = await cycle_service.get_unit_details_for_user(db, current_user.id)
    unit_officials = await cycle_service.get_unit_officials_for_user(db, current_user.id)
    
    stmt = (
        select(UnitMembers)
        .options(*_MEMBER_RESIDENCE_OPTIONS)
        .where(UnitMembers.registered_user_id == current_user.id)
        .order_by(UnitMembers.name)
    )
    result = await db.execute(stmt)
    unit_members = list(result.scalars().all())
    
    stmt = select(UnitCouncilor).where(UnitCouncilor.registered_user_id == current_user.id)
    result = await db.execute(stmt)
    unit_councilors = list(result.scalars().all())
    
    members_count = len(unit_members)
    unit_registration_fee, unit_member_fee = await _get_unit_registration_fees(db)
    members_amount = members_count * unit_member_fee
    total_amount = members_amount + unit_registration_fee

    cycle, _, _ = await cycle_service.resolve_active_cycle(db, current_user.id)
    registration_year = (
        cycle.registration_year
        if cycle
        else (unit_details.registration_year if unit_details and unit_details.registration_year else await cycle_service.get_current_registration_year(db))
    )

    unit_details_payload = _serialize_or_none(UnitDetailsResponse, unit_details)
    if unit_details_payload is not None:
        unit_details_payload["registration_year"] = registration_year
    
    return {
        "unit_details": unit_details_payload,
        "unit_officials": _serialize_or_none(UnitOfficialsResponse, unit_officials),
        "unit_members": _serialize_list(UnitMemberResponse, unit_members),
        "unit_councilors": _serialize_list(UnitCouncilorResponse, unit_councilors),
        "councilors_count": len(unit_councilors),
        "members_count": members_count,
        "unit_registration_fee": unit_registration_fee,
        "unit_member_fee": unit_member_fee,
        "members_amount": members_amount,
        "total_amount": total_amount,
        "registration_year": registration_year,
    }


# ── Registration Payment endpoints ───────────────────────────────────────────


@router.get("/payment", response_model=dict)
async def get_payment_status(
    current_user: CustomUser = Depends(get_current_unit_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Return payment proof submissions for the active registration cycle.
    """
    cycle, _, _ = await cycle_service.resolve_active_cycle(db, current_user.id)
    if cycle is None:
        return {
            "overall_status": "not_submitted",
            "balance_amount": None,
            "latest_rejection_note": None,
            "qr_url": None,
            "registration_year": await cycle_service.get_current_registration_year(db),
            "submissions": [],
        }

    if await cycle_service.reconcile_cycle_fee_after_member_removals(
        db,
        registered_user_id=current_user.id,
        cycle=cycle,
    ):
        await db.commit()
        await db.refresh(cycle)

    payment_data = await cycle_service.get_payment_status_for_cycle(
        db, current_user.id, cycle.id
    )
    payments = payment_data["payments"]

    approved = [p for p in payments if p.status == PaymentProofStatus.APPROVED]
    if approved:
        latest = approved[-1]
        before_balance = latest.balance_amount
        cycle_service.recalculate_latest_approved_balance(cycle, approved)
        if latest.balance_amount != before_balance:
            await db.commit()
            payment_data = await cycle_service.get_payment_status_for_cycle(
                db, current_user.id, cycle.id
            )
            payments = payment_data["payments"]
            approved = [p for p in payments if p.status == PaymentProofStatus.APPROVED]

    payment_summary = cycle_service.build_payment_summary(cycle, approved)

    items = []
    for p in payments:
        file_url = get_public_file_url(p.file_path) if p.file_path else None
        items.append({
            "id": p.id,
            "file_url": file_url,
            "total_amount": p.total_amount,
            "balance_amount": p.balance_amount,
            "approved_paid_amount": p.approved_paid_amount,
            "detected_paid_amount": p.detected_paid_amount,
            "status": p.status.value,
            "rejection_note": p.rejection_note,
            "submitted_at": p.submitted_at.isoformat(),
            "reviewed_at": p.reviewed_at.isoformat() if p.reviewed_at else None,
        })

    site_stmt = select(SiteSettings).limit(1)
    site_result = await db.execute(site_stmt)
    site = site_result.scalar_one_or_none()
    qr_url = get_public_file_url(site.payment_qr_url) if site and site.payment_qr_url else None

    return {
        "overall_status": payment_data["overall_status"],
        "balance_amount": (
            payment_summary["balance_due"]
            if approved and payment_summary["balance_due"] > 0
            else payment_data["balance_amount"]
        ),
        "registration_total_amount": cycle.total_fee_at_submit,
        "registration_member_count": cycle.member_count_at_submit,
        "total_paid": payment_summary["total_paid"],
        "payment_credit": payment_summary["payment_credit"],
        "balance_due": payment_summary["balance_due"],
        "latest_rejection_note": payment_data["latest_rejection_note"],
        "qr_url": qr_url,
        "registration_year": cycle.registration_year,
        "submissions": items,
    }


@router.post("/payment", response_model=dict, status_code=201)
async def submit_payment_proof(
    file: UploadFile = File(..., description="Payment proof screenshot or PDF (max 5 MB)"),
    detected_amount: Optional[int] = Form(
        None,
        description="Client-detected paid amount in rupees (from browser OCR for images)",
    ),
    current_user: CustomUser = Depends(get_current_unit_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Upload a payment proof file for the unit registration fee.
    Only one pending submission is allowed per registration cycle at a time.
    Re-uploads are allowed after the admin approves or rejects the current proof.

    Images: pass detected_amount from browser Tesseract OCR when available.
    PDFs: backend runs OCR.space when configured and file is under 1 MB.
    """
    cycle, _, _ = await cycle_service.resolve_active_cycle(db, current_user.id)
    if cycle is None or cycle.status not in (
        cycle_service.DECLARATION_SUBMITTED,
        cycle_service.REGISTRATION_COMPLETED,
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Submit the registration declaration before uploading payment proof.",
        )

    if await cycle_service.cycle_is_fully_paid(db, cycle.id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Payment for this registration year has already been fully approved.",
        )

    if await cycle_service.reconcile_cycle_fee_after_member_removals(
        db,
        registered_user_id=current_user.id,
        cycle=cycle,
    ):
        await db.commit()
        await db.refresh(cycle)

    registration_total = cycle.total_fee_at_submit
    if registration_total is not None:
        await cycle_service.supersede_stale_pending_payments(
            db,
            cycle.id,
            registration_total=registration_total,
        )

    if await cycle_service.cycle_has_blocking_pending_payment(
        db,
        cycle.id,
        registration_total=registration_total,
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A payment proof is already awaiting admin review. Please wait for approval or rejection before submitting another.",
        )

    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is empty.",
        )

    suffix = Path(file.filename or "").suffix.lower()
    resolved_detected_amount: Optional[int] = None
    if detected_amount is not None:
        if detected_amount < 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Detected amount cannot be negative.",
            )
        resolved_detected_amount = detected_amount
    elif suffix == ".pdf":
        resolved_detected_amount = extract_amount_from_pdf_bytes(
            file_bytes,
            file.filename or "payment.pdf",
        )

    file.file = BytesIO(file_bytes)
    file.file.seek(0)

    # Upload file to B2
    object_key, _ = save_upload_file(file, subdir="units/registration-payments")

    # Calculate total amount for reference (prefer cycle snapshot after declaration)
    unit_fee, member_fee = await _get_unit_registration_fees(db)
    if cycle.total_fee_at_submit is not None:
        total = cycle.total_fee_at_submit
    else:
        member_count_result = await db.execute(
            select(UnitMembers).where(UnitMembers.registered_user_id == current_user.id)
        )
        member_count = len(list(member_count_result.scalars().all()))
        total = unit_fee + (member_count * member_fee)

    payment = UnitRegistrationPayment(
        registered_user_id=current_user.id,
        registration_cycle_id=cycle.id,
        file_path=object_key,
        total_amount=total,
        detected_paid_amount=resolved_detected_amount,
        status=PaymentProofStatus.PENDING,
    )
    db.add(payment)
    await db.commit()
    await db.refresh(payment)

    return {
        "id": payment.id,
        "status": payment.status.value,
        "detected_paid_amount": payment.detected_paid_amount,
        "submitted_at": payment.submitted_at.isoformat(),
        "message": "Payment proof submitted successfully. Awaiting admin review.",
    }
