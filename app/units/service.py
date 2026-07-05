"""Units service layer - business logic for unit operations."""

from datetime import date, datetime
from typing import List, Optional, Dict, Any
from sqlalchemy import delete, select, func, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from fastapi import HTTPException, status

from app.common.datetime_utils import now_ist
from app.common.phone_utils import phone_lookup_variants
from app.auth.models import (
    CustomUser,
    UnitDetails,
    UnitMembers,
    UnitOfficials,
    UnitCouncilor,
    UnitRegistrationData,
    UnitName,
    UserType,
)
from app.admin.models import SiteSettings
from app.units.models import (
    ArchivedUnitMember,
    ArchivedMemberConcernRequest,
    RemovedUnitMember,
    MemberRemovalType,
    UnitTransferRequest,
    UnitMemberChangeRequest,
    UnitOfficialsChangeRequest,
    UnitCouncilorChangeRequest,
    UnitMemberAddRequest,
    RequestStatus,
)
from app.units.schemas import (
    UnitTransferRequestCreate,
    UnitMemberChangeRequestCreate,
    UnitOfficialsChangeRequestCreate,
    UnitCouncilorChangeRequestCreate,
    UnitMemberAddRequestCreate,
    ArchivedMemberConcernRequestCreate,
)
from app.units import registration_cycle_service as cycle_service
from app.units import residence_service
from app.units.gender_utils import normalize_member_gender
from app.kalamela.models import (
    Appeal,
    AppealPayments,
    GroupEventParticipation,
    IndividualEventParticipation,
    IndividualEventScoreCard,
    KalamelaExcludeMembers,
)
from app.conference.models import ConferenceDelegate


async def remove_member_dependencies(db: AsyncSession, member_ids: List[int]) -> None:
    """Remove records that reference unit_members before archive/delete."""
    if not member_ids:
        return

    appeal_ids = select(Appeal.id).where(Appeal.added_by_id.in_(member_ids))
    individual_participation_ids = select(IndividualEventParticipation.id).where(
        IndividualEventParticipation.participant_id.in_(member_ids)
    )

    await db.execute(
        delete(AppealPayments).where(AppealPayments.appeal_id.in_(appeal_ids))
    )
    await db.execute(delete(Appeal).where(Appeal.added_by_id.in_(member_ids)))
    await db.execute(
        delete(IndividualEventScoreCard).where(
            or_(
                IndividualEventScoreCard.participant_id.in_(member_ids),
                IndividualEventScoreCard.event_participation_id.in_(
                    individual_participation_ids
                ),
            )
        )
    )
    await db.execute(
        delete(IndividualEventParticipation).where(
            IndividualEventParticipation.participant_id.in_(member_ids)
        )
    )
    await db.execute(
        delete(GroupEventParticipation).where(
            GroupEventParticipation.participant_id.in_(member_ids)
        )
    )
    await db.execute(
        delete(KalamelaExcludeMembers).where(
            KalamelaExcludeMembers.members_id.in_(member_ids)
        )
    )
    await db.execute(
        delete(UnitCouncilorChangeRequest).where(
            or_(
                UnitCouncilorChangeRequest.unit_member_id.in_(member_ids),
                UnitCouncilorChangeRequest.original_unit_member_id.in_(member_ids),
            )
        )
    )
    await db.execute(
        delete(UnitCouncilor).where(UnitCouncilor.unit_member_id.in_(member_ids))
    )
    await db.execute(
        delete(UnitMemberChangeRequest).where(
            UnitMemberChangeRequest.unit_member_id.in_(member_ids)
        )
    )
    await db.execute(
        delete(UnitTransferRequest).where(
            UnitTransferRequest.unit_member_id.in_(member_ids)
        )
    )
    await db.execute(
        delete(ConferenceDelegate).where(ConferenceDelegate.members_id.in_(member_ids))
    )
    await db.flush()


# Unit Transfer Request Functions
async def get_transfer_destination_units(
    db: AsyncSession,
    user_id: int,
) -> List[Dict[str, Any]]:
    """List registered units that can receive a member transfer."""
    current_user_result = await db.execute(
        select(CustomUser).where(CustomUser.id == user_id)
    )
    current_user = current_user_result.scalar_one_or_none()
    if not current_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    stmt = (
        select(CustomUser)
        .where(
            CustomUser.user_type == UserType.UNIT,
            CustomUser.is_active.is_(True),
            CustomUser.unit_name_id.isnot(None),
            CustomUser.id != user_id,
        )
        .options(
            selectinload(CustomUser.unit_name).selectinload(UnitName.district)
        )
        .order_by(CustomUser.username)
    )
    result = await db.execute(stmt)
    users = list(result.scalars().all())

    destinations: List[Dict[str, Any]] = []
    for user in users:
        if not user.unit_name_id or not user.unit_name:
            continue
        if current_user.unit_name_id and user.unit_name_id == current_user.unit_name_id:
            continue
        destinations.append(
            {
                "id": user.unit_name_id,
                "name": user.unit_name.name,
                "clergy_district": user.unit_name.district.name
                if user.unit_name.district
                else "Unknown",
                "unit_number": user.username,
            }
        )

    return destinations


async def create_unit_transfer_request(
    db: AsyncSession,
    user_id: int,
    data: UnitTransferRequestCreate,
) -> UnitTransferRequest:
    """
    Create a new unit transfer request.
    
    Args:
        db: Database session
        user_id: ID of the user creating the request
        data: Transfer request data
    
    Returns:
        Created transfer request
    
    Raises:
        HTTPException: If member or unit not found
    """
    # Verify member exists and belongs to user
    stmt = select(UnitMembers).where(
        and_(
            UnitMembers.id == data.unit_member_id,
            UnitMembers.registered_user_id == user_id
        )
    )
    result = await db.execute(stmt)
    member = result.scalar_one_or_none()
    
    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Unit member not found or does not belong to you"
        )
    
    # Verify destination unit exists
    stmt = select(UnitName).where(UnitName.id == data.destination_unit_id)
    result = await db.execute(stmt)
    destination_unit = result.scalar_one_or_none()
    
    if not destination_unit:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Destination unit not found"
        )
    
    # Get current unit and registered user
    stmt = select(CustomUser).where(CustomUser.id == user_id)
    result = await db.execute(stmt)
    user = result.scalar_one()
    
    # Create transfer request
    transfer_request = UnitTransferRequest(
        unit_member_id=data.unit_member_id,
        current_unit_id=user.unit_name_id,
        original_registered_user_id=user_id,
        destination_unit_id=data.destination_unit_id,
        reason=data.reason,
        proof=data.proof,
        status=RequestStatus.PENDING,
    )
    
    db.add(transfer_request)
    await db.commit()
    await db.refresh(transfer_request)
    
    return transfer_request


async def approve_unit_transfer_request(
    db: AsyncSession,
    request_id: int,
) -> UnitTransferRequest:
    """
    Approve a unit transfer request and update member's unit.
    
    Args:
        db: Database session
        request_id: ID of the transfer request
    
    Returns:
        Updated transfer request
    
    Raises:
        HTTPException: If request not found or not pending
    """
    # Get transfer request with member
    stmt = select(UnitTransferRequest).where(
        and_(
            UnitTransferRequest.id == request_id,
            UnitTransferRequest.status == RequestStatus.PENDING
        )
    )
    result = await db.execute(stmt)
    transfer_request = result.scalar_one_or_none()
    
    if not transfer_request:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Transfer request not found or already processed"
        )
    
    # Get the member
    stmt = select(UnitMembers).where(UnitMembers.id == transfer_request.unit_member_id)
    result = await db.execute(stmt)
    member = result.scalar_one()
    
    # Get new registered user for destination unit
    stmt = select(CustomUser).where(CustomUser.unit_name_id == transfer_request.destination_unit_id)
    result = await db.execute(stmt)
    new_registered_user = result.scalar_one_or_none()
    
    if not new_registered_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No registered user found for destination unit"
        )
    
    # Update member's registered user
    member.registered_user_id = new_registered_user.id
    
    # Update transfer request status
    transfer_request.status = RequestStatus.APPROVED
    
    await db.commit()
    await db.refresh(transfer_request)
    
    return transfer_request


async def revert_unit_transfer_request(
    db: AsyncSession,
    request_id: int,
) -> UnitTransferRequest:
    """
    Revert an approved unit transfer request.
    
    Args:
        db: Database session
        request_id: ID of the transfer request
    
    Returns:
        Reverted transfer request
    
    Raises:
        HTTPException: If request not found or not approved
    """
    # Get approved transfer request
    stmt = select(UnitTransferRequest).where(
        and_(
            UnitTransferRequest.id == request_id,
            UnitTransferRequest.status == RequestStatus.APPROVED
        )
    )
    result = await db.execute(stmt)
    transfer_request = result.scalar_one_or_none()
    
    if not transfer_request:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Transfer request not found or not approved"
        )
    
    if not transfer_request.original_registered_user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No original registered user to revert to"
        )
    
    # Get the member
    stmt = select(UnitMembers).where(UnitMembers.id == transfer_request.unit_member_id)
    result = await db.execute(stmt)
    member = result.scalar_one()
    
    # Restore original registered user
    member.registered_user_id = transfer_request.original_registered_user_id
    
    # Update status back to pending
    transfer_request.status = RequestStatus.PENDING
    
    await db.commit()
    await db.refresh(transfer_request)
    
    return transfer_request


async def reject_unit_transfer_request(
    db: AsyncSession,
    request_id: int,
) -> UnitTransferRequest:
    """
    Reject a pending unit transfer request.
    
    Args:
        db: Database session
        request_id: ID of the transfer request
    
    Returns:
        Rejected transfer request
    
    Raises:
        HTTPException: If request not found or not pending
    """
    # Get pending transfer request
    stmt = select(UnitTransferRequest).where(
        and_(
            UnitTransferRequest.id == request_id,
            UnitTransferRequest.status == RequestStatus.PENDING
        )
    )
    result = await db.execute(stmt)
    transfer_request = result.scalar_one_or_none()
    
    if not transfer_request:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Transfer request not found or not pending"
        )
    
    # Update status to rejected
    transfer_request.status = RequestStatus.REJECTED
    
    await db.commit()
    await db.refresh(transfer_request)
    
    return transfer_request


# Member Info Change Request Functions
OFFICIAL_IDENTITY_FIELDS = (
    ("president_name", "president_phone"),
    ("vice_president_name", "vice_president_phone"),
    ("secretary_name", "secretary_phone"),
    ("joint_secretary_name", "joint_secretary_phone"),
    ("treasurer_name", "treasurer_phone"),
)


def _phones_differ(current: Optional[str], requested: Optional[str]) -> bool:
    if not requested:
        return False
    if not current:
        return True
    current_variants = set(phone_lookup_variants(current))
    requested_variants = set(phone_lookup_variants(requested))
    return current_variants.isdisjoint(requested_variants)


def _official_matches_member_identity(
    official_name: Optional[str],
    official_phone: Optional[str],
    member_name: str,
    member_phone: Optional[str],
) -> bool:
    if not official_name or not member_name:
        return False
    if official_name.strip().upper() != member_name.strip().upper():
        return False
    if member_phone and official_phone:
        return not _phones_differ(official_phone, member_phone)
    return official_phone == member_phone


async def _sync_member_identity_in_officials(
    db: AsyncSession,
    registered_user_id: int,
    previous_name: str,
    previous_phone: Optional[str],
    updated_name: str,
    updated_phone: Optional[str],
) -> None:
    """Keep denormalized unit official rows in sync when a member's identity changes."""
    if (
        previous_name.strip().upper() == updated_name.strip().upper()
        and not _phones_differ(previous_phone, updated_phone)
    ):
        return

    stmt = select(UnitOfficials).where(UnitOfficials.registered_user_id == registered_user_id)
    result = await db.execute(stmt)
    officials = result.scalar_one_or_none()
    if not officials:
        return

    normalized_name = updated_name.strip().upper()
    for name_field, phone_field in OFFICIAL_IDENTITY_FIELDS:
        current_name = getattr(officials, name_field)
        current_phone = getattr(officials, phone_field)
        if _official_matches_member_identity(
            current_name,
            current_phone,
            previous_name,
            previous_phone,
        ):
            setattr(officials, name_field, normalized_name)
            if updated_phone is not None:
                setattr(officials, phone_field, updated_phone)


async def create_member_info_change_request(
    db: AsyncSession,
    user_id: int,
    data: UnitMemberChangeRequestCreate,
) -> UnitMemberChangeRequest:
    """
    Create a member information change request.
    
    Args:
        db: Database session
        user_id: ID of the user creating the request
        data: Change request data
    
    Returns:
        Created change request
    
    Raises:
        HTTPException: If member not found or no changes detected
    """
    # Verify member exists and belongs to user
    stmt = select(UnitMembers).where(
        and_(
            UnitMembers.id == data.unit_member_id,
            UnitMembers.registered_user_id == user_id
        )
    )
    result = await db.execute(stmt)
    member = result.scalar_one_or_none()
    
    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Unit member not found or does not belong to you"
        )
    
    resolved_residence = None
    if data.residence_location is not None:
        resolved_residence = await residence_service.apply_residence_fields(
            db,
            residence_location=data.residence_location,
            residence_state_id=data.residence_state_id,
            residence_city_id=data.residence_city_id,
        )

    residence_changed = False
    if resolved_residence is not None:
        new_location, new_state_id, new_city_id = resolved_residence
        residence_changed = (
            new_location != member.residence_location
            or new_state_id != member.residence_state_id
            or new_city_id != member.residence_city_id
        )
    
    # Check if any changes were requested
    number_changed = _phones_differ(member.number, data.number)
    has_changes = (
        (data.name and data.name != member.name) or
        (data.gender and data.gender != member.gender) or
        (data.dob and data.dob != member.dob) or
        (data.blood_group and data.blood_group != member.blood_group) or
        (data.qualification and data.qualification != member.qualification) or
        number_changed or
        residence_changed
    )
    
    if not has_changes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No changes detected in the provided information"
        )
    
    # Create change request
    change_request = UnitMemberChangeRequest(
        unit_member_id=data.unit_member_id,
        name=data.name if data.name and data.name != member.name else None,
        original_name=member.name,
        gender=data.gender if data.gender and data.gender != member.gender else None,
        original_gender=member.gender,
        dob=data.dob if data.dob and data.dob != member.dob else None,
        original_dob=member.dob,
        blood_group=data.blood_group if data.blood_group and data.blood_group != member.blood_group else None,
        original_blood_group=member.blood_group,
        qualification=data.qualification if data.qualification and data.qualification != member.qualification else None,
        original_qualification=member.qualification,
        number=data.number if number_changed else None,
        original_number=member.number,
        residence_location=resolved_residence[0] if residence_changed and resolved_residence else None,
        residence_state_id=resolved_residence[1] if residence_changed and resolved_residence else None,
        residence_city_id=resolved_residence[2] if residence_changed and resolved_residence else None,
        original_residence_location=member.residence_location if residence_changed else None,
        original_residence_state_id=member.residence_state_id if residence_changed else None,
        original_residence_city_id=member.residence_city_id if residence_changed else None,
        reason=data.reason,
        proof=data.proof,
        status=RequestStatus.PENDING,
    )
    
    db.add(change_request)
    await db.commit()
    await db.refresh(change_request)
    
    return change_request


async def approve_member_info_change(
    db: AsyncSession,
    request_id: int,
) -> UnitMemberChangeRequest:
    """
    Approve a member information change request and apply changes.
    
    Args:
        db: Database session
        request_id: ID of the change request
    
    Returns:
        Updated change request
    
    Raises:
        HTTPException: If request not found or not pending
    """
    # Get change request
    stmt = select(UnitMemberChangeRequest).where(
        and_(
            UnitMemberChangeRequest.id == request_id,
            UnitMemberChangeRequest.status == RequestStatus.PENDING
        )
    )
    result = await db.execute(stmt)
    change_request = result.scalar_one_or_none()
    
    if not change_request:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Change request not found or already processed"
        )
    
    # Get the member
    stmt = select(UnitMembers).where(UnitMembers.id == change_request.unit_member_id)
    result = await db.execute(stmt)
    member = result.scalar_one()

    previous_name = member.name
    previous_phone = member.number
    
    # Apply changes
    if change_request.name:
        member.name = change_request.name
    if change_request.gender:
        member.gender = normalize_member_gender(change_request.gender)
    if change_request.dob:
        member.dob = change_request.dob
    if change_request.blood_group:
        member.blood_group = change_request.blood_group
    if change_request.qualification:
        member.qualification = change_request.qualification
    if change_request.number:
        member.number = change_request.number
    if change_request.residence_location is not None:
        member.residence_location = change_request.residence_location
        member.residence_state_id = change_request.residence_state_id
        member.residence_city_id = change_request.residence_city_id

    if change_request.name or change_request.number:
        await _sync_member_identity_in_officials(
            db,
            member.registered_user_id,
            previous_name,
            previous_phone,
            member.name,
            member.number,
        )
    
    # Update status
    change_request.status = RequestStatus.APPROVED
    
    await db.commit()
    await db.refresh(change_request)
    
    return change_request


async def revert_member_info_change(
    db: AsyncSession,
    request_id: int,
) -> UnitMemberChangeRequest:
    """
    Revert an approved member information change request.
    
    Args:
        db: Database session
        request_id: ID of the change request
    
    Returns:
        Reverted change request
    
    Raises:
        HTTPException: If request not found or not approved
    """
    # Get approved change request
    stmt = select(UnitMemberChangeRequest).where(
        and_(
            UnitMemberChangeRequest.id == request_id,
            UnitMemberChangeRequest.status == RequestStatus.APPROVED
        )
    )
    result = await db.execute(stmt)
    change_request = result.scalar_one_or_none()
    
    if not change_request:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Change request not found or not approved"
        )
    
    # Get the member
    stmt = select(UnitMembers).where(UnitMembers.id == change_request.unit_member_id)
    result = await db.execute(stmt)
    member = result.scalar_one()

    previous_name = member.name
    previous_phone = member.number
    
    # Restore original values
    if change_request.original_name is not None:
        member.name = change_request.original_name
    if change_request.original_gender is not None:
        member.gender = change_request.original_gender
    if change_request.original_dob is not None:
        member.dob = change_request.original_dob
    if change_request.original_blood_group is not None:
        member.blood_group = change_request.original_blood_group
    if change_request.original_qualification is not None:
        member.qualification = change_request.original_qualification
    if change_request.original_number is not None:
        member.number = change_request.original_number
    if change_request.original_residence_location is not None or change_request.residence_location is not None:
        member.residence_location = change_request.original_residence_location
        member.residence_state_id = change_request.original_residence_state_id
        member.residence_city_id = change_request.original_residence_city_id

    if change_request.name or change_request.number:
        await _sync_member_identity_in_officials(
            db,
            member.registered_user_id,
            previous_name,
            previous_phone,
            member.name,
            member.number,
        )
    
    # Update status back to pending
    change_request.status = RequestStatus.PENDING
    
    await db.commit()
    await db.refresh(change_request)
    
    return change_request


async def reject_member_info_change(
    db: AsyncSession,
    request_id: int,
) -> UnitMemberChangeRequest:
    """
    Reject a pending member information change request.
    
    Args:
        db: Database session
        request_id: ID of the change request
    
    Returns:
        Rejected change request
    
    Raises:
        HTTPException: If request not found or not pending
    """
    # Get pending change request
    stmt = select(UnitMemberChangeRequest).where(
        and_(
            UnitMemberChangeRequest.id == request_id,
            UnitMemberChangeRequest.status == RequestStatus.PENDING
        )
    )
    result = await db.execute(stmt)
    change_request = result.scalar_one_or_none()
    
    if not change_request:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Change request not found or not pending"
        )
    
    # Update status to rejected
    change_request.status = RequestStatus.REJECTED
    
    await db.commit()
    await db.refresh(change_request)
    
    return change_request


# Officials Change Request Functions
async def create_officials_change_request(
    db: AsyncSession,
    user_id: int,
    data: UnitOfficialsChangeRequestCreate,
) -> UnitOfficialsChangeRequest:
    """
    Create an officials information change request.
    
    Args:
        db: Database session
        user_id: ID of the user creating the request
        data: Change request data
    
    Returns:
        Created change request
    
    Raises:
        HTTPException: If officials not found or no changes detected
    """
    # Verify officials exist and belong to user
    stmt = select(UnitOfficials).where(
        and_(
            UnitOfficials.id == data.unit_official_id,
            UnitOfficials.registered_user_id == user_id
        )
    )
    result = await db.execute(stmt)
    officials = result.scalar_one_or_none()
    
    if not officials:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Unit officials not found or do not belong to you"
        )
    
    # Check for changes
    has_changes = any([
        data.president_designation and data.president_designation != officials.president_designation,
        data.president_name and data.president_name != officials.president_name,
        data.president_phone and data.president_phone != officials.president_phone,
        data.vice_president_name and data.vice_president_name != officials.vice_president_name,
        data.vice_president_phone and data.vice_president_phone != officials.vice_president_phone,
        data.secretary_name and data.secretary_name != officials.secretary_name,
        data.secretary_phone and data.secretary_phone != officials.secretary_phone,
        data.joint_secretary_name and data.joint_secretary_name != officials.joint_secretary_name,
        data.joint_secretary_phone and data.joint_secretary_phone != officials.joint_secretary_phone,
        data.treasurer_name and data.treasurer_name != officials.treasurer_name,
        data.treasurer_phone and data.treasurer_phone != officials.treasurer_phone,
    ])
    
    if not has_changes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No changes detected in the provided information"
        )
    
    # Create change request with all fields
    change_request = UnitOfficialsChangeRequest(
        unit_official_id=data.unit_official_id,
        president_designation=data.president_designation if data.president_designation and data.president_designation != officials.president_designation else None,
        original_president_designation=officials.president_designation,
        president_name=data.president_name if data.president_name and data.president_name != officials.president_name else None,
        original_president_name=officials.president_name,
        president_phone=data.president_phone if data.president_phone and data.president_phone != officials.president_phone else None,
        original_president_phone=officials.president_phone,
        vice_president_name=data.vice_president_name if data.vice_president_name and data.vice_president_name != officials.vice_president_name else None,
        original_vice_president_name=officials.vice_president_name,
        vice_president_phone=data.vice_president_phone if data.vice_president_phone and data.vice_president_phone != officials.vice_president_phone else None,
        original_vice_president_phone=officials.vice_president_phone,
        secretary_name=data.secretary_name if data.secretary_name and data.secretary_name != officials.secretary_name else None,
        original_secretary_name=officials.secretary_name,
        secretary_phone=data.secretary_phone if data.secretary_phone and data.secretary_phone != officials.secretary_phone else None,
        original_secretary_phone=officials.secretary_phone,
        joint_secretary_name=data.joint_secretary_name if data.joint_secretary_name and data.joint_secretary_name != officials.joint_secretary_name else None,
        original_joint_secretary_name=officials.joint_secretary_name,
        joint_secretary_phone=data.joint_secretary_phone if data.joint_secretary_phone and data.joint_secretary_phone != officials.joint_secretary_phone else None,
        original_joint_secretary_phone=officials.joint_secretary_phone,
        treasurer_name=data.treasurer_name if data.treasurer_name and data.treasurer_name != officials.treasurer_name else None,
        original_treasurer_name=officials.treasurer_name,
        treasurer_phone=data.treasurer_phone if data.treasurer_phone and data.treasurer_phone != officials.treasurer_phone else None,
        original_treasurer_phone=officials.treasurer_phone,
        reason=data.reason,
        proof=data.proof,
        status=RequestStatus.PENDING,
    )
    
    db.add(change_request)
    await db.commit()
    await db.refresh(change_request)
    
    return change_request


async def approve_officials_change(
    db: AsyncSession,
    request_id: int,
) -> UnitOfficialsChangeRequest:
    """
    Approve an officials change request and apply changes.
    
    Args:
        db: Database session
        request_id: ID of the change request
    
    Returns:
        Updated change request
    
    Raises:
        HTTPException: If request not found or not pending
    """
    # Get change request
    stmt = select(UnitOfficialsChangeRequest).where(
        and_(
            UnitOfficialsChangeRequest.id == request_id,
            UnitOfficialsChangeRequest.status == RequestStatus.PENDING
        )
    )
    result = await db.execute(stmt)
    change_request = result.scalar_one_or_none()
    
    if not change_request:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Change request not found or already processed"
        )
    
    # Get the officials
    stmt = select(UnitOfficials).where(UnitOfficials.id == change_request.unit_official_id)
    result = await db.execute(stmt)
    officials = result.scalar_one()
    
    # Apply changes
    if change_request.president_designation:
        officials.president_designation = change_request.president_designation
    if change_request.president_name:
        officials.president_name = change_request.president_name
    if change_request.president_phone:
        officials.president_phone = change_request.president_phone
    if change_request.vice_president_name:
        officials.vice_president_name = change_request.vice_president_name
    if change_request.vice_president_phone:
        officials.vice_president_phone = change_request.vice_president_phone
    if change_request.secretary_name:
        officials.secretary_name = change_request.secretary_name
    if change_request.secretary_phone:
        officials.secretary_phone = change_request.secretary_phone
    if change_request.joint_secretary_name:
        officials.joint_secretary_name = change_request.joint_secretary_name
    if change_request.joint_secretary_phone:
        officials.joint_secretary_phone = change_request.joint_secretary_phone
    if change_request.treasurer_name:
        officials.treasurer_name = change_request.treasurer_name
    if change_request.treasurer_phone:
        officials.treasurer_phone = change_request.treasurer_phone
    
    # Update status
    change_request.status = RequestStatus.APPROVED
    
    await db.commit()
    await db.refresh(change_request)
    
    return change_request


async def revert_officials_change(
    db: AsyncSession,
    request_id: int,
) -> UnitOfficialsChangeRequest:
    """
    Revert an approved officials change request.
    
    Args:
        db: Database session
        request_id: ID of the change request
    
    Returns:
        Reverted change request
    
    Raises:
        HTTPException: If request not found or not approved
    """
    # Get approved change request
    stmt = select(UnitOfficialsChangeRequest).where(
        and_(
            UnitOfficialsChangeRequest.id == request_id,
            UnitOfficialsChangeRequest.status == RequestStatus.APPROVED
        )
    )
    result = await db.execute(stmt)
    change_request = result.scalar_one_or_none()
    
    if not change_request:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Change request not found or not approved"
        )
    
    # Get the officials
    stmt = select(UnitOfficials).where(UnitOfficials.id == change_request.unit_official_id)
    result = await db.execute(stmt)
    officials = result.scalar_one()
    
    # Restore original values
    if change_request.original_president_designation is not None:
        officials.president_designation = change_request.original_president_designation
    if change_request.original_president_name is not None:
        officials.president_name = change_request.original_president_name
    if change_request.original_president_phone is not None:
        officials.president_phone = change_request.original_president_phone
    if change_request.original_vice_president_name is not None:
        officials.vice_president_name = change_request.original_vice_president_name
    if change_request.original_vice_president_phone is not None:
        officials.vice_president_phone = change_request.original_vice_president_phone
    if change_request.original_secretary_name is not None:
        officials.secretary_name = change_request.original_secretary_name
    if change_request.original_secretary_phone is not None:
        officials.secretary_phone = change_request.original_secretary_phone
    if change_request.original_joint_secretary_name is not None:
        officials.joint_secretary_name = change_request.original_joint_secretary_name
    if change_request.original_joint_secretary_phone is not None:
        officials.joint_secretary_phone = change_request.original_joint_secretary_phone
    if change_request.original_treasurer_name is not None:
        officials.treasurer_name = change_request.original_treasurer_name
    if change_request.original_treasurer_phone is not None:
        officials.treasurer_phone = change_request.original_treasurer_phone
    
    # Update status back to pending
    change_request.status = RequestStatus.PENDING
    
    await db.commit()
    await db.refresh(change_request)
    
    return change_request


async def reject_officials_change(
    db: AsyncSession,
    request_id: int,
) -> UnitOfficialsChangeRequest:
    """
    Reject an officials change request.
    
    Args:
        db: Database session
        request_id: ID of the change request
    
    Returns:
        Rejected change request
    
    Raises:
        HTTPException: If request not found
    """
    stmt = select(UnitOfficialsChangeRequest).where(
        UnitOfficialsChangeRequest.id == request_id
    )
    result = await db.execute(stmt)
    change_request = result.scalar_one_or_none()
    
    if not change_request:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Change request not found"
        )
    
    change_request.status = RequestStatus.REJECTED
    
    await db.commit()
    await db.refresh(change_request)
    
    return change_request


# Councilor Change Request Functions
async def create_councilor_change_request(
    db: AsyncSession,
    user_id: int,
    data: UnitCouncilorChangeRequestCreate,
) -> UnitCouncilorChangeRequest:
    """
    Create a councilor change request.
    
    Args:
        db: Database session
        user_id: ID of the user creating the request
        data: Change request data
    
    Returns:
        Created change request
    
    Raises:
        HTTPException: If councilor not found or no changes detected
    """
    # Verify councilor exists and belongs to user
    stmt = select(UnitCouncilor).where(
        and_(
            UnitCouncilor.id == data.unit_councilor_id,
            UnitCouncilor.registered_user_id == user_id
        )
    )
    result = await db.execute(stmt)
    councilor = result.scalar_one_or_none()
    
    if not councilor:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Unit councilor not found or does not belong to you"
        )
    
    # Check if new member is different
    if data.unit_member_id and data.unit_member_id == councilor.unit_member_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No changes detected in the councilor assignment"
        )
    
    # If new member specified, verify it exists and belongs to user
    new_member = None
    if data.unit_member_id:
        stmt = select(UnitMembers).where(
            and_(
                UnitMembers.id == data.unit_member_id,
                UnitMembers.registered_user_id == user_id
            )
        )
        result = await db.execute(stmt)
        new_member = result.scalar_one_or_none()
        
        if not new_member:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="New unit member not found or does not belong to you"
            )

    original_member_stmt = select(UnitMembers).where(
        UnitMembers.id == councilor.unit_member_id
    )
    original_member_result = await db.execute(original_member_stmt)
    original_member = original_member_result.scalar_one()
    
    # Create change request
    change_request = UnitCouncilorChangeRequest(
        unit_councilor_id=data.unit_councilor_id,
        unit_member_id=data.unit_member_id,
        original_unit_member_id=councilor.unit_member_id,
        original_member_name=original_member.name,
        new_member_name=new_member.name if new_member else None,
        reason=data.reason,
        proof=data.proof,
        status=RequestStatus.PENDING,
    )
    
    db.add(change_request)
    await db.commit()
    await db.refresh(change_request)
    
    return change_request


async def approve_councilor_change(
    db: AsyncSession,
    request_id: int,
) -> UnitCouncilorChangeRequest:
    """
    Approve a councilor change request and apply changes.
    
    Args:
        db: Database session
        request_id: ID of the change request
    
    Returns:
        Updated change request
    
    Raises:
        HTTPException: If request not found or not pending
    """
    # Get change request
    stmt = select(UnitCouncilorChangeRequest).where(
        and_(
            UnitCouncilorChangeRequest.id == request_id,
            UnitCouncilorChangeRequest.status == RequestStatus.PENDING
        )
    )
    result = await db.execute(stmt)
    change_request = result.scalar_one_or_none()
    
    if not change_request:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Change request not found or already processed"
        )
    
    if not change_request.unit_member_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No new member specified in request"
        )

    if not change_request.unit_councilor_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The councilor for this request was removed and can no longer be updated",
        )
    
    # Get the councilor
    stmt = select(UnitCouncilor).where(UnitCouncilor.id == change_request.unit_councilor_id)
    result = await db.execute(stmt)
    councilor = result.scalar_one_or_none()
    if not councilor:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The councilor for this request was removed and can no longer be updated",
        )
    
    # Apply change
    councilor.unit_member_id = change_request.unit_member_id
    
    # Update status
    change_request.status = RequestStatus.APPROVED
    
    await db.commit()
    await db.refresh(change_request)
    
    return change_request


async def revert_councilor_change(
    db: AsyncSession,
    request_id: int,
) -> UnitCouncilorChangeRequest:
    """
    Revert an approved councilor change request.
    
    Args:
        db: Database session
        request_id: ID of the change request
    
    Returns:
        Reverted change request
    
    Raises:
        HTTPException: If request not found or not approved
    """
    # Get approved change request
    stmt = select(UnitCouncilorChangeRequest).where(
        and_(
            UnitCouncilorChangeRequest.id == request_id,
            UnitCouncilorChangeRequest.status == RequestStatus.APPROVED
        )
    )
    result = await db.execute(stmt)
    change_request = result.scalar_one_or_none()
    
    if not change_request:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Change request not found or not approved"
        )
    
    if not change_request.original_unit_member_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No original member to revert to"
        )

    if not change_request.unit_councilor_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The councilor for this request was removed and can no longer be reverted",
        )
    
    # Get the councilor
    stmt = select(UnitCouncilor).where(UnitCouncilor.id == change_request.unit_councilor_id)
    result = await db.execute(stmt)
    councilor = result.scalar_one_or_none()
    if not councilor:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The councilor for this request was removed and can no longer be reverted",
        )
    
    # Restore original member
    councilor.unit_member_id = change_request.original_unit_member_id
    
    # Update status back to pending
    change_request.status = RequestStatus.PENDING
    
    await db.commit()
    await db.refresh(change_request)
    
    return change_request


async def reject_councilor_change(
    db: AsyncSession,
    request_id: int,
) -> UnitCouncilorChangeRequest:
    """
    Reject a councilor change request.
    
    Args:
        db: Database session
        request_id: ID of the change request
    
    Returns:
        Rejected change request
    
    Raises:
        HTTPException: If request not found
    """
    stmt = select(UnitCouncilorChangeRequest).where(
        UnitCouncilorChangeRequest.id == request_id
    )
    result = await db.execute(stmt)
    change_request = result.scalar_one_or_none()
    
    if not change_request:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Change request not found"
        )
    
    change_request.status = RequestStatus.REJECTED
    
    await db.commit()
    await db.refresh(change_request)
    
    return change_request


# Member Add Request Functions
async def create_member_add_request(
    db: AsyncSession,
    user_id: int,
    data: UnitMemberAddRequestCreate,
) -> UnitMemberAddRequest:
    """
    Create a request to add a new member.
    
    Args:
        db: Database session
        user_id: ID of the user creating the request
        data: Add request data
    
    Returns:
        Created add request
    """
    residence_location, residence_state_id, residence_city_id = await residence_service.apply_residence_fields(
        db,
        residence_location=data.residence_location,
        residence_state_id=data.residence_state_id,
        residence_city_id=data.residence_city_id,
    )

    add_request = UnitMemberAddRequest(
        registered_user_id=user_id,
        name=data.name,
        gender=data.gender,
        dob=data.dob,
        number=data.number,
        qualification=data.qualification,
        blood_group=data.blood_group,
        reason=data.reason,
        proof=data.proof,
        status=RequestStatus.PENDING,
        residence_location=residence_location,
        residence_state_id=residence_state_id,
        residence_city_id=residence_city_id,
    )
    
    db.add(add_request)
    await db.commit()
    await db.refresh(add_request)
    
    return add_request


async def approve_member_add_request(
    db: AsyncSession,
    request_id: int,
) -> Dict[str, Any]:
    """
    Approve a member add request and create the member.
    
    Args:
        db: Database session
        request_id: ID of the add request
    
    Returns:
        Approved add request
    
    Raises:
        HTTPException: If request not found or not pending
    """
    # Get add request
    stmt = select(UnitMemberAddRequest).where(
        and_(
            UnitMemberAddRequest.id == request_id,
            UnitMemberAddRequest.status == RequestStatus.PENDING
        )
    )
    result = await db.execute(stmt)
    add_request = result.scalar_one_or_none()
    
    if not add_request:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Add request not found or already processed"
        )
    
    current_year = await cycle_service.get_current_registration_year(db)
    cycle = await cycle_service.get_cycle(db, add_request.registered_user_id, current_year)

    new_member = UnitMembers(
        registered_user_id=add_request.registered_user_id,
        name=add_request.name,
        gender=normalize_member_gender(add_request.gender),
        dob=add_request.dob,
        number=add_request.number,
        qualification=add_request.qualification,
        blood_group=add_request.blood_group,
        added_registration_cycle_id=cycle.id if cycle else None,
        residence_location=add_request.residence_location,
        residence_state_id=add_request.residence_state_id,
        residence_city_id=add_request.residence_city_id,
    )

    db.add(new_member)
    add_request.status = RequestStatus.APPROVED

    await cycle_service.adjust_fee_for_member_delta(
        db,
        registered_user_id=add_request.registered_user_id,
        delta_members=1,
    )

    await db.commit()
    await db.refresh(add_request)

    labels = await _lookup_unit_labels_for_users(db, [add_request.registered_user_id])
    unit_name, username = labels.get(add_request.registered_user_id, (None, None))
    return _member_add_request_dict(add_request, unit_name=unit_name, username=username)


async def reject_member_add_request(
    db: AsyncSession,
    request_id: int,
) -> Dict[str, Any]:
    """
    Reject a member add request.
    
    Args:
        db: Database session
        request_id: ID of the add request
    
    Returns:
        Rejected add request
    
    Raises:
        HTTPException: If request not found
    """
    stmt = select(UnitMemberAddRequest).where(
        UnitMemberAddRequest.id == request_id
    )
    result = await db.execute(stmt)
    add_request = result.scalar_one_or_none()
    
    if not add_request:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Add request not found"
        )
    
    add_request.status = RequestStatus.REJECTED
    
    await db.commit()
    await db.refresh(add_request)

    labels = await _lookup_unit_labels_for_users(db, [add_request.registered_user_id])
    unit_name, username = labels.get(add_request.registered_user_id, (None, None))
    return _member_add_request_dict(add_request, unit_name=unit_name, username=username)


# Admin member removal (distinct from seasonal archival → archived_unit_member)
async def _get_member_dob_limits(db: AsyncSession) -> tuple[date, date]:
    ss_result = await db.execute(select(SiteSettings))
    ss = ss_result.scalar_one_or_none()
    min_dob = ss.member_min_dob if ss and ss.member_min_dob else date(1990, 1, 1)
    max_dob = ss.member_max_dob if ss and ss.member_max_dob else date(2011, 12, 31)
    return min_dob, max_dob


def _member_is_archive_eligible(member: UnitMembers, min_dob: date, max_dob: date) -> bool:
    return member.dob < min_dob or member.dob > max_dob


async def _validate_admin_removal_not_archival(
    db: AsyncSession,
    members: List[UnitMembers],
    confirm_not_archival: bool,
) -> None:
    """
    Prevent accidental use of admin removal for members who should use
    the seasonal Archive Members workflow instead.
    """
    if not members:
        return

    min_dob, max_dob = await _get_member_dob_limits(db)
    archive_eligible = [m for m in members if _member_is_archive_eligible(m, min_dob, max_dob)]
    if archive_eligible and not confirm_not_archival:
        preview = ", ".join(m.name for m in archive_eligible[:3])
        extra = f" (+{len(archive_eligible) - 3} more)" if len(archive_eligible) > 3 else ""
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Member(s) eligible for seasonal archiving: {preview}{extra}. "
                "Use Admin → Archive Members for age-based archival. "
                "To remove anyway (e.g. duplicate or invalid entry), confirm this is "
                "not seasonal archival."
            ),
        )


def _build_removed_member_record(
    member: UnitMembers,
    reason: str,
    deleted_by_id: int,
) -> RemovedUnitMember:
    """Create a removed_unit_member row — never writes to archived_unit_member."""
    return RemovedUnitMember(
        registered_user_id=member.registered_user_id,
        name=member.name,
        gender=member.gender,
        dob=member.dob,
        number=member.number,
        qualification=member.qualification,
        blood_group=member.blood_group,
        delete_reason=reason.strip(),
        deleted_by_id=deleted_by_id,
        original_member_id=member.id,
        removal_type=MemberRemovalType.ADMIN,
    )


async def remove_unit_member(
    db: AsyncSession,
    member_id: int,
    reason: str,
    deleted_by_id: int,
    confirm_not_archival: bool = False,
) -> RemovedUnitMember:
    """
    Move an active unit member to removed_unit_member (admin removal).
    Seasonal archival must use bulk_archive → archived_unit_member instead.
    """
    stmt = select(UnitMembers).where(UnitMembers.id == member_id)
    result = await db.execute(stmt)
    member = result.scalar_one_or_none()

    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Unit member not found",
        )

    await _validate_admin_removal_not_archival(db, [member], confirm_not_archival)

    registered_user_id = member.registered_user_id
    removed_member = _build_removed_member_record(member, reason, deleted_by_id)
    db.add(removed_member)
    await db.delete(member)
    await cycle_service.adjust_fee_for_member_delta(
        db,
        registered_user_id=registered_user_id,
        delta_members=-1,
    )
    await db.commit()
    await db.refresh(removed_member)

    return removed_member


async def bulk_remove_unit_members(
    db: AsyncSession,
    members: List[UnitMembers],
    reason: str,
    deleted_by_id: int,
    confirm_not_archival: bool = False,
) -> int:
    """Bulk admin removal into removed_unit_member (not archival)."""
    if not members:
        return 0

    await _validate_admin_removal_not_archival(db, members, confirm_not_archival)

    removals_by_unit: Dict[int, int] = {}
    for member in members:
        db.add(_build_removed_member_record(member, reason, deleted_by_id))
        removals_by_unit[member.registered_user_id] = (
            removals_by_unit.get(member.registered_user_id, 0) + 1
        )
        await db.delete(member)

    for registered_user_id, removal_count in removals_by_unit.items():
        await cycle_service.adjust_fee_for_member_delta(
            db,
            registered_user_id=registered_user_id,
            delta_members=-removal_count,
        )

    await db.commit()
    return len(members)


def _summarize_removed_members(members: List[RemovedUnitMember]) -> Dict[str, int]:
    male = 0
    female = 0
    for member in members:
        gender = normalize_member_gender(member.gender)
        if gender == "M":
            male += 1
        elif gender == "F":
            female += 1
    return {"total": len(members), "male": male, "female": female}


async def get_pending_removed_members_for_unit(
    db: AsyncSession,
    user_id: int,
) -> Dict[str, Any]:
    """Return admin-removed members to show the unit on login (until session dismiss)."""
    stmt = (
        select(RemovedUnitMember)
        .where(
            RemovedUnitMember.registered_user_id == user_id,
            RemovedUnitMember.removal_type == MemberRemovalType.ADMIN,
            RemovedUnitMember.delete_reason.isnot(None),
        )
        .order_by(RemovedUnitMember.archived_at.desc())
    )
    result = await db.execute(stmt)
    members = list(result.scalars().all())

    return {
        "summary": _summarize_removed_members(members),
        "members": members,
    }


async def acknowledge_removed_members(
    db: AsyncSession,
    user_id: int,
    removed_member_ids: Optional[List[int]] = None,
) -> int:
    """Mark removed-member notifications as seen by the unit."""
    stmt = select(RemovedUnitMember).where(
        RemovedUnitMember.registered_user_id == user_id,
        RemovedUnitMember.notified_at.is_(None),
        RemovedUnitMember.removal_type == MemberRemovalType.ADMIN,
    )
    if removed_member_ids:
        stmt = stmt.where(RemovedUnitMember.id.in_(removed_member_ids))

    result = await db.execute(stmt)
    members = list(result.scalars().all())
    if not members:
        return 0

    now = now_ist()
    for member in members:
        member.notified_at = now

    await db.commit()
    return len(members)


# List Functions for requests
async def get_transfer_requests(
    db: AsyncSession,
    user_id: Optional[int] = None,
    status_filter: Optional[RequestStatus] = None,
) -> List[Dict[str, Any]]:
    """Get list of transfer requests, optionally filtered."""
    stmt = select(UnitTransferRequest).options(
        selectinload(UnitTransferRequest.unit_member)
    )
    
    if user_id:
        stmt = stmt.where(UnitTransferRequest.original_registered_user_id == user_id)
    if status_filter:
        stmt = stmt.where(UnitTransferRequest.status == status_filter)
    
    stmt = stmt.order_by(UnitTransferRequest.created_at.desc())
    
    result = await db.execute(stmt)
    requests = list(result.scalars().all())
    
    # Get unit names for current and destination units
    unit_ids = set()
    for req in requests:
        if req.current_unit_id:
            unit_ids.add(req.current_unit_id)
        if req.destination_unit_id:
            unit_ids.add(req.destination_unit_id)
    
    # Fetch unit names
    unit_names = {}
    if unit_ids:
        stmt = select(UnitName).where(UnitName.id.in_(unit_ids))
        result = await db.execute(stmt)
        for unit in result.scalars().all():
            unit_names[unit.id] = unit.name
    
    # Build response with additional fields
    return [
        {
            "id": req.id,
            "unit_member_id": req.unit_member_id,
            "destination_unit_id": req.destination_unit_id,
            "reason": req.reason,
            "current_unit_id": req.current_unit_id,
            "original_registered_user_id": req.original_registered_user_id,
            "proof": req.proof,
            "status": req.status,
            "created_at": req.created_at,
            "updated_at": req.updated_at,
            "member_name": req.unit_member.name if req.unit_member else None,
            "current_unit_name": unit_names.get(req.current_unit_id) if req.current_unit_id else None,
            "destination_unit_name": unit_names.get(req.destination_unit_id) if req.destination_unit_id else None,
        }
        for req in requests
    ]


def _member_change_request_dict(
    req: UnitMemberChangeRequest,
    *,
    unit_name: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "id": req.id,
        "unit_member_id": req.unit_member_id,
        "reason": req.reason,
        "name": req.name,
        "gender": req.gender,
        "dob": req.dob,
        "blood_group": req.blood_group,
        "qualification": req.qualification,
        "number": req.number,
        "residence_location": req.residence_location,
        "residence_state_id": req.residence_state_id,
        "residence_city_id": req.residence_city_id,
        "original_name": req.original_name,
        "original_gender": req.original_gender,
        "original_dob": req.original_dob,
        "original_blood_group": req.original_blood_group,
        "original_qualification": req.original_qualification,
        "original_number": req.original_number,
        "original_residence_location": req.original_residence_location,
        "original_residence_state_id": req.original_residence_state_id,
        "original_residence_city_id": req.original_residence_city_id,
        "proof": req.proof,
        "status": req.status,
        "created_at": req.created_at,
        "updated_at": req.updated_at,
        "unit_name": unit_name,
    }


async def get_member_change_requests(
    db: AsyncSession,
    user_id: Optional[int] = None,
    status_filter: Optional[RequestStatus] = None,
) -> List[Dict[str, Any]]:
    """Get list of member change requests, optionally filtered."""
    stmt = (
        select(UnitMemberChangeRequest, UnitName.name.label("unit_name"))
        .join(UnitMembers, UnitMemberChangeRequest.unit_member_id == UnitMembers.id)
        .join(CustomUser, UnitMembers.registered_user_id == CustomUser.id)
        .outerjoin(UnitName, CustomUser.unit_name_id == UnitName.id)
    )

    if user_id:
        stmt = stmt.where(UnitMembers.registered_user_id == user_id)
    if status_filter:
        stmt = stmt.where(UnitMemberChangeRequest.status == status_filter)

    stmt = stmt.order_by(UnitMemberChangeRequest.created_at.desc())

    result = await db.execute(stmt)
    rows = result.all()

    return [
        _member_change_request_dict(req, unit_name=unit_name)
        for req, unit_name in rows
    ]


async def get_officials_change_requests(
    db: AsyncSession,
    user_id: Optional[int] = None,
    status_filter: Optional[RequestStatus] = None,
) -> List[Dict[str, Any]]:
    """Get list of officials change requests, optionally filtered."""
    stmt = (
        select(UnitOfficialsChangeRequest, UnitName.name.label("unit_name"))
        .join(UnitOfficials, UnitOfficialsChangeRequest.unit_official_id == UnitOfficials.id)
        .join(CustomUser, UnitOfficials.registered_user_id == CustomUser.id)
        .outerjoin(UnitName, CustomUser.unit_name_id == UnitName.id)
    )

    if user_id:
        stmt = stmt.where(UnitOfficials.registered_user_id == user_id)
    if status_filter:
        stmt = stmt.where(UnitOfficialsChangeRequest.status == status_filter)

    stmt = stmt.order_by(UnitOfficialsChangeRequest.created_at.desc())

    result = await db.execute(stmt)
    rows = result.all()

    return [
        {
            "id": req.id,
            "unit_official_id": req.unit_official_id,
            "reason": req.reason,
            "president_designation": req.president_designation,
            "president_name": req.president_name,
            "president_phone": req.president_phone,
            "original_president_designation": req.original_president_designation,
            "original_president_name": req.original_president_name,
            "original_president_phone": req.original_president_phone,
            "vice_president_name": req.vice_president_name,
            "vice_president_phone": req.vice_president_phone,
            "original_vice_president_name": req.original_vice_president_name,
            "original_vice_president_phone": req.original_vice_president_phone,
            "secretary_name": req.secretary_name,
            "secretary_phone": req.secretary_phone,
            "original_secretary_name": req.original_secretary_name,
            "original_secretary_phone": req.original_secretary_phone,
            "joint_secretary_name": req.joint_secretary_name,
            "joint_secretary_phone": req.joint_secretary_phone,
            "original_joint_secretary_name": req.original_joint_secretary_name,
            "original_joint_secretary_phone": req.original_joint_secretary_phone,
            "treasurer_name": req.treasurer_name,
            "treasurer_phone": req.treasurer_phone,
            "original_treasurer_name": req.original_treasurer_name,
            "original_treasurer_phone": req.original_treasurer_phone,
            "proof": req.proof,
            "status": req.status,
            "created_at": req.created_at,
            "updated_at": req.updated_at,
            "unit_name": unit_name,
        }
        for req, unit_name in rows
    ]


def _councilor_change_request_dict(
    req: UnitCouncilorChangeRequest,
    *,
    unit_id: Optional[int] = None,
    unit_name: Optional[str] = None,
    original_member_name: Optional[str] = None,
    new_member_name: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "id": req.id,
        "unit_councilor_id": req.unit_councilor_id,
        "reason": req.reason,
        "unit_member_id": req.unit_member_id,
        "original_unit_member_id": req.original_unit_member_id,
        "original_member_name": original_member_name or req.original_member_name,
        "new_member_name": new_member_name or req.new_member_name,
        "proof": req.proof,
        "status": req.status,
        "created_at": req.created_at,
        "updated_at": req.updated_at,
        "unit_id": unit_id,
        "unit_name": unit_name,
    }


async def get_councilor_change_requests(
    db: AsyncSession,
    user_id: Optional[int] = None,
    status_filter: Optional[RequestStatus] = None,
) -> List[Dict[str, Any]]:
    """Get list of councilor change requests, optionally filtered."""
    stmt = (
        select(
            UnitCouncilorChangeRequest,
            UnitName.name.label("unit_name"),
            CustomUser.id.label("unit_id"),
        )
        .outerjoin(
            UnitMembers,
            UnitCouncilorChangeRequest.original_unit_member_id == UnitMembers.id,
        )
        .outerjoin(CustomUser, UnitMembers.registered_user_id == CustomUser.id)
        .outerjoin(UnitName, CustomUser.unit_name_id == UnitName.id)
    )

    if user_id:
        stmt = stmt.where(UnitMembers.registered_user_id == user_id)
    if status_filter:
        stmt = stmt.where(UnitCouncilorChangeRequest.status == status_filter)

    stmt = stmt.order_by(UnitCouncilorChangeRequest.created_at.desc())

    result = await db.execute(stmt)
    rows = result.all()

    member_ids: set[int] = set()
    for req, _, _ in rows:
        if req.original_unit_member_id:
            member_ids.add(req.original_unit_member_id)
        if req.unit_member_id:
            member_ids.add(req.unit_member_id)

    member_names: Dict[int, str] = {}
    if member_ids:
        members_result = await db.execute(
            select(UnitMembers.id, UnitMembers.name).where(UnitMembers.id.in_(member_ids))
        )
        member_names = {row[0]: row[1] for row in members_result.all()}

    return [
        _councilor_change_request_dict(
            req,
            unit_id=unit_id,
            unit_name=unit_name,
            original_member_name=(
                req.original_member_name
                or (
                    member_names.get(req.original_unit_member_id)
                    if req.original_unit_member_id
                    else None
                )
            ),
            new_member_name=(
                req.new_member_name
                or (member_names.get(req.unit_member_id) if req.unit_member_id else None)
            ),
        )
        for req, unit_name, unit_id in rows
    ]


def _member_add_request_dict(
    req: UnitMemberAddRequest,
    *,
    unit_name: Optional[str] = None,
    username: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "id": req.id,
        "registered_user_id": req.registered_user_id,
        "name": req.name,
        "gender": req.gender,
        "dob": req.dob,
        "number": req.number,
        "qualification": req.qualification,
        "blood_group": req.blood_group,
        "reason": req.reason,
        "proof": req.proof,
        "status": req.status,
        "created_at": req.created_at,
        "updated_at": req.updated_at,
        "unit_name": unit_name,
        "username": username,
        "residence_location": (
            req.residence_location.value if req.residence_location else None
        ),
        "residence_state_id": req.residence_state_id,
        "residence_city_id": req.residence_city_id,
    }


async def _lookup_unit_labels_for_users(
    db: AsyncSession,
    user_ids: List[int],
) -> Dict[int, tuple[Optional[str], Optional[str]]]:
    if not user_ids:
        return {}
    result = await db.execute(
        select(CustomUser.id, CustomUser.username, UnitName.name)
        .outerjoin(UnitName, UnitName.id == CustomUser.unit_name_id)
        .where(CustomUser.id.in_(user_ids))
    )
    return {
        row[0]: (row[2], row[1])
        for row in result.all()
    }


async def get_member_add_requests(
    db: AsyncSession,
    user_id: Optional[int] = None,
    status_filter: Optional[RequestStatus] = None,
) -> List[Dict[str, Any]]:
    """Get list of member add requests with unit labels, optionally filtered."""
    stmt = select(UnitMemberAddRequest)

    if user_id:
        stmt = stmt.where(UnitMemberAddRequest.registered_user_id == user_id)
    if status_filter:
        stmt = stmt.where(UnitMemberAddRequest.status == status_filter)

    stmt = stmt.order_by(UnitMemberAddRequest.created_at.desc())

    result = await db.execute(stmt)
    requests = list(result.scalars().all())
    labels = await _lookup_unit_labels_for_users(
        db,
        list({req.registered_user_id for req in requests}),
    )

    return [
        _member_add_request_dict(
            req,
            unit_name=labels.get(req.registered_user_id, (None, None))[0],
            username=labels.get(req.registered_user_id, (None, None))[1],
        )
        for req in requests
    ]


def _summarize_archived_members(members: List[ArchivedUnitMember]) -> Dict[str, int]:
    male = 0
    female = 0
    for member in members:
        gender = normalize_member_gender(member.gender)
        if gender == "M":
            male += 1
        elif gender == "F":
            female += 1
    return {"total": len(members), "male": male, "female": female}


async def get_recent_archived_members_for_unit(
    db: AsyncSession,
    user_id: int,
) -> Dict[str, Any]:
    """Return the most recent archive_year batch for a unit with summary stats."""
    year_stmt = (
        select(ArchivedUnitMember.archive_year)
        .where(
            ArchivedUnitMember.registered_user_id == user_id,
            ArchivedUnitMember.archive_year.isnot(None),
        )
        .distinct()
        .order_by(ArchivedUnitMember.archive_year.desc())
        .limit(1)
    )
    year_result = await db.execute(year_stmt)
    latest_year = year_result.scalar_one_or_none()

    if not latest_year:
        return {
            "archive_year": None,
            "archive_reason": None,
            "summary": {"total": 0, "male": 0, "female": 0},
            "members": [],
            "pending_concern_member_ids": [],
            "member_concerns": {},
        }

    members_stmt = (
        select(ArchivedUnitMember)
        .where(
            ArchivedUnitMember.registered_user_id == user_id,
            ArchivedUnitMember.archive_year == latest_year,
        )
        .order_by(ArchivedUnitMember.name)
    )
    members_result = await db.execute(members_stmt)
    members = list(members_result.scalars().all())

    archive_reason = next((m.archive_reason for m in members if m.archive_reason), None)
    member_ids = [m.id for m in members]

    pending_ids: List[int] = []
    member_concerns: Dict[str, Dict[str, Any]] = {}
    if member_ids:
        concerns_stmt = (
            select(ArchivedMemberConcernRequest)
            .where(
                ArchivedMemberConcernRequest.archived_unit_member_id.in_(member_ids),
                ArchivedMemberConcernRequest.registered_user_id == user_id,
            )
            .order_by(ArchivedMemberConcernRequest.created_at.desc())
        )
        concerns_result = await db.execute(concerns_stmt)
        for concern in concerns_result.scalars().all():
            key = str(concern.archived_unit_member_id)
            if key not in member_concerns:
                member_concerns[key] = {
                    "status": concern.status.value,
                    "admin_response": concern.admin_response,
                }
                if concern.status == RequestStatus.PENDING:
                    pending_ids.append(concern.archived_unit_member_id)

    return {
        "archive_year": latest_year,
        "archive_reason": archive_reason,
        "summary": _summarize_archived_members(members),
        "members": members,
        "pending_concern_member_ids": pending_ids,
        "member_concerns": member_concerns,
    }


async def _enrich_concern_requests(
    db: AsyncSession,
    requests: List[ArchivedMemberConcernRequest],
) -> List[Dict[str, Any]]:
    if not requests:
        return []

    archived_ids = {r.archived_unit_member_id for r in requests}
    user_ids = {r.registered_user_id for r in requests}

    archived_stmt = select(ArchivedUnitMember).where(ArchivedUnitMember.id.in_(archived_ids))
    archived_result = await db.execute(archived_stmt)
    archived_map = {m.id: m for m in archived_result.scalars().all()}

    users_stmt = (
        select(CustomUser, UnitName.name.label("unit_name"))
        .outerjoin(UnitName, UnitName.id == CustomUser.unit_name_id)
        .where(CustomUser.id.in_(user_ids))
    )
    users_result = await db.execute(users_stmt)
    unit_name_map = {user.id: unit_name for user, unit_name in users_result.all()}

    enriched: List[Dict[str, Any]] = []
    for request in requests:
        archived = archived_map.get(request.archived_unit_member_id)
        enriched.append({
            "id": request.id,
            "archived_unit_member_id": request.archived_unit_member_id,
            "registered_user_id": request.registered_user_id,
            "concern_text": request.concern_text,
            "admin_response": request.admin_response,
            "status": request.status.value,
            "created_at": request.created_at.isoformat(),
            "updated_at": request.updated_at.isoformat(),
            "archived_member_name": archived.name if archived else None,
            "archived_member_gender": archived.gender if archived else None,
            "archived_member_dob": archived.dob.isoformat() if archived and archived.dob else None,
            "unit_name": unit_name_map.get(request.registered_user_id),
            "archive_year": archived.archive_year if archived else None,
        })
    return enriched


async def create_archived_member_concern_request(
    db: AsyncSession,
    user_id: int,
    data: ArchivedMemberConcernRequestCreate,
) -> ArchivedMemberConcernRequest:
    """Create a concern request for a recently archived member."""
    archived_stmt = select(ArchivedUnitMember).where(
        ArchivedUnitMember.id == data.archived_unit_member_id,
        ArchivedUnitMember.registered_user_id == user_id,
    )
    archived_result = await db.execute(archived_stmt)
    archived_member = archived_result.scalar_one_or_none()
    if not archived_member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Archived member not found for this unit",
        )

    recent = await get_recent_archived_members_for_unit(db, user_id)
    recent_ids = {m.id for m in recent["members"]}
    if archived_member.id not in recent_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Concerns can only be raised for members from the most recent archive batch",
        )

    pending_stmt = select(ArchivedMemberConcernRequest).where(
        ArchivedMemberConcernRequest.archived_unit_member_id == data.archived_unit_member_id,
        ArchivedMemberConcernRequest.status == RequestStatus.PENDING,
    )
    pending_result = await db.execute(pending_stmt)
    if pending_result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A pending concern already exists for this archived member",
        )

    concern_request = ArchivedMemberConcernRequest(
        archived_unit_member_id=data.archived_unit_member_id,
        registered_user_id=user_id,
        concern_text=data.concern_text.strip(),
        status=RequestStatus.PENDING,
    )
    db.add(concern_request)
    await db.commit()
    await db.refresh(concern_request)
    return concern_request


async def approve_archived_member_concern_request(
    db: AsyncSession,
    request_id: int,
    admin_response: Optional[str] = None,
) -> ArchivedMemberConcernRequest:
    """Mark a concern as reviewed and resolved."""
    stmt = select(ArchivedMemberConcernRequest).where(
        ArchivedMemberConcernRequest.id == request_id,
        ArchivedMemberConcernRequest.status == RequestStatus.PENDING,
    )
    result = await db.execute(stmt)
    concern_request = result.scalar_one_or_none()
    if not concern_request:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Concern request not found or already processed",
        )

    concern_request.status = RequestStatus.APPROVED
    if admin_response:
        concern_request.admin_response = admin_response.strip()

    await db.commit()
    await db.refresh(concern_request)
    return concern_request


async def reject_archived_member_concern_request(
    db: AsyncSession,
    request_id: int,
    admin_response: Optional[str] = None,
) -> ArchivedMemberConcernRequest:
    """Reject a concern after admin review."""
    stmt = select(ArchivedMemberConcernRequest).where(
        ArchivedMemberConcernRequest.id == request_id,
        ArchivedMemberConcernRequest.status == RequestStatus.PENDING,
    )
    result = await db.execute(stmt)
    concern_request = result.scalar_one_or_none()
    if not concern_request:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Concern request not found or already processed",
        )

    concern_request.status = RequestStatus.REJECTED
    if admin_response:
        concern_request.admin_response = admin_response.strip()

    await db.commit()
    await db.refresh(concern_request)
    return concern_request


async def get_archived_member_concern_requests(
    db: AsyncSession,
    user_id: Optional[int] = None,
    status_filter: Optional[RequestStatus] = None,
) -> List[Dict[str, Any]]:
    """Get archived member concern requests, optionally filtered by unit user."""
    stmt = select(ArchivedMemberConcernRequest)
    if user_id:
        stmt = stmt.where(ArchivedMemberConcernRequest.registered_user_id == user_id)
    if status_filter:
        stmt = stmt.where(ArchivedMemberConcernRequest.status == status_filter)
    stmt = stmt.order_by(ArchivedMemberConcernRequest.created_at.desc())

    result = await db.execute(stmt)
    requests = list(result.scalars().all())
    return await _enrich_concern_requests(db, requests)


async def get_unit_my_requests(
    db: AsyncSession,
    user_id: int,
) -> Dict[str, Any]:
    """Aggregate all request types for a unit user's My Requests page."""
    transfers = await get_transfer_requests(db, user_id=user_id)
    member_info_changes = await get_member_change_requests(db, user_id=user_id)
    officials_changes = await get_officials_change_requests(db, user_id=user_id)
    councilor_changes = await get_councilor_change_requests(db, user_id=user_id)
    member_adds = await get_member_add_requests(db, user_id=user_id)
    archived_concerns = await get_archived_member_concern_requests(db, user_id=user_id)

    user_stmt = (
        select(CustomUser, UnitName.name.label("unit_name"))
        .outerjoin(UnitName, UnitName.id == CustomUser.unit_name_id)
        .where(CustomUser.id == user_id)
    )
    user_result = await db.execute(user_stmt)
    user_row = user_result.first()
    unit_name = user_row[1] if user_row else None

    def _serialize_transfer(req: Dict[str, Any]) -> Dict[str, Any]:
        created_at = req["created_at"]
        status = req["status"]
        return {
            "id": req["id"],
            "createdAt": created_at.isoformat() if hasattr(created_at, "isoformat") else created_at,
            "memberId": req["unit_member_id"],
            "memberName": req.get("member_name") or f"Member #{req['unit_member_id']}",
            "currentUnitId": req.get("current_unit_id") or 0,
            "currentUnitName": req.get("current_unit_name") or "",
            "destinationUnitId": req["destination_unit_id"],
            "destinationUnitName": req.get("destination_unit_name") or "",
            "reason": req["reason"],
            "status": status.value if hasattr(status, "value") else status,
            "proof": req.get("proof"),
        }

    def _serialize_member_info(req: Dict[str, Any]) -> Dict[str, Any]:
        created_at = req["created_at"]
        status = req["status"]
        dob = req.get("dob")
        return {
            "id": req["id"],
            "createdAt": created_at.isoformat() if hasattr(created_at, "isoformat") else created_at,
            "memberId": req["unit_member_id"],
            "memberName": req.get("original_name") or req.get("name") or f"Member #{req['unit_member_id']}",
            "unitName": req.get("unit_name") or unit_name or "",
            "changes": {
                k: v
                for k, v in {
                    "name": req.get("name"),
                    "gender": req.get("gender"),
                    "dob": dob.isoformat() if hasattr(dob, "isoformat") else dob,
                    "bloodGroup": req.get("blood_group"),
                    "qualification": req.get("qualification"),
                    "number": req.get("number"),
                    "residenceLocation": (
                        req.get("residence_location").value
                        if hasattr(req.get("residence_location"), "value")
                        else req.get("residence_location")
                    ),
                    "residenceStateId": req.get("residence_state_id"),
                    "residenceCityId": req.get("residence_city_id"),
                }.items()
                if v is not None
            },
            "reason": req["reason"],
            "status": status.value if hasattr(status, "value") else status,
            "proof": req.get("proof"),
        }

    def _serialize_officials(req: Dict[str, Any]) -> Dict[str, Any]:
        status = req["status"]
        created_at = req["created_at"]
        return {
            "id": req["id"],
            "createdAt": created_at.isoformat() if hasattr(created_at, "isoformat") else created_at,
            "unitId": user_id,
            "unitName": req.get("unit_name") or unit_name or "",
            "originalOfficials": {
                "presidentDesignation": req.get("original_president_designation"),
                "presidentName": req.get("original_president_name") or "",
                "presidentPhone": req.get("original_president_phone") or "",
                "vicePresidentName": req.get("original_vice_president_name") or "",
                "vicePresidentPhone": req.get("original_vice_president_phone") or "",
                "secretaryName": req.get("original_secretary_name") or "",
                "secretaryPhone": req.get("original_secretary_phone") or "",
                "jointSecretaryName": req.get("original_joint_secretary_name") or "",
                "jointSecretaryPhone": req.get("original_joint_secretary_phone") or "",
                "treasurerName": req.get("original_treasurer_name") or "",
                "treasurerPhone": req.get("original_treasurer_phone") or "",
            },
            "requestedChanges": {
                k: v
                for k, v in {
                    "presidentDesignation": req.get("president_designation"),
                    "presidentName": req.get("president_name"),
                    "presidentPhone": req.get("president_phone"),
                    "vicePresidentName": req.get("vice_president_name"),
                    "vicePresidentPhone": req.get("vice_president_phone"),
                    "secretaryName": req.get("secretary_name"),
                    "secretaryPhone": req.get("secretary_phone"),
                    "jointSecretaryName": req.get("joint_secretary_name"),
                    "jointSecretaryPhone": req.get("joint_secretary_phone"),
                    "treasurerName": req.get("treasurer_name"),
                    "treasurerPhone": req.get("treasurer_phone"),
                }.items()
                if v is not None
            },
            "reason": req["reason"],
            "status": status.value if hasattr(status, "value") else status,
            "proof": req.get("proof"),
        }

    def _serialize_councilor(req: Dict[str, Any]) -> Dict[str, Any]:
        created_at = req["created_at"]
        status = req["status"]
        original_member_id = req.get("original_unit_member_id") or 0
        new_member_id = req.get("unit_member_id")
        return {
            "id": req["id"],
            "createdAt": created_at.isoformat() if hasattr(created_at, "isoformat") else created_at,
            "unitId": req.get("unit_id") or user_id,
            "unitName": req.get("unit_name") or unit_name or "",
            "councilorId": req.get("unit_councilor_id"),
            "originalMemberId": original_member_id,
            "originalMemberName": req.get("original_member_name") or f"Member #{original_member_id}",
            "newMemberId": new_member_id,
            "newMemberName": req.get("new_member_name") or (
                f"Member #{new_member_id}" if new_member_id else None
            ),
            "reason": req["reason"],
            "status": status.value if hasattr(status, "value") else status,
            "proof": req.get("proof"),
        }

    def _serialize_member_add(req: Dict[str, Any]) -> Dict[str, Any]:
        created_at = req["created_at"]
        status = req["status"]
        return {
            "id": req["id"],
            "createdAt": created_at.isoformat() if hasattr(created_at, "isoformat") else created_at,
            "unitId": req["registered_user_id"],
            "unitName": req.get("unit_name") or unit_name or "",
            "name": req["name"],
            "gender": req["gender"],
            "number": req["number"],
            "dob": req["dob"].isoformat() if hasattr(req["dob"], "isoformat") else req["dob"],
            "qualification": req.get("qualification"),
            "bloodGroup": req.get("blood_group"),
            "reason": req["reason"],
            "status": status.value if hasattr(status, "value") else status,
            "proof": req.get("proof"),
            "residenceLocation": req.get("residence_location"),
            "residenceStateId": req.get("residence_state_id"),
            "residenceCityId": req.get("residence_city_id"),
        }

    def _serialize_concern(req: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": req["id"],
            "createdAt": req["created_at"],
            "archivedMemberId": req["archived_unit_member_id"],
            "archivedMemberName": req.get("archived_member_name") or "",
            "archiveYear": req.get("archive_year"),
            "unitName": req.get("unit_name") or "",
            "concernText": req["concern_text"],
            "adminResponse": req.get("admin_response"),
            "status": req["status"],
        }

    return {
        "transfers": [_serialize_transfer(r) for r in transfers],
        "memberInfoChanges": [_serialize_member_info(r) for r in member_info_changes],
        "officialsChanges": [_serialize_officials(r) for r in officials_changes],
        "councilorChanges": [_serialize_councilor(r) for r in councilor_changes],
        "memberAdds": [_serialize_member_add(r) for r in member_adds],
        "archivedMemberConcerns": [_serialize_concern(r) for r in archived_concerns],
    }

