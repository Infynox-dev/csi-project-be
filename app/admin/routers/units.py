"""Admin units router - administrative endpoints for units management."""

from datetime import date, datetime
from typing import List, Optional
from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Request, status, Query, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import select, func, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.common.datetime_utils import format_timestamp_ist, now_ist

from app.common.db import get_async_db
from app.common.security import get_current_user, get_current_user_sync
from app.common.cache import get_cache, set_cache, clear_cache, TTL_DASHBOARD, TTL_UNITS_LIST, TTL_PAYMENTS, TTL_MEMBER_ADD_REQ
from app.common.storage import save_upload_file
from app.admin.models import SiteSettings
from app.admin.routers.site import get_public_file_url, SITE_SETTINGS_CACHE_KEY
from app.auth.models import (
    CustomUser,
    UnitMembers,
    UnitOfficials,
    UnitCouncilor,
    UnitDetails,
    UnitRegistrationData,
    ClergyDistrict,
    UnitName,
    UserType,
    ResidenceLocation,
)
from app.units.models import (
    UnitTransferRequest,
    UnitMemberChangeRequest,
    UnitOfficialsChangeRequest,
    UnitCouncilorChangeRequest,
    UnitMemberAddRequest,
    ArchivedMemberConcernRequest,
    ArchivedUnitMember,
    RequestStatus,
    UnitRegistrationCycle,
    UnitRegistrationPayment,
    PaymentProofStatus,
)
from app.units.gender_utils import normalize_member_gender
from app.units import registration_cycle_service as cycle_service
from app.units.schemas import (
    UnitTransferRequestResponse,
    UnitMemberChangeRequestResponse,
    UnitOfficialsChangeRequestResponse,
    UnitCouncilorChangeRequestResponse,
    UnitMemberAddRequestResponse,
    ArchivedMemberConcernRequestResponse,
    RequestActionSchema,
    MemberRemoveRequest,
    BulkMemberRemoveRequest,
)
from app.units import service as units_service
from app.common.exporter import (
    create_archived_members_csv,
    create_archived_members_excel,
    create_councilors_excel,
    create_district_payment_summary_excel,
    create_members_excel,
    create_officials_excel,
    create_registration_payments_csv,
    create_units_summary_csv,
)
from app.admin.units_summary_export import load_units_summary_for_export
from app.units.member_serialization import (
    MEMBER_RESIDENCE_LOAD_OPTIONS,
    member_export_row,
    serialize_member,
)

router = APIRouter()


def _onboarded_unit_name_ids_subquery(admin_user_id: int):
    """UnitName IDs that have an active platform account with registration data."""
    return (
        select(CustomUser.unit_name_id)
        .join(UnitRegistrationData, UnitRegistrationData.registered_user_id == CustomUser.id)
        .where(
            CustomUser.user_type == UserType.UNIT,
            CustomUser.is_active.is_(True),
            CustomUser.unit_name_id.isnot(None),
            CustomUser.id != admin_user_id,
        )
        .distinct()
    )


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


@router.get("/home", response_model=dict)
@router.get("/dashboard", response_model=dict)
async def admin_home_page(
    current_user: CustomUser = Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_db),
    refresh: bool = Query(False, description="Force refresh cache"),
):
    """Get admin dashboard statistics. Accessible via /home or /dashboard. Cached for 5 minutes."""
    from sqlalchemy import case, distinct, or_
    
    cache_key = "admin_dashboard_v2"
    
    # Check cache unless refresh is requested
    if not refresh:
        cached_data = get_cache(cache_key)
        if cached_data is not None:
            return cached_data
    
    # Single query for total counts
    stmt = select(
        func.count(distinct(ClergyDistrict.id)).label('total_districts'),
        func.count(distinct(UnitName.id)).label('total_units')
    ).select_from(ClergyDistrict).outerjoin(UnitName)
    result = await db.execute(stmt)
    counts = result.one()
    total_dist_count = counts[0] or 0
    total_units_count = counts[1] or 0

    current_year = await cycle_service.get_current_registration_year(db)
    
    # Completed registrations for the current registration year
    stmt = select(
        func.count(distinct(UnitRegistrationCycle.id)).label('completed_units'),
        func.count(distinct(UnitName.clergy_district_id)).label('completed_districts')
    ).select_from(UnitRegistrationCycle).join(
        CustomUser, UnitRegistrationCycle.registered_user_id == CustomUser.id
    ).join(
        UnitName, CustomUser.unit_name_id == UnitName.id
    ).where(
        UnitRegistrationCycle.status == cycle_service.REGISTRATION_COMPLETED,
        UnitRegistrationCycle.registration_year == current_year,
        UnitRegistrationCycle.registered_user_id != current_user.id
    )
    result = await db.execute(stmt)
    completed = result.one()
    completed_units_count = completed[0] or 0
    completed_dist_count = completed[1] or 0

    in_progress_result = await db.execute(
        select(func.count(distinct(UnitRegistrationCycle.id))).select_from(
            UnitRegistrationCycle
        ).join(
            CustomUser, UnitRegistrationCycle.registered_user_id == CustomUser.id
        ).where(
            UnitRegistrationCycle.registration_year == current_year,
            UnitRegistrationCycle.status != cycle_service.REGISTRATION_COMPLETED,
            UnitRegistrationCycle.registered_user_id != current_user.id,
        )
    )
    in_progress_units_count = in_progress_result.scalar() or 0

    registered_units_result = await db.execute(
        select(func.count(UnitRegistrationData.id)).where(
            UnitRegistrationData.registered_user_id != current_user.id,
        )
    )
    registered_units_count = registered_units_result.scalar() or 0
    not_started_units_count = max(
        0,
        registered_units_count - completed_units_count - in_progress_units_count,
    )

    not_onboarded_result = await db.execute(
        select(func.count(UnitName.id))
        .select_from(UnitName)
        .where(UnitName.id.not_in(_onboarded_unit_name_ids_subquery(current_user.id)))
    )
    not_onboarded_units_count = not_onboarded_result.scalar() or 0

    pending_payments_result = await db.execute(
        select(func.count(UnitRegistrationPayment.id))
        .select_from(UnitRegistrationPayment)
        .join(
            UnitRegistrationCycle,
            UnitRegistrationCycle.id == UnitRegistrationPayment.registration_cycle_id,
        )
        .where(
            UnitRegistrationPayment.status == PaymentProofStatus.PENDING,
            UnitRegistrationCycle.registration_year == current_year,
        )
    )
    pending_payments_count = pending_payments_result.scalar() or 0

    pending_approval_result = await db.execute(
        select(func.count(distinct(UnitRegistrationCycle.id))).select_from(
            UnitRegistrationCycle
        ).join(
            CustomUser, UnitRegistrationCycle.registered_user_id == CustomUser.id
        ).where(
            UnitRegistrationCycle.registration_year == current_year,
            UnitRegistrationCycle.status == cycle_service.DECLARATION_SUBMITTED,
            UnitRegistrationCycle.registered_user_id != current_user.id,
        )
    )
    pending_approval_units_count = pending_approval_result.scalar() or 0

    # Single UNION ALL query instead of 6 round-trips to count pending requests
    from sqlalchemy import union_all
    pending_union = union_all(
        select(func.count(UnitTransferRequest.id)).where(UnitTransferRequest.status == RequestStatus.PENDING),
        select(func.count(UnitMemberChangeRequest.id)).where(UnitMemberChangeRequest.status == RequestStatus.PENDING),
        select(func.count(UnitOfficialsChangeRequest.id)).where(UnitOfficialsChangeRequest.status == RequestStatus.PENDING),
        select(func.count(UnitCouncilorChangeRequest.id)).where(UnitCouncilorChangeRequest.status == RequestStatus.PENDING),
        select(func.count(UnitMemberAddRequest.id)).where(UnitMemberAddRequest.status == RequestStatus.PENDING),
        select(func.count(ArchivedMemberConcernRequest.id)).where(ArchivedMemberConcernRequest.status == RequestStatus.PENDING),
    )
    pending_rows = (await db.execute(pending_union)).all()
    pending_requests_count = sum(row[0] for row in pending_rows)
    
    # Member counts across all units
    male_gender = or_(UnitMembers.gender == 'M', UnitMembers.gender == 'Male')
    female_gender = or_(UnitMembers.gender == 'F', UnitMembers.gender == 'Female')
    stmt = select(
        func.count(UnitMembers.id).label('total'),
        func.count(case((male_gender, 1))).label('male'),
        func.count(case((female_gender, 1))).label('female')
    ).select_from(UnitMembers).join(
        CustomUser, UnitMembers.registered_user_id == CustomUser.id
    ).where(
        CustomUser.id != current_user.id,
    )
    result = await db.execute(stmt)
    member_counts = result.one()
    unit_members_count = member_counts[0] or 0

    # Member counts for units with completed registration or pending admin approval
    # (excludes in-progress, not-started, and not-onboarded units for the current season)
    reg_completed_cycle_statuses = (
        cycle_service.REGISTRATION_COMPLETED,
        cycle_service.DECLARATION_SUBMITTED,
    )
    stmt = select(
        func.count(UnitMembers.id).label('total'),
        func.count(case((male_gender, 1))).label('male'),
        func.count(case((female_gender, 1))).label('female')
    ).select_from(UnitMembers).join(
        CustomUser, UnitMembers.registered_user_id == CustomUser.id
    ).join(
        UnitRegistrationCycle,
        and_(
            UnitRegistrationCycle.registered_user_id == CustomUser.id,
            UnitRegistrationCycle.registration_year == current_year,
            UnitRegistrationCycle.status.in_(reg_completed_cycle_statuses),
        ),
    ).where(
        CustomUser.id != current_user.id,
    )
    result = await db.execute(stmt)
    reg_completed_member_counts = result.one()
    unit_members_reg_completed_count = reg_completed_member_counts[0] or 0
    unit_members_males_count = reg_completed_member_counts[1] or 0
    unit_females_count = reg_completed_member_counts[2] or 0
    
    # Single query for max member unit with unit name
    stmt = select(
        UnitName.name,
        func.count(UnitMembers.id).label('count')
    ).select_from(UnitMembers).join(
        CustomUser, UnitMembers.registered_user_id == CustomUser.id
    ).join(
        UnitName, CustomUser.unit_name_id == UnitName.id
    ).group_by(UnitName.id, UnitName.name).order_by(
        func.count(UnitMembers.id).desc()
    ).limit(1)
    result = await db.execute(stmt)
    max_unit = result.first()
    
    max_member_unit_name = max_unit[0] if max_unit else "N/A"
    max_member_count = max_unit[1] if max_unit else 0
    
    completed_dists_percent = (completed_dist_count / total_dist_count * 100) if total_dist_count > 0 else 0
    completed_units_percent = (completed_units_count / total_units_count * 100) if total_units_count > 0 else 0
    
    result_data = {
        "total_dist_count": total_dist_count,
        "total_units_count": total_units_count,
        "completed_dist_count": completed_dist_count,
        "completed_units_count": completed_units_count,
        "completed_dists_percent": f"{completed_dists_percent:.2f}",
        "completed_units_percent": f"{completed_units_percent:.2f}",
        "current_registration_year": current_year,
        "registered_units_count": registered_units_count,
        "in_progress_units_count": in_progress_units_count,
        "not_started_units_count": not_started_units_count,
        "not_onboarded_units_count": not_onboarded_units_count,
        "pending_payments_count": pending_payments_count,
        "pending_approval_units_count": pending_approval_units_count,
        "pending_requests": pending_requests_count,
        "total_unit_members": unit_members_count,
        "total_unit_members_reg_completed": unit_members_reg_completed_count,
        "total_male_members": unit_members_males_count,
        "total_female_members": unit_females_count,
        "max_member_unit": max_member_unit_name,
        "max_member_unit_count": max_member_count,
    }
    
    # Cache for 5 minutes (300 seconds)
    set_cache(cache_key, result_data, ttl_seconds=TTL_DASHBOARD)
    
    return result_data


@router.get("/all", response_model=List[dict])
@router.get("", response_model=List[dict])
async def list_all_units(
    current_user: CustomUser = Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_db),
    refresh: bool = Query(False, description="Force refresh cache"),
):
    """List all registered units. Accessible via /all or root path. Cached 5 minutes."""
    current_year = await cycle_service.get_current_registration_year(db)
    cache_key = f"admin_units_list:{current_year}"
    if not refresh:
        cached = get_cache(cache_key)
        if cached is not None:
            return cached

    stmt = select(UnitRegistrationData).where(
        UnitRegistrationData.registered_user_id != current_user.id
    ).options(
        selectinload(UnitRegistrationData.registered_user).selectinload(CustomUser.unit_name)
    )
    result = await db.execute(stmt)
    units_data = list(result.scalars().all())

    user_ids = [u.registered_user_id for u in units_data]
    cycles_by_user: dict[int, UnitRegistrationCycle] = {}
    if user_ids:
        cycles_result = await db.execute(
            select(UnitRegistrationCycle).where(
                UnitRegistrationCycle.registered_user_id.in_(user_ids),
                UnitRegistrationCycle.registration_year == current_year,
            )
        )
        for cycle in cycles_result.scalars().all():
            cycles_by_user[cycle.registered_user_id] = cycle

    payment_status_by_user: dict[int, str] = {}
    payment_fully_approved_by_user: dict[int, bool] = {}
    payment_reviewed_at_by_user: dict[int, Optional[datetime]] = {}
    if user_ids:
        payments_result = await db.execute(
            select(UnitRegistrationPayment).where(
                UnitRegistrationPayment.registered_user_id.in_(user_ids),
            )
        )
        payments = list(payments_result.scalars().all())
        for user_id in user_ids:
            cycle = cycles_by_user.get(user_id)
            if not cycle:
                payment_status_by_user[user_id] = "not_submitted"
                payment_fully_approved_by_user[user_id] = False
                payment_reviewed_at_by_user[user_id] = None
                continue
            user_payments = sorted(
                [p for p in payments if p.registration_cycle_id == cycle.id],
                key=lambda p: p.submitted_at,
            )
            if not user_payments:
                payment_status_by_user[user_id] = "not_submitted"
                payment_fully_approved_by_user[user_id] = False
                payment_reviewed_at_by_user[user_id] = None
                continue

            approved_payments = [
                p for p in user_payments if p.status == PaymentProofStatus.APPROVED
            ]
            if approved_payments:
                latest_approved = approved_payments[-1]
                payment_reviewed_at_by_user[user_id] = latest_approved.reviewed_at
                if (
                    latest_approved.balance_amount is not None
                    and latest_approved.balance_amount > 0
                ):
                    payment_status_by_user[user_id] = "partial"
                    payment_fully_approved_by_user[user_id] = False
                else:
                    payment_status_by_user[user_id] = "approved"
                    payment_fully_approved_by_user[user_id] = True
            elif user_payments[-1].status == PaymentProofStatus.REJECTED:
                payment_status_by_user[user_id] = "rejected"
                payment_fully_approved_by_user[user_id] = False
                payment_reviewed_at_by_user[user_id] = None
            else:
                payment_status_by_user[user_id] = "pending"
                payment_fully_approved_by_user[user_id] = False
                payment_reviewed_at_by_user[user_id] = None

    member_counts_by_user: dict[int, int] = {}
    if user_ids:
        member_counts_result = await db.execute(
            select(
                UnitMembers.registered_user_id,
                func.count(UnitMembers.id).label("count"),
            )
            .where(UnitMembers.registered_user_id.in_(user_ids))
            .group_by(UnitMembers.registered_user_id)
        )
        for row in member_counts_result.all():
            member_counts_by_user[row.registered_user_id] = row.count or 0
    
    result = [
        {
            "id": unit_data.id,
            "user_id": unit_data.registered_user_id,
            "username": unit_data.registered_user.username,
            "unit_name": unit_data.registered_user.unit_name.name if unit_data.registered_user.unit_name else None,
            "member_count": member_counts_by_user.get(unit_data.registered_user_id, 0),
            "status": cycles_by_user[unit_data.registered_user_id].status
            if unit_data.registered_user_id in cycles_by_user
            else "Not Started",
            "registration_year": current_year,
            "cycle_status": cycles_by_user[unit_data.registered_user_id].status
            if unit_data.registered_user_id in cycles_by_user
            else None,
            "path_type": cycles_by_user[unit_data.registered_user_id].path_type
            if unit_data.registered_user_id in cycles_by_user
            else None,
            "payment_status": payment_status_by_user.get(unit_data.registered_user_id, "not_submitted"),
            "payment_fully_approved": payment_fully_approved_by_user.get(
                unit_data.registered_user_id, False
            ),
            "can_complete_registration": cycle_service.can_admin_complete_registration(
                cycles_by_user.get(unit_data.registered_user_id),
                payment_fully_approved=payment_fully_approved_by_user.get(
                    unit_data.registered_user_id, False
                ),
                current_registration_year=current_year,
                latest_payment_reviewed_at=payment_reviewed_at_by_user.get(
                    unit_data.registered_user_id
                ),
            ),
        }
        for unit_data in units_data
    ]

    set_cache(cache_key, result, ttl_seconds=TTL_UNITS_LIST)
    return result


@router.get("/not-onboarded", response_model=List[dict])
async def list_not_onboarded_units(
    current_user: CustomUser = Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Master-list churches with no active platform registration account."""
    stmt = (
        select(
            UnitName.id,
            UnitName.name,
            UnitName.clergy_district_id,
            ClergyDistrict.name.label("clergy_district"),
        )
        .join(ClergyDistrict, ClergyDistrict.id == UnitName.clergy_district_id)
        .where(UnitName.id.not_in(_onboarded_unit_name_ids_subquery(current_user.id)))
        .order_by(ClergyDistrict.name, UnitName.name)
    )
    rows = (await db.execute(stmt)).all()
    return [
        {
            "id": row.id,
            "name": row.name,
            "clergy_district_id": row.clergy_district_id,
            "clergy_district": row.clergy_district,
        }
        for row in rows
    ]


# Transfer Request Endpoints
@router.get("/transfer-requests", response_model=List[UnitTransferRequestResponse])
async def list_transfer_requests(
    status: Optional[str] = Query(None, description="Filter by status: PENDING, APPROVED, REJECTED. Omit to return all."),
    current_user: CustomUser = Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_db),
):
    """List transfer requests. Returns all statuses when no filter is provided."""
    from app.units.models import RequestStatus as RS
    status_filter = RS(status.upper()) if status else None
    return await units_service.get_transfer_requests(db, status_filter=status_filter)


@router.put("/transfer-requests/{request_id}/approve", response_model=UnitTransferRequestResponse)
async def approve_transfer_request(
    request_id: int,
    action: RequestActionSchema = None,
    current_user: CustomUser = Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Approve a transfer request."""
    return await units_service.approve_unit_transfer_request(db, request_id)


@router.put("/transfer-requests/{request_id}/revert", response_model=UnitTransferRequestResponse)
async def revert_transfer_request(
    request_id: int,
    action: RequestActionSchema = None,
    current_user: CustomUser = Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Revert a transfer request."""
    return await units_service.revert_unit_transfer_request(db, request_id)


@router.put("/transfer-requests/{request_id}/reject", response_model=UnitTransferRequestResponse)
async def reject_transfer_request(
    request_id: int,
    action: RequestActionSchema = None,
    current_user: CustomUser = Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Reject a transfer request."""
    return await units_service.reject_unit_transfer_request(db, request_id)


# Member Change Request Endpoints
@router.get("/member-change-requests", response_model=List[UnitMemberChangeRequestResponse])
async def list_member_change_requests(
    status: Optional[str] = Query(None, description="Filter by status: PENDING, APPROVED, REJECTED. Omit to return all."),
    current_user: CustomUser = Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_db),
):
    """List member change requests. Returns all statuses when no filter is provided."""
    from app.units.models import RequestStatus as RS
    status_filter = RS(status.upper()) if status else None
    return await units_service.get_member_change_requests(db, status_filter=status_filter)


@router.put("/member-change-requests/{request_id}/approve", response_model=UnitMemberChangeRequestResponse)
async def approve_member_change_request(
    request_id: int,
    action: RequestActionSchema = None,
    current_user: CustomUser = Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Approve a member change request."""
    return await units_service.approve_member_info_change(db, request_id)


@router.put("/member-change-requests/{request_id}/revert", response_model=UnitMemberChangeRequestResponse)
async def revert_member_change_request(
    request_id: int,
    action: RequestActionSchema = None,
    current_user: CustomUser = Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Revert a member change request."""
    return await units_service.revert_member_info_change(db, request_id)


@router.put("/member-change-requests/{request_id}/reject", response_model=UnitMemberChangeRequestResponse)
async def reject_member_change_request(
    request_id: int,
    action: RequestActionSchema = None,
    current_user: CustomUser = Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Reject a member change request."""
    return await units_service.reject_member_info_change(db, request_id)


# Officials Change Request Endpoints
@router.get("/officials-change-requests", response_model=List[UnitOfficialsChangeRequestResponse])
async def list_officials_change_requests(
    status: Optional[str] = Query(None, description="Filter by status: PENDING, APPROVED, REJECTED. Omit to return all."),
    current_user: CustomUser = Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_db),
):
    """List officials change requests. Returns all statuses when no filter is provided."""
    from app.units.models import RequestStatus as RS
    status_filter = RS(status.upper()) if status else None
    return await units_service.get_officials_change_requests(db, status_filter=status_filter)


@router.put("/officials-change-requests/{request_id}/approve", response_model=UnitOfficialsChangeRequestResponse)
async def approve_officials_change_request(
    request_id: int,
    action: RequestActionSchema = None,
    current_user: CustomUser = Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Approve an officials change request."""
    return await units_service.approve_officials_change(db, request_id)


@router.put("/officials-change-requests/{request_id}/revert", response_model=UnitOfficialsChangeRequestResponse)
async def revert_officials_change_request(
    request_id: int,
    action: RequestActionSchema = None,
    current_user: CustomUser = Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Revert an officials change request."""
    return await units_service.revert_officials_change(db, request_id)


@router.put("/officials-change-requests/{request_id}/reject", response_model=UnitOfficialsChangeRequestResponse)
async def reject_officials_change_request(
    request_id: int,
    action: RequestActionSchema = None,
    current_user: CustomUser = Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Reject an officials change request."""
    return await units_service.reject_officials_change(db, request_id)


# Councilor Change Request Endpoints
@router.get("/councilor-change-requests", response_model=List[UnitCouncilorChangeRequestResponse])
async def list_councilor_change_requests(
    status: Optional[str] = Query(None, description="Filter by status: PENDING, APPROVED, REJECTED. Omit to return all."),
    current_user: CustomUser = Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_db),
):
    """List councilor change requests. Returns all statuses when no filter is provided."""
    from app.units.models import RequestStatus as RS
    status_filter = RS(status.upper()) if status else None
    return await units_service.get_councilor_change_requests(db, status_filter=status_filter)


@router.put("/councilor-change-requests/{request_id}/approve", response_model=UnitCouncilorChangeRequestResponse)
async def approve_councilor_change_request(
    request_id: int,
    action: RequestActionSchema = None,
    current_user: CustomUser = Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Approve a councilor change request."""
    return await units_service.approve_councilor_change(db, request_id)


@router.put("/councilor-change-requests/{request_id}/revert", response_model=UnitCouncilorChangeRequestResponse)
async def revert_councilor_change_request(
    request_id: int,
    action: RequestActionSchema = None,
    current_user: CustomUser = Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Revert a councilor change request."""
    return await units_service.revert_councilor_change(db, request_id)


@router.put("/councilor-change-requests/{request_id}/reject", response_model=UnitCouncilorChangeRequestResponse)
async def reject_councilor_change_request(
    request_id: int,
    action: RequestActionSchema = None,
    current_user: CustomUser = Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Reject a councilor change request."""
    return await units_service.reject_councilor_change(db, request_id)


# Member Add Request Endpoints
@router.get("/member-add-requests", response_model=List[UnitMemberAddRequestResponse])
async def list_member_add_requests(
    refresh: bool = Query(False, description="Force refresh cache"),
    current_user: CustomUser = Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_db),
):
    """List all member add requests."""
    cache_key = "admin_member_add_requests"
    if not refresh:
        cached = get_cache(cache_key)
        if cached is not None:
            return cached
    payload = await units_service.get_member_add_requests(db)
    set_cache(cache_key, payload, ttl_seconds=TTL_MEMBER_ADD_REQ)
    return payload


@router.put("/member-add-requests/{request_id}/approve", response_model=UnitMemberAddRequestResponse)
async def approve_member_add_request(
    request_id: int,
    action: RequestActionSchema = None,
    current_user: CustomUser = Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Approve a member add request."""
    result = await units_service.approve_member_add_request(db, request_id)
    clear_cache("admin_member_add_requests")
    clear_cache("admin_units_list")
    return result


@router.put("/member-add-requests/{request_id}/reject", response_model=UnitMemberAddRequestResponse)
async def reject_member_add_request(
    request_id: int,
    action: RequestActionSchema = None,
    current_user: CustomUser = Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Reject a member add request."""
    result = await units_service.reject_member_add_request(db, request_id)
    clear_cache("admin_member_add_requests")
    return result


# Archived Member Concern Request Endpoints
@router.get("/archived-member-concern-requests")
async def list_archived_member_concern_requests(
    status: Optional[str] = Query(None, description="Filter by status: PENDING, APPROVED, REJECTED. Omit to return all."),
    current_user: CustomUser = Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_db),
):
    """List archived member concern requests. Returns all statuses when no filter is provided."""
    from app.units.models import RequestStatus as RS
    status_filter = RS(status.upper()) if status else None
    return await units_service.get_archived_member_concern_requests(db, status_filter=status_filter)


@router.put("/archived-member-concern-requests/{request_id}/approve")
async def approve_archived_member_concern_request(
    request_id: int,
    action: RequestActionSchema = None,
    current_user: CustomUser = Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Resolve an archived member concern after admin review."""
    concern = await units_service.approve_archived_member_concern_request(
        db,
        request_id,
        admin_response=action.remarks if action else None,
    )
    enriched = await units_service.get_archived_member_concern_requests(db)
    match = next((item for item in enriched if item["id"] == concern.id), None)
    return match or concern


@router.put("/archived-member-concern-requests/{request_id}/reject")
async def reject_archived_member_concern_request(
    request_id: int,
    action: RequestActionSchema = None,
    current_user: CustomUser = Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Reject an archived member concern after admin review."""
    concern = await units_service.reject_archived_member_concern_request(
        db,
        request_id,
        admin_response=action.remarks if action else None,
    )
    enriched = await units_service.get_archived_member_concern_requests(db)
    match = next((item for item in enriched if item["id"] == concern.id), None)
    return match or concern


# Unit Officials Endpoint with Pagination
@router.get("/officials", response_model=dict)
async def list_all_unit_officials(
    current_user: CustomUser = Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_db),
    page: int = 1,
    page_size: int = 50,
    unit_id: Optional[int] = Query(None, description="Filter by registered unit user id"),
):
    """List all unit officials across all units with pagination."""
    filters = []
    if unit_id is not None:
        filters.append(UnitOfficials.registered_user_id == unit_id)

    # Get total count
    count_stmt = select(func.count()).select_from(UnitOfficials)
    if filters:
        count_stmt = count_stmt.where(*filters)
    total = (await db.execute(count_stmt)).scalar() or 0
    
    # Get paginated data
    offset = (page - 1) * page_size
    stmt = select(UnitOfficials).options(
        selectinload(UnitOfficials.registered_user).selectinload(CustomUser.unit_name).selectinload(UnitName.district)
    ).offset(offset).limit(page_size)
    if filters:
        stmt = stmt.where(*filters)
    result = await db.execute(stmt)
    officials_list = list(result.scalars().all())
    
    data = [
        {
            "id": officials.id,
            "registered_user_id": officials.registered_user_id,
            "unit_name": officials.registered_user.unit_name.name if officials.registered_user and officials.registered_user.unit_name else None,
            "district": officials.registered_user.unit_name.district.name if officials.registered_user and officials.registered_user.unit_name and officials.registered_user.unit_name.district else None,
            "president_designation": officials.president_designation,
            "president_name": officials.president_name,
            "president_phone": officials.president_phone,
            "vice_president_name": officials.vice_president_name,
            "vice_president_phone": officials.vice_president_phone,
            "secretary_name": officials.secretary_name,
            "secretary_phone": officials.secretary_phone,
            "joint_secretary_name": officials.joint_secretary_name,
            "joint_secretary_phone": officials.joint_secretary_phone,
            "treasurer_name": officials.treasurer_name,
            "treasurer_phone": officials.treasurer_phone,
        }
        for officials in officials_list
    ]
    
    return {"data": data, "total": total, "page": page, "page_size": page_size, "pages": (total + page_size - 1) // page_size}


# Unit Councilors Endpoint with Pagination
@router.get("/councilors", response_model=dict)
async def list_all_unit_councilors(
    current_user: CustomUser = Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_db),
    page: int = 1,
    page_size: int = 50,
    unit_id: Optional[int] = Query(None, description="Filter by registered unit user id"),
):
    """List all unit councilors across all units with pagination."""
    filters = []
    if unit_id is not None:
        filters.append(UnitCouncilor.registered_user_id == unit_id)

    # Get total count
    count_stmt = select(func.count()).select_from(UnitCouncilor)
    if filters:
        count_stmt = count_stmt.where(*filters)
    total = (await db.execute(count_stmt)).scalar() or 0
    
    # Get paginated data
    offset = (page - 1) * page_size
    stmt = select(UnitCouncilor).options(
        selectinload(UnitCouncilor.registered_user).selectinload(CustomUser.unit_name).selectinload(UnitName.district),
        selectinload(UnitCouncilor.unit_member)
    ).offset(offset).limit(page_size)
    if filters:
        stmt = stmt.where(*filters)
    result = await db.execute(stmt)
    councilors_list = list(result.scalars().all())
    
    data = [
        {
            "id": councilor.id,
            "registered_user_id": councilor.registered_user_id,
            "unit_name": councilor.registered_user.unit_name.name if councilor.registered_user and councilor.registered_user.unit_name else None,
            "district": councilor.registered_user.unit_name.district.name if councilor.registered_user and councilor.registered_user.unit_name and councilor.registered_user.unit_name.district else None,
            "unit_member_id": councilor.unit_member_id,
            "member_name": councilor.unit_member.name if councilor.unit_member else None,
            "member_gender": councilor.unit_member.gender if councilor.unit_member else None,
            "member_phone": councilor.unit_member.number if councilor.unit_member else None,
        }
        for councilor in councilors_list
    ]
    
    return {"data": data, "total": total, "page": page, "page_size": page_size, "pages": (total + page_size - 1) // page_size}


def _member_search_filters(search_term: str) -> tuple[list, bool]:
    """Match each word against name, phone, qualification, or unit name."""
    words = [w for w in search_term.split() if w]
    if not words:
        return [], False

    word_filters = []
    for word in words:
        pattern = f"%{word}%"
        word_filters.append(
            or_(
                func.trim(UnitMembers.name).ilike(pattern),
                UnitMembers.number.ilike(pattern),
                func.coalesce(UnitMembers.qualification, "").ilike(pattern),
                UnitName.name.ilike(pattern),
            )
        )
    return word_filters, True


# Unit Members Endpoint with Pagination
@router.get("/members", response_model=dict)
async def list_all_unit_members(
    current_user: CustomUser = Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_db),
    page: int = 1,
    page_size: int = 50,
    unit_id: Optional[int] = Query(None, description="Filter by registered unit user id"),
    residence_location: Optional[ResidenceLocation] = Query(None),
    missing_residence_location: Optional[bool] = Query(None),
    search: Optional[str] = Query(None, description="Search name, phone, qualification, or unit name"),
):
    """List all unit members across all units with pagination."""
    filters = []
    if unit_id is not None:
        filters.append(UnitMembers.registered_user_id == unit_id)
    if missing_residence_location:
        filters.append(UnitMembers.residence_location.is_(None))
    elif residence_location is not None:
        filters.append(UnitMembers.residence_location == residence_location)

    search_term = (search or "").strip()
    unit_name_joined = False
    if search_term:
        search_filters, unit_name_joined = _member_search_filters(search_term)
        filters.extend(search_filters)

    # Get total count
    count_stmt = select(func.count()).select_from(UnitMembers)
    if unit_name_joined:
        count_stmt = (
            count_stmt
            .outerjoin(CustomUser, CustomUser.id == UnitMembers.registered_user_id)
            .outerjoin(UnitName, UnitName.id == CustomUser.unit_name_id)
        )
    if filters:
        count_stmt = count_stmt.where(*filters)
    total = (await db.execute(count_stmt)).scalar() or 0
    
    # Get paginated data
    offset = (page - 1) * page_size
    stmt = select(UnitMembers).options(
        selectinload(UnitMembers.registered_user).selectinload(CustomUser.unit_name).selectinload(UnitName.district),
        *MEMBER_RESIDENCE_LOAD_OPTIONS,
    ).order_by(UnitMembers.name.asc()).offset(offset).limit(page_size)
    if unit_name_joined:
        stmt = (
            stmt
            .outerjoin(CustomUser, CustomUser.id == UnitMembers.registered_user_id)
            .outerjoin(UnitName, UnitName.id == CustomUser.unit_name_id)
        )
    if filters:
        stmt = stmt.where(*filters)
    result = await db.execute(stmt)
    members_list = list(result.scalars().unique().all())
    
    data = []
    for member in members_list:
        row = serialize_member(member)
        row["unit_name"] = (
            member.registered_user.unit_name.name
            if member.registered_user and member.registered_user.unit_name
            else None
        )
        row["district"] = (
            member.registered_user.unit_name.district.name
            if member.registered_user
            and member.registered_user.unit_name
            and member.registered_user.unit_name.district
            else None
        )
        data.append(row)
    
    return {"data": data, "total": total, "page": page, "page_size": page_size, "pages": (total + page_size - 1) // page_size}


# ── Archive Preview ──────────────────────────────────────────────────────────

@router.get("/archive-preview", response_model=dict)
async def archive_preview(
    current_user: CustomUser = Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Return all active unit members whose DOB falls outside the configured
    Min DOB / Max DOB limits (i.e. candidates for yearly archiving).
    Reads limits from site_settings; falls back to 1990-01-01 / 2011-12-31.
    """
    from app.admin.models import SiteSettings

    # Load DOB limits from site settings
    ss_result = await db.execute(select(SiteSettings))
    ss = ss_result.scalar_one_or_none()
    min_dob: date = (ss.member_min_dob if ss and ss.member_min_dob else date(1990, 1, 1))
    max_dob: date = (ss.member_max_dob if ss and ss.member_max_dob else date(2011, 12, 31))

    # Find members whose DOB is outside [min_dob, max_dob]
    stmt = (
        select(UnitMembers)
        .options(
            selectinload(UnitMembers.registered_user).selectinload(CustomUser.unit_name),
            *MEMBER_RESIDENCE_LOAD_OPTIONS,
        )
        .where(
            or_(
                UnitMembers.dob < min_dob,
                UnitMembers.dob > max_dob,
            )
        )
        .order_by(UnitMembers.dob.asc())
    )
    result = await db.execute(stmt)
    members = list(result.scalars().all())

    data = []
    for m in members:
        row = serialize_member(m)
        row["unit_name"] = (
            m.registered_user.unit_name.name
            if m.registered_user and m.registered_user.unit_name
            else None
        )
        data.append(row)

    return {
        "data": data,
        "total": len(data),
        "min_dob": min_dob.isoformat(),
        "max_dob": max_dob.isoformat(),
    }


# ── Bulk Archive ──────────────────────────────────────────────────────────────

@router.post("/bulk-archive", response_model=dict)
async def bulk_archive_members(
    member_ids: List[int] = Body(..., embed=True),
    archive_year: str = Body(..., embed=True),
    archive_reason: Optional[str] = Body(None, embed=True),
    current_user: CustomUser = Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Archive active members into archived_unit_member for seasonal age-based archival.
    Does not write to removed_unit_member — use POST /members/remove for admin deletion.
    """
    if not member_ids:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No member IDs provided")

    stmt = select(UnitMembers).where(UnitMembers.id.in_(member_ids))
    result = await db.execute(stmt)
    members = list(result.scalars().all())

    found_ids = {m.id for m in members}
    missing = set(member_ids) - found_ids
    if missing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Members not found: {sorted(missing)}",
        )

    await units_service.remove_member_dependencies(db, member_ids)

    archived_count = 0
    for member in members:
        archived = ArchivedUnitMember(
            registered_user_id=member.registered_user_id,
            name=member.name,
            gender=member.gender,
            dob=member.dob,
            number=member.number,
            qualification=member.qualification,
            blood_group=member.blood_group,
            archived_at=now_ist(),
            archive_year=archive_year.strip(),
            archive_reason=archive_reason,
        )
        db.add(archived)
        await db.delete(member)
        archived_count += 1

    await db.commit()
    return {
        "message": f"{archived_count} member(s) archived successfully",
        "archived_count": archived_count,
        "archive_year": archive_year,
    }


# ── Restore Archived Member ───────────────────────────────────────────────────

@router.post("/restore-member/{member_id}", response_model=dict)
async def restore_archived_member(
    member_id: int,
    current_user: CustomUser = Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Restore an archived member back to active unit_members."""
    stmt = select(ArchivedUnitMember).where(ArchivedUnitMember.id == member_id)
    result = await db.execute(stmt)
    archived = result.scalar_one_or_none()

    if not archived:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Archived member not found")

    current_year = await cycle_service.get_current_registration_year(db)
    cycle = await cycle_service.get_cycle(db, archived.registered_user_id, current_year)

    active_member = UnitMembers(
        registered_user_id=archived.registered_user_id,
        name=archived.name,
        gender=normalize_member_gender(archived.gender),
        dob=archived.dob,
        number=archived.number,
        qualification=archived.qualification,
        blood_group=archived.blood_group,
        added_registration_cycle_id=cycle.id if cycle else None,
    )
    db.add(active_member)
    await db.delete(archived)
    await db.commit()

    return {"message": f"{archived.name} has been restored to active members"}


# ── Archived Unit Members List ────────────────────────────────────────────────

async def _fetch_archived_members(
    db: AsyncSession,
    archive_year: Optional[str] = None,
) -> List[dict]:
    """Load archived members with unit names, optionally filtered by archive year."""
    stmt = (
        select(ArchivedUnitMember, UnitName.name.label("unit_name"))
        .outerjoin(CustomUser, CustomUser.id == ArchivedUnitMember.registered_user_id)
        .outerjoin(UnitName, UnitName.id == CustomUser.unit_name_id)
        .order_by(ArchivedUnitMember.archived_at.desc())
    )
    if archive_year and archive_year.lower() != "all":
        stmt = stmt.where(ArchivedUnitMember.archive_year == archive_year.strip())

    result = await db.execute(stmt)
    rows = result.all()

    return [
        {
            "id": member.id,
            "registered_user_id": member.registered_user_id,
            "name": member.name,
            "gender": member.gender,
            "dob": member.dob.isoformat() if member.dob else None,
            "age": member.age,
            "number": member.number,
            "qualification": member.qualification,
            "blood_group": member.blood_group,
            "archived_at": member.archived_at.isoformat() if member.archived_at else None,
            "archive_year": member.archive_year,
            "archive_reason": member.archive_reason,
            "unit_name": unit_name,
        }
        for member, unit_name in rows
    ]


@router.get("/archived-members/export")
async def export_archived_members(
    format: str = Query("xlsx", pattern="^(xlsx|csv)$"),
    archive_year: Optional[str] = Query(
        None,
        description='Filter by archive year label, or omit / use "all" for every year',
    ),
    current_user: CustomUser = Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Export archived unit members to Excel or CSV, respecting the archive year filter."""
    members = await _fetch_archived_members(db, archive_year)

    year_suffix = "all"
    if archive_year and archive_year.lower() != "all":
        year_suffix = archive_year.strip().replace("/", "-").replace("\\", "-")

    if format == "csv":
        export_file = create_archived_members_csv(members)
        media_type = "text/csv"
        filename = f"archived_members_{year_suffix}.csv"
    else:
        export_file = create_archived_members_excel(members)
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        filename = f"archived_members_{year_suffix}.xlsx"

    return StreamingResponse(
        export_file,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# Archived Unit Members Endpoint with Pagination
@router.get("/archived-members", response_model=dict)
async def list_all_archived_members(
    current_user: CustomUser = Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_db),
    page: int = 1,
    page_size: int = 50,
):
    """List all archived unit members across all units with pagination."""
    # Get total count
    count_stmt = select(func.count()).select_from(ArchivedUnitMember)
    total = (await db.execute(count_stmt)).scalar() or 0

    # Get paginated data — join CustomUser → UnitName to resolve the unit name
    offset = (page - 1) * page_size
    stmt = (
        select(ArchivedUnitMember, UnitName.name.label("unit_name"))
        .outerjoin(CustomUser, CustomUser.id == ArchivedUnitMember.registered_user_id)
        .outerjoin(UnitName, UnitName.id == CustomUser.unit_name_id)
        .order_by(ArchivedUnitMember.archived_at.desc())
        .offset(offset)
        .limit(page_size)
    )
    result = await db.execute(stmt)
    rows = result.all()

    data = [
        {
            "id": member.id,
            "registered_user_id": member.registered_user_id,
            "name": member.name,
            "gender": member.gender,
            "dob": member.dob.isoformat() if member.dob else None,
            "age": member.age,
            "number": member.number,
            "qualification": member.qualification,
            "blood_group": member.blood_group,
            "archived_at": member.archived_at.isoformat() if member.archived_at else None,
            "archive_year": member.archive_year,
            "archive_reason": member.archive_reason,
            "unit_name": unit_name,
        }
        for member, unit_name in rows
    ]

    return {"data": data, "total": total, "page": page, "page_size": page_size, "pages": (total + page_size - 1) // page_size}


# ── Blood Donor Search ────────────────────────────────────────────────────────

@router.get("/blood-donor-search", response_model=dict)
async def blood_donor_search(
    blood_group: Optional[str] = Query(None, description="Filter by blood group e.g. O+"),
    name: Optional[str] = Query(None, description="Partial name search"),
    gender: Optional[str] = Query(None, description="Filter by gender: M or F"),
    districts: List[str] = Query(default=[], description="Filter by district names (multi-select)"),
    units: List[str] = Query(default=[], description="Filter by unit names (multi-select)"),
    include_archived: bool = Query(True, description="Include archived members"),
    current_user: CustomUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Search for blood donors across active and archived unit members.
    Access: Admin always allowed. District officials / unit users only if enabled in site settings.
    """
    from app.admin.models import SiteSettings

    # ── Permission check ──────────────────────────────────────────────────────
    if current_user.user_type == UserType.BLOOD_BANK:
        pass  # Dedicated blood bank users always have access
    elif current_user.user_type != UserType.ADMIN:
        ss_result = await db.execute(select(SiteSettings))
        ss = ss_result.scalar_one_or_none()
        user_type_val = current_user.user_type.value if hasattr(current_user.user_type, 'value') else current_user.user_type
        is_district = user_type_val == "DISTRICT_OFFICIAL" or current_user.user_type == UserType.DISTRICT_OFFICIAL
        is_unit = user_type_val == "UNIT" or current_user.user_type == UserType.UNIT
        if is_district and not (ss and ss.blood_donor_district_access):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Blood Donor Search not enabled for District Officials")
        if is_unit and not (ss and ss.blood_donor_unit_access):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Blood Donor Search not enabled for Unit Officials")
        if not (is_district or is_unit):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    results = []

    # ── Helper: build filters ─────────────────────────────────────────────────
    def name_filter(col): return col.ilike(f"%{name}%") if name else True
    def unit_filter(col): return col.in_(units) if units else True
    def district_filter(col): return col.in_(districts) if districts else True
    def bg_filter(col): return col == blood_group if blood_group else True
    def gender_filter(col): return col == gender if gender else True

    # ── Active members ─────────────────────────────────────────────────────────
    active_stmt = (
        select(
            UnitMembers.id,
            UnitMembers.name,
            UnitMembers.gender,
            UnitMembers.dob,
            UnitMembers.number,
            UnitMembers.blood_group,
            UnitMembers.qualification,
            UnitName.name.label("unit_name"),
            ClergyDistrict.name.label("district_name"),
        )
        .outerjoin(CustomUser, CustomUser.id == UnitMembers.registered_user_id)
        .outerjoin(UnitName, UnitName.id == CustomUser.unit_name_id)
        .outerjoin(ClergyDistrict, ClergyDistrict.id == UnitName.clergy_district_id)
        .where(bg_filter(UnitMembers.blood_group))
        .where(name_filter(UnitMembers.name))
        .where(gender_filter(UnitMembers.gender))
        .where(unit_filter(UnitName.name))
        .where(district_filter(ClergyDistrict.name))
        .order_by(UnitMembers.name)
        .limit(500)
    )
    active_rows = (await db.execute(active_stmt)).all()
    for r in active_rows:
        results.append({
            "id": r.id,
            "name": r.name,
            "gender": r.gender,
            "dob": r.dob.isoformat() if r.dob else None,
            "blood_group": r.blood_group,
            "number": r.number,
            "qualification": r.qualification,
            "unit_name": r.unit_name,
            "district_name": r.district_name,
            "status": "active",
            "archive_year": None,
        })

    # ── Archived members ───────────────────────────────────────────────────────
    if include_archived:
        archived_stmt = (
            select(
                ArchivedUnitMember.id,
                ArchivedUnitMember.name,
                ArchivedUnitMember.gender,
                ArchivedUnitMember.dob,
                ArchivedUnitMember.number,
                ArchivedUnitMember.blood_group,
                ArchivedUnitMember.qualification,
                ArchivedUnitMember.archive_year,
                UnitName.name.label("unit_name"),
                ClergyDistrict.name.label("district_name"),
            )
            .outerjoin(CustomUser, CustomUser.id == ArchivedUnitMember.registered_user_id)
            .outerjoin(UnitName, UnitName.id == CustomUser.unit_name_id)
            .outerjoin(ClergyDistrict, ClergyDistrict.id == UnitName.clergy_district_id)
            .where(bg_filter(ArchivedUnitMember.blood_group))
            .where(name_filter(ArchivedUnitMember.name))
            .where(gender_filter(ArchivedUnitMember.gender))
            .where(unit_filter(UnitName.name))
            .where(district_filter(ClergyDistrict.name))
            .order_by(ArchivedUnitMember.name)
            .limit(500)
        )
        archived_rows = (await db.execute(archived_stmt)).all()
        for r in archived_rows:
            results.append({
                "id": r.id,
                "name": r.name,
                "gender": r.gender,
                "dob": r.dob.isoformat() if r.dob else None,
                "blood_group": r.blood_group,
                "number": r.number,
                "qualification": r.qualification,
                "unit_name": r.unit_name,
                "district_name": r.district_name,
                "status": "archived",
                "archive_year": r.archive_year,
            })

    # Sort combined results: active first, then by name
    results.sort(key=lambda x: (x["status"] != "active", x["name"] or ""))

    return {"data": results, "total": len(results)}


# Member Management — admin removal (removed_unit_member, NOT seasonal archival)


@router.post("/members/removal-payment-preview", response_model=dict)
async def preview_member_removal_payment_impact(
    member_ids: List[int] = Body(..., embed=True),
    current_user: CustomUser = Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Preview registration payment impact before admin removes members.

    Seasonal archival (bulk-archive) is unrelated to registration payments.
    """
    if not member_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No member IDs provided",
        )

    stmt = select(UnitMembers).where(UnitMembers.id.in_(member_ids))
    result = await db.execute(stmt)
    members = list(result.scalars().all())

    found_ids = {m.id for m in members}
    missing = set(member_ids) - found_ids
    if missing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Members not found: {sorted(missing)}",
        )

    settings_result = await db.execute(select(SiteSettings).limit(1))
    settings = settings_result.scalar_one_or_none()
    unit_fee = (
        settings.unit_registration_fee
        if settings and settings.unit_registration_fee is not None
        else 100
    )
    member_fee = (
        settings.unit_member_fee
        if settings and settings.unit_member_fee is not None
        else 10
    )
    current_year = await cycle_service.get_current_registration_year(db)

    removals_by_unit: dict[int, int] = {}
    for member in members:
        removals_by_unit[member.registered_user_id] = (
            removals_by_unit.get(member.registered_user_id, 0) + 1
        )

    impacts: list[dict] = []
    for registered_user_id, remove_count in sorted(removals_by_unit.items()):
        user_stmt = (
            select(CustomUser, UnitName.name.label("unit_name"))
            .outerjoin(UnitName, UnitName.id == CustomUser.unit_name_id)
            .where(CustomUser.id == registered_user_id)
        )
        user_row = (await db.execute(user_stmt)).first()
        username = user_row[0].username if user_row else str(registered_user_id)
        unit_name = user_row[1] if user_row else None

        cycle = await cycle_service.get_cycle(db, registered_user_id, current_year)
        if cycle is None or cycle.status not in (
            cycle_service.DECLARATION_SUBMITTED,
            cycle_service.REGISTRATION_COMPLETED,
        ):
            impacts.append({
                "registered_user_id": registered_user_id,
                "username": username,
                "unit_name": unit_name,
                "applies": False,
                "reason": "Registration declaration not submitted for the current season.",
                "members_to_remove": remove_count,
            })
            continue

        cycle_payments = await cycle_service._get_cycle_payments(db, cycle.id)
        approved = [
            p for p in cycle_payments if p.status == PaymentProofStatus.APPROVED
        ]
        if not approved:
            impacts.append({
                "registered_user_id": registered_user_id,
                "username": username,
                "unit_name": unit_name,
                "applies": False,
                "reason": "No approved registration payment for the current season.",
                "members_to_remove": remove_count,
            })
            continue

        preview = cycle_service.preview_payment_after_member_delta(
            cycle,
            approved,
            delta_members=-remove_count,
            member_fee=member_fee,
            unit_fee=unit_fee,
        )
        impacts.append({
            "registered_user_id": registered_user_id,
            "username": username,
            "unit_name": unit_name,
            "applies": True,
            "members_to_remove": remove_count,
            "member_fee": member_fee,
            **preview,
        })

    return {"impacts": impacts, "member_count": len(members)}


@router.post("/members/{member_id}/remove", response_model=dict)
async def remove_member(
    member_id: int,
    body: MemberRemoveRequest,
    current_user: CustomUser = Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Remove an active unit member. Seasonal archival uses POST /bulk-archive instead."""
    await units_service.remove_member_dependencies(db, [member_id])
    await units_service.remove_unit_member(
        db,
        member_id,
        reason=body.reason,
        deleted_by_id=current_user.id,
        confirm_not_archival=body.confirm_not_archival,
    )
    return {"message": "Member removed successfully"}


@router.post("/members/bulk-remove", response_model=dict)
async def bulk_remove_members(
    body: BulkMemberRemoveRequest,
    current_user: CustomUser = Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Remove active unit members. Does not archive — use POST /bulk-archive for that."""
    member_ids = body.member_ids
    if not member_ids:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No member IDs provided")

    stmt = select(UnitMembers).where(UnitMembers.id.in_(member_ids))
    result = await db.execute(stmt)
    members = list(result.scalars().all())

    found_ids = {m.id for m in members}
    missing = set(member_ids) - found_ids
    if missing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Members not found: {sorted(missing)}",
        )

    await units_service.remove_member_dependencies(db, member_ids)
    removed_count = await units_service.bulk_remove_unit_members(
        db,
        members,
        reason=body.reason,
        deleted_by_id=current_user.id,
        confirm_not_archival=body.confirm_not_archival,
    )
    return {
        "message": f"{removed_count} member(s) removed successfully",
        "removed_count": removed_count,
    }


@router.delete("/members/{member_id}", response_model=dict, deprecated=True)
async def archive_member(
    member_id: int,
    current_user: CustomUser = Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Deprecated — use POST /members/{member_id}/remove with a reason instead."""
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Use POST /admin/units/members/{member_id}/remove with a mandatory reason",
    )


# Password Reset
@router.post("/reset-password", response_model=dict)
async def reset_password(
    username: str,
    new_password: str,
    current_user: CustomUser = Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Reset a user's password."""
    from app.common.security import get_password_hash
    
    stmt = select(CustomUser).where(CustomUser.username == username)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User with username {username} not found"
        )
    
    user.hashed_password = get_password_hash(new_password)
    await db.commit()
    
    return {"message": f"Password for user {username} reset successfully"}



# ── Registration Payment Review ──────────────────────────────────────────────


@router.get("/registration-payments", response_model=List[dict])
async def list_registration_payments(
    payment_status: Optional[str] = Query(None, description="Filter by status: PENDING, APPROVED, REJECTED"),
    registration_year: Optional[int] = Query(None, description="Filter by registration year"),
    refresh: bool = Query(False, description="Force refresh cache"),
    current_user: CustomUser = Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_db),
):
    """List all unit registration payment proof submissions."""
    cache_key = f"admin_registration_payments:{registration_year}:{payment_status or 'all'}"
    if not refresh:
        cached = get_cache(cache_key)
        if cached is not None:
            return cached

    stmt = (
        select(UnitRegistrationPayment, CustomUser, UnitName, UnitRegistrationCycle)
        .join(CustomUser, CustomUser.id == UnitRegistrationPayment.registered_user_id)
        .outerjoin(UnitName, UnitName.id == CustomUser.unit_name_id)
        .outerjoin(
            UnitRegistrationCycle,
            UnitRegistrationCycle.id == UnitRegistrationPayment.registration_cycle_id,
        )
        .order_by(
            func.lower(func.coalesce(UnitName.name, CustomUser.username)).asc(),
            UnitRegistrationPayment.submitted_at.asc(),
        )
    )
    if payment_status:
        try:
            ps = PaymentProofStatus(payment_status.upper())
            stmt = stmt.where(UnitRegistrationPayment.status == ps)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid status value")
    if registration_year is not None:
        stmt = stmt.where(UnitRegistrationCycle.registration_year == registration_year)

    result = await db.execute(stmt)
    rows = result.all()

    cycles_by_id: dict[int, UnitRegistrationCycle] = {}
    for _, _, _, cycle in rows:
        if cycle is not None:
            cycles_by_id[cycle.id] = cycle

    payments_by_cycle = await cycle_service.get_payments_by_cycle_ids(
        db, list(cycles_by_id.keys())
    )
    summary_by_cycle: dict[int, dict] = {}
    for cycle_id, cycle_row in cycles_by_id.items():
        cycle_payments = payments_by_cycle.get(cycle_id, [])
        approved = [
            pay for pay in cycle_payments if pay.status == PaymentProofStatus.APPROVED
        ]
        if approved:
            cycle_service.recalculate_latest_approved_balance(cycle_row, approved)
        summary_by_cycle[cycle_id] = {
            **cycle_service.build_payment_summary(cycle_row, approved),
            "registration_member_count": cycle_row.member_count_at_submit,
        }

    payload = [
        {
            "id": p.id,
            "registered_user_id": p.registered_user_id,
            "username": u.username,
            "unit_name": un.name if un else None,
            "registration_year": cycle.registration_year if cycle else None,
            "file_url": get_public_file_url(p.file_path) if p.file_path else None,
            "total_amount": p.total_amount,
            "balance_amount": p.balance_amount,
            "registration_total_amount": cycle.total_fee_at_submit if cycle else None,
            "registration_member_count": (
                summary_by_cycle[cycle.id]["member_count"] if cycle and cycle.id in summary_by_cycle else None
            ),
            "total_paid": (
                summary_by_cycle[cycle.id]["total_paid"] if cycle and cycle.id in summary_by_cycle else None
            ),
            "payment_credit": (
                summary_by_cycle[cycle.id]["payment_credit"] if cycle and cycle.id in summary_by_cycle else None
            ),
            "balance_due": (
                summary_by_cycle[cycle.id]["balance_due"] if cycle and cycle.id in summary_by_cycle else None
            ),
            "status": p.status.value,
            "rejection_note": p.rejection_note,
            "approved_paid_amount": p.approved_paid_amount,
            "detected_paid_amount": p.detected_paid_amount,
            "submitted_at": p.submitted_at.isoformat(),
            "reviewed_at": p.reviewed_at.isoformat() if p.reviewed_at else None,
        }
        for p, u, un, cycle in rows
    ]
    set_cache(cache_key, payload, ttl_seconds=TTL_PAYMENTS)
    return payload


def _absolute_file_url(base_url: str, object_key: str | None) -> str | None:
    relative = get_public_file_url(object_key)
    if not relative:
        return None
    return f"{base_url.rstrip('/')}{relative}"


def _format_export_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    return format_timestamp_ist(value, fmt="%d-%b-%Y %I:%M %p IST")


def _derive_unit_payment_status(
    payments: list[UnitRegistrationPayment],
    summary: dict,
) -> str:
    approved = [pay for pay in payments if pay.status == PaymentProofStatus.APPROVED]
    pending = [pay for pay in payments if pay.status == PaymentProofStatus.PENDING]
    if approved:
        balance_due = summary.get("balance_due") or 0
        if balance_due > 0:
            return "Partial Payment"
        return "Fully Paid"
    if pending:
        return "Pending Review"
    return "Rejected"


def _group_registration_payments_for_export(payments: List[dict]) -> List[dict]:
    """Group payment rows by unit and season, keeping all proofs together."""
    grouped: dict[tuple[int, int | None], list[dict]] = {}
    for payment in payments:
        key = (payment["registered_user_id"], payment.get("registration_year"))
        grouped.setdefault(key, []).append(payment)

    unit_groups = sorted(
        grouped.values(),
        key=lambda rows: (
            (rows[0].get("district_name") or "").lower(),
            (rows[0].get("unit_name") or rows[0].get("username") or "").lower(),
            rows[0].get("registered_user_id") or 0,
        ),
    )

    flattened: List[dict] = []
    for index, rows in enumerate(unit_groups):
        rows.sort(key=lambda row: row.get("_submitted_at_sort") or "")
        submission_count = len(rows)
        unit_status = rows[0].get("unit_payment_status", "")
        for proof_index, row in enumerate(rows, start=1):
            row["proof_sequence"] = proof_index
            row["submission_count"] = submission_count
            row["unit_payment_status"] = unit_status
            flattened.append(row)
        if index < len(unit_groups) - 1:
            flattened.append({"_group_separator": True})
    return flattened


async def _load_registration_payments_for_export(
    db: AsyncSession,
    *,
    registration_year: Optional[int] = None,
    district_id: Optional[int] = None,
    unit_id: Optional[int] = None,
    base_url: str = "",
) -> List[dict]:
    stmt = (
        select(
            UnitRegistrationPayment,
            CustomUser,
            UnitName,
            UnitRegistrationCycle,
            ClergyDistrict.name.label("district_name"),
        )
        .join(CustomUser, CustomUser.id == UnitRegistrationPayment.registered_user_id)
        .outerjoin(UnitName, UnitName.id == CustomUser.unit_name_id)
        .outerjoin(ClergyDistrict, ClergyDistrict.id == UnitName.clergy_district_id)
        .outerjoin(
            UnitRegistrationCycle,
            UnitRegistrationCycle.id == UnitRegistrationPayment.registration_cycle_id,
        )
    )
    if registration_year is not None:
        stmt = stmt.where(UnitRegistrationCycle.registration_year == registration_year)
    if district_id is not None:
        stmt = stmt.where(UnitName.clergy_district_id == district_id)
    if unit_id is not None:
        stmt = stmt.where(UnitRegistrationPayment.registered_user_id == unit_id)

    result = await db.execute(stmt)
    rows = result.all()

    cycles_by_id: dict[int, UnitRegistrationCycle] = {}
    for _, _, _, cycle, _ in rows:
        if cycle is not None:
            cycles_by_id[cycle.id] = cycle

    payments_by_cycle = await cycle_service.get_payments_by_cycle_ids(
        db, list(cycles_by_id.keys())
    )
    summary_by_cycle: dict[int, dict] = {}
    for cycle_id, cycle_row in cycles_by_id.items():
        cycle_payments = payments_by_cycle.get(cycle_id, [])
        approved = [
            pay for pay in cycle_payments if pay.status == PaymentProofStatus.APPROVED
        ]
        if approved:
            cycle_service.recalculate_latest_approved_balance(cycle_row, approved)
        summary_by_cycle[cycle_id] = cycle_service.build_payment_summary(cycle_row, approved)

    payments_by_unit_cycle: dict[tuple[int, int | None], list[UnitRegistrationPayment]] = {}
    for p, u, un, cycle, district_name in rows:
        cycle_id = cycle.id if cycle else None
        payments_by_unit_cycle.setdefault((p.registered_user_id, cycle_id), []).append(p)

    unit_status_by_cycle: dict[tuple[int, int | None], str] = {}
    for (registered_user_id, cycle_id), cycle_payments in payments_by_unit_cycle.items():
        summary = summary_by_cycle.get(cycle_id, {}) if cycle_id is not None else {}
        unit_status_by_cycle[(registered_user_id, cycle_id)] = _derive_unit_payment_status(
            cycle_payments,
            summary,
        )

    payment_rows = [
        {
            "id": p.id,
            "registered_user_id": p.registered_user_id,
            "username": u.username,
            "unit_name": un.name if un else None,
            "district_name": district_name,
            "registration_year": cycle.registration_year if cycle else None,
            "total_amount": p.total_amount,
            "balance_amount": p.balance_amount,
            "registration_total_amount": cycle.total_fee_at_submit if cycle else None,
            "registration_member_count": (
                summary_by_cycle[cycle.id]["member_count"] if cycle and cycle.id in summary_by_cycle else None
            ),
            "total_paid": (
                summary_by_cycle[cycle.id]["total_paid"] if cycle and cycle.id in summary_by_cycle else None
            ),
            "payment_credit": (
                summary_by_cycle[cycle.id]["payment_credit"] if cycle and cycle.id in summary_by_cycle else None
            ),
            "balance_due": (
                summary_by_cycle[cycle.id]["balance_due"] if cycle and cycle.id in summary_by_cycle else None
            ),
            "unit_payment_status": unit_status_by_cycle.get(
                (p.registered_user_id, cycle.id if cycle else None),
                p.status.value,
            ),
            "status": p.status.value,
            "rejection_note": p.rejection_note,
            "approved_paid_amount": p.approved_paid_amount,
            "detected_paid_amount": p.detected_paid_amount,
            "submitted_at": _format_export_datetime(p.submitted_at),
            "reviewed_at": _format_export_datetime(p.reviewed_at),
            "_submitted_at_sort": p.submitted_at.isoformat(),
            "payment_proof_url": _absolute_file_url(base_url, p.file_path),
        }
        for p, u, un, cycle, district_name in rows
    ]
    return _group_registration_payments_for_export(payment_rows)


async def _load_district_payment_summary_for_export(
    db: AsyncSession,
    *,
    registration_year: int,
) -> List[dict]:
    """Aggregate registration payment totals by district for the summary report."""
    districts_result = await db.execute(select(ClergyDistrict).order_by(ClergyDistrict.name))
    districts = list(districts_result.scalars().all())

    summary_by_district: dict[int, dict] = {
        district.id: {
            "district_name": district.name,
            "amount_to_be_paid": 0,
            "total_amount_paid": 0,
            "balance_excess": 0,
            "started": 0,
            "payment_done": 0,
        }
        for district in districts
    }

    cycles_stmt = (
        select(UnitRegistrationCycle, UnitName.clergy_district_id)
        .join(CustomUser, CustomUser.id == UnitRegistrationCycle.registered_user_id)
        .join(UnitName, UnitName.id == CustomUser.unit_name_id)
        .where(
            UnitRegistrationCycle.registration_year == registration_year,
            CustomUser.user_type == UserType.UNIT,
            UnitRegistrationCycle.total_fee_at_submit.is_not(None),
        )
    )
    cycle_rows = (await db.execute(cycles_stmt)).all()
    cycle_ids = [cycle.id for cycle, _ in cycle_rows]
    payments_by_cycle = await cycle_service.get_payments_by_cycle_ids(db, cycle_ids)

    for cycle, district_id in cycle_rows:
        if district_id not in summary_by_district:
            continue

        approved = [
            payment
            for payment in payments_by_cycle.get(cycle.id, [])
            if payment.status == PaymentProofStatus.APPROVED
        ]
        payment_summary = cycle_service.build_payment_summary(cycle, approved)
        district_summary = summary_by_district[district_id]
        district_summary["amount_to_be_paid"] += payment_summary["fee_owed"]
        district_summary["total_amount_paid"] += payment_summary["total_paid"]
        district_summary["balance_excess"] += payment_summary["payment_credit"]
        district_summary["started"] += 1
        if payment_summary["is_fully_paid"]:
            district_summary["payment_done"] += 1

    return [summary_by_district[district.id] for district in districts]


@router.get("/registration-payments/summary/export")
async def export_district_payment_summary(
    registration_year: Optional[int] = Query(None, description="Filter by registration year"),
    current_user: CustomUser = Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Export district-wise registration payment summary to Excel."""
    if registration_year is None:
        registration_year = await cycle_service.get_current_registration_year(db)

    rows = await _load_district_payment_summary_for_export(
        db,
        registration_year=registration_year,
    )
    export_file = create_district_payment_summary_excel(
        rows,
        registration_year=registration_year,
    )
    filename = f"district_payment_summary_{registration_year}.xlsx"

    return StreamingResponse(
        export_file,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/registration-payments/export")
async def export_registration_payments(
    request: Request,
    registration_year: Optional[int] = Query(None, description="Filter by registration year"),
    district_id: Optional[int] = Query(None, description="Filter by clergy district id"),
    unit_id: Optional[int] = Query(None, description="Filter by registered unit user id"),
    current_user: CustomUser = Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Export unit registration payment submissions to CSV."""
    rows = await _load_registration_payments_for_export(
        db,
        registration_year=registration_year,
        district_id=district_id,
        unit_id=unit_id,
        base_url=str(request.base_url),
    )

    year_suffix = str(registration_year) if registration_year is not None else "all"
    district_suffix = str(district_id) if district_id is not None else "all"
    unit_suffix = str(unit_id) if unit_id is not None else "all"
    filename = f"registration_payments_{year_suffix}_{district_suffix}_{unit_suffix}.csv"
    export_file = create_registration_payments_csv(rows)

    return StreamingResponse(
        export_file,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/registration-payments/{payment_id}/approve", response_model=dict)
async def approve_registration_payment(
    payment_id: int,
    paid_amount: int = Body(..., embed=True),
    current_user: CustomUser = Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Approve a unit registration payment proof submission.

    Admin enters the amount paid in this proof; remaining balance is derived from
    the outstanding balance after prior approved proofs, not the full registration
    total. Print form unlocks for the unit once balance is 0.
    """
    if paid_amount < 0:
        raise HTTPException(status_code=400, detail="Paid amount cannot be negative")

    stmt = select(UnitRegistrationPayment).where(UnitRegistrationPayment.id == payment_id)
    result = await db.execute(stmt)
    payment = result.scalar_one_or_none()

    if not payment:
        raise HTTPException(status_code=404, detail="Payment submission not found")

    if payment.total_amount is None:
        raise HTTPException(
            status_code=400,
            detail="Payment total is unknown; cannot approve with a paid amount",
        )

    cycle = None
    if payment.registration_cycle_id is not None:
        cycle_result = await db.execute(
            select(UnitRegistrationCycle).where(
                UnitRegistrationCycle.id == payment.registration_cycle_id
            )
        )
        cycle = cycle_result.scalar_one_or_none()

    fee_owed = (
        cycle.total_fee_at_submit
        if cycle is not None and cycle.total_fee_at_submit is not None
        else payment.total_amount
    )

    prior_stmt = (
        select(UnitRegistrationPayment)
        .where(
            UnitRegistrationPayment.registered_user_id == payment.registered_user_id,
            UnitRegistrationPayment.registration_cycle_id == payment.registration_cycle_id,
            UnitRegistrationPayment.status == PaymentProofStatus.APPROVED,
            UnitRegistrationPayment.id != payment.id,
        )
        .order_by(UnitRegistrationPayment.submitted_at.asc())
    )
    prior_result = await db.execute(prior_stmt)
    prior_approved = list(prior_result.scalars().all())

    total_paid_so_far = cycle_service.compute_total_paid_for_approved_payments(
        prior_approved, fee_owed=fee_owed
    )
    current_balance = max(0, fee_owed - total_paid_so_far)

    if paid_amount > current_balance:
        raise HTTPException(
            status_code=400,
            detail="Paid amount cannot exceed the remaining balance",
        )

    payment.status = PaymentProofStatus.APPROVED
    payment.approved_paid_amount = paid_amount
    payment.rejection_note = None
    payment.reviewed_at = now_ist()
    payment.reviewed_by_id = current_user.id

    all_approved = prior_approved + [payment]
    if cycle is not None:
        cycle_service.recalculate_latest_approved_balance(cycle, all_approved)
        balance_amount = payment.balance_amount
    else:
        balance_amount = max(0, current_balance - paid_amount)
        payment.balance_amount = balance_amount

    await db.commit()

    clear_cache("admin_units_list")
    clear_cache("admin_registration_payments")
    clear_cache("admin_dashboard_v2")

    return {
        "message": "Payment approved successfully",
        "id": payment_id,
        "paid_amount": paid_amount,
        "balance_amount": balance_amount,
    }


@router.post("/registration-payments/{payment_id}/reject", response_model=dict)
async def reject_registration_payment(
    payment_id: int,
    rejection_note: str = Body(..., embed=True),
    current_user: CustomUser = Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Reject a unit registration payment proof submission with a note."""
    stmt = select(UnitRegistrationPayment).where(UnitRegistrationPayment.id == payment_id)
    result = await db.execute(stmt)
    payment = result.scalar_one_or_none()

    if not payment:
        raise HTTPException(status_code=404, detail="Payment submission not found")

    if not rejection_note.strip():
        raise HTTPException(status_code=400, detail="Rejection note is required")

    payment.status = PaymentProofStatus.REJECTED
    payment.rejection_note = rejection_note.strip()
    payment.reviewed_at = now_ist()
    payment.reviewed_by_id = current_user.id
    await db.commit()

    clear_cache("admin_units_list")
    clear_cache("admin_registration_payments")
    clear_cache("admin_dashboard_v2")

    return {"message": "Payment rejected", "id": payment_id}


@router.post("/payment-qr", response_model=dict)
async def upload_payment_qr(
    file: UploadFile = File(..., description="QR code image for unit registration payment"),
    current_user: CustomUser = Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Upload or replace the QR code used for unit registration fee collection."""
    from sqlalchemy.ext.asyncio import AsyncSession as _AS
    from app.common.storage import delete_file

    # Get or create site settings
    site_stmt = select(SiteSettings).limit(1)
    site_result = await db.execute(site_stmt)
    site = site_result.scalar_one_or_none()
    if not site:
        site = SiteSettings(app_name="CSI MKD YOUTH MOVEMENT")
        db.add(site)
        await db.flush()

    # Delete old QR if exists
    if site.payment_qr_url:
        delete_file(site.payment_qr_url)

    object_key, _ = save_upload_file(file, subdir="site/payment-qr")
    site.payment_qr_url = object_key
    await db.commit()
    clear_cache(SITE_SETTINGS_CACHE_KEY)

    return {
        "message": "Payment QR code uploaded successfully",
        "url": get_public_file_url(object_key),
    }


# Unit Details - MUST be last due to path parameter matching
async def _load_members_for_export(
    db: AsyncSession,
    *,
    registered_user_id: Optional[int] = None,
    district_id: Optional[int] = None,
) -> List[dict]:
    stmt = select(UnitMembers).options(
        selectinload(UnitMembers.registered_user).selectinload(CustomUser.unit_name).selectinload(UnitName.district),
        *MEMBER_RESIDENCE_LOAD_OPTIONS,
    )
    if registered_user_id is not None:
        stmt = stmt.where(UnitMembers.registered_user_id == registered_user_id)
    if district_id is not None:
        stmt = stmt.join(CustomUser, CustomUser.id == UnitMembers.registered_user_id).join(
            UnitName, UnitName.id == CustomUser.unit_name_id
        ).where(UnitName.clergy_district_id == district_id)
    stmt = stmt.order_by(UnitMembers.name)
    result = await db.execute(stmt)
    members = list(result.scalars().all())
    rows: List[dict] = []
    for member in members:
        unit_name = ""
        district = ""
        if member.registered_user and member.registered_user.unit_name:
            unit_name = member.registered_user.unit_name.name or ""
            if member.registered_user.unit_name.district:
                district = member.registered_user.unit_name.district.name or ""
        rows.append(member_export_row(member, unit_name=unit_name, district=district))
    return rows


async def _load_officials_for_export(
    db: AsyncSession,
    *,
    registered_user_id: Optional[int] = None,
    district_id: Optional[int] = None,
) -> List[dict]:
    stmt = select(UnitOfficials).options(
        selectinload(UnitOfficials.registered_user).selectinload(CustomUser.unit_name)
    )
    if registered_user_id is not None:
        stmt = stmt.where(UnitOfficials.registered_user_id == registered_user_id)
    if district_id is not None:
        stmt = stmt.join(CustomUser, CustomUser.id == UnitOfficials.registered_user_id).join(
            UnitName, UnitName.id == CustomUser.unit_name_id
        ).where(UnitName.clergy_district_id == district_id)
    result = await db.execute(stmt)
    officials = list(result.scalars().all())
    return [
        {
            "unit_name": (
                official.registered_user.unit_name.name
                if official.registered_user and official.registered_user.unit_name
                else ""
            ),
            "president_name": official.president_name or "",
            "president_phone": official.president_phone or "",
            "vice_president_name": official.vice_president_name or "",
            "vice_president_phone": official.vice_president_phone or "",
            "secretary_name": official.secretary_name or "",
            "secretary_phone": official.secretary_phone or "",
            "joint_secretary_name": official.joint_secretary_name or "",
            "joint_secretary_phone": official.joint_secretary_phone or "",
            "treasurer_name": official.treasurer_name or "",
            "treasurer_phone": official.treasurer_phone or "",
        }
        for official in officials
    ]


async def _load_councilors_for_export(
    db: AsyncSession,
    *,
    registered_user_id: Optional[int] = None,
    district_id: Optional[int] = None,
) -> List[dict]:
    stmt = select(UnitCouncilor).options(
        selectinload(UnitCouncilor.registered_user).selectinload(CustomUser.unit_name),
        selectinload(UnitCouncilor.unit_member),
    )
    if registered_user_id is not None:
        stmt = stmt.where(UnitCouncilor.registered_user_id == registered_user_id)
    if district_id is not None:
        stmt = stmt.join(CustomUser, CustomUser.id == UnitCouncilor.registered_user_id).join(
            UnitName, UnitName.id == CustomUser.unit_name_id
        ).where(UnitName.clergy_district_id == district_id)
    result = await db.execute(stmt)
    councilors = list(result.scalars().all())
    return [
        {
            "name": councilor.unit_member.name if councilor.unit_member else "",
            "number": councilor.unit_member.number if councilor.unit_member else "",
            "unit_name": (
                councilor.registered_user.unit_name.name
                if councilor.registered_user and councilor.registered_user.unit_name
                else ""
            ),
        }
        for councilor in councilors
    ]


@router.get("/export/{export_type}")
async def export_unit_data(
    export_type: str,
    id: Optional[int] = Query(None, description="Unit user id or district id depending on export type"),
    registration_year: Optional[int] = Query(
        None, description="Registration year to scope the 'units' export type to (defaults to current year)"
    ),
    current_user: CustomUser = Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Export unit, member, official, or councilor data."""
    export_type = export_type.strip().lower()
    timestamp = format_timestamp_ist()
    media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

    if export_type == "members":
        rows = await _load_members_for_export(db)
        export_file = create_members_excel(rows)
        filename = f"unit_members_{timestamp}.xlsx"
    elif export_type == "unit":
        if id is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unit id is required")
        rows = await _load_members_for_export(db, registered_user_id=id)
        export_file = create_members_excel(rows)
        filename = f"unit_{id}_members_{timestamp}.xlsx"
    elif export_type == "officials":
        rows = await _load_officials_for_export(db)
        export_file = create_officials_excel(rows)
        filename = f"unit_officials_{timestamp}.xlsx"
    elif export_type == "councilors":
        rows = await _load_councilors_for_export(db)
        export_file = create_councilors_excel(rows)
        filename = f"unit_councilors_{timestamp}.xlsx"
    elif export_type == "district-officials":
        if id is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="District id is required")
        rows = await _load_officials_for_export(db, district_id=id)
        export_file = create_officials_excel(rows)
        filename = f"district_{id}_officials_{timestamp}.xlsx"
    elif export_type == "district-councilors":
        if id is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="District id is required")
        rows = await _load_councilors_for_export(db, district_id=id)
        export_file = create_councilors_excel(rows)
        filename = f"district_{id}_councilors_{timestamp}.xlsx"
    elif export_type == "unit-officials":
        if id is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unit id is required")
        rows = await _load_officials_for_export(db, registered_user_id=id)
        export_file = create_officials_excel(rows)
        filename = f"unit_{id}_officials_{timestamp}.xlsx"
    elif export_type == "unit-councilors":
        if id is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unit id is required")
        rows = await _load_councilors_for_export(db, registered_user_id=id)
        export_file = create_councilors_excel(rows)
        filename = f"unit_{id}_councilors_{timestamp}.xlsx"
    elif export_type == "units":
        year = registration_year or await cycle_service.get_current_registration_year(db)
        rows = await load_units_summary_for_export(
            db, registration_year=year, exclude_user_id=current_user.id
        )
        export_file = create_units_summary_csv(rows)
        filename = f"units_summary_{year}_{timestamp}.csv"
        media_type = "text/csv"
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported export type: {export_type}",
        )

    return StreamingResponse(
        export_file,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/{user_id}/complete-registration", response_model=dict)
async def complete_unit_registration(
    user_id: int,
    current_user: CustomUser = Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Mark a unit's current-season registration cycle as completed after admin approval."""
    cycle = await cycle_service.admin_complete_registration(db, user_id)
    return {
        "message": "Registration marked as completed.",
        "user_id": user_id,
        "registration_year": cycle.registration_year,
        "status": cycle.status,
    }


@router.post("/bulk-complete-legacy", response_model=dict)
async def bulk_complete_legacy_registrations(
    current_user: CustomUser = Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Bulk-mark older-season registration cycles as completed (no admin approval)."""
    updated = await cycle_service.bulk_complete_legacy_registrations(db, dry_run=False)
    clear_cache("admin_dashboard_v2")
    return {
        "message": "Legacy registration cycles updated.",
        "updated_count": len(updated),
        "updated": updated,
    }


@router.get("/{unit_id}", response_model=dict)
async def view_unit_details(
    unit_id: int,
    current_user: CustomUser = Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_db),
):
    """View individual unit details."""
    stmt = select(CustomUser).where(CustomUser.id == unit_id).options(
        selectinload(CustomUser.unit_name).selectinload(UnitName.district),
        selectinload(CustomUser.clergy_district),
    )
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Unit not found"
        )
    
    # Get officials
    stmt = select(UnitOfficials).where(UnitOfficials.registered_user_id == unit_id)
    result = await db.execute(stmt)
    officials = result.scalar_one_or_none()
    
    # Get councilors
    stmt = (
        select(UnitCouncilor)
        .where(UnitCouncilor.registered_user_id == unit_id)
        .options(selectinload(UnitCouncilor.unit_member))
    )
    result = await db.execute(stmt)
    councilors = list(result.scalars().all())
    
    # Get members
    stmt = (
        select(UnitMembers)
        .options(*MEMBER_RESIDENCE_LOAD_OPTIONS)
        .where(UnitMembers.registered_user_id == unit_id)
        .order_by(UnitMembers.name)
    )
    result = await db.execute(stmt)
    members = list(result.scalars().all())

    current_year = await cycle_service.get_current_registration_year(db)
    cycle_result = await db.execute(
        select(UnitRegistrationCycle).where(
            UnitRegistrationCycle.registered_user_id == unit_id,
            UnitRegistrationCycle.registration_year == current_year,
        )
    )
    cycle = cycle_result.scalar_one_or_none()

    registration_year = current_year
    
    # Convert officials to dict
    officials_dict = None
    if officials:
        officials_dict = {
            "id": officials.id,
            "president_designation": officials.president_designation,
            "president_name": officials.president_name,
            "president_phone": officials.president_phone,
            "vice_president_name": officials.vice_president_name,
            "vice_president_phone": officials.vice_president_phone,
            "secretary_name": officials.secretary_name,
            "secretary_phone": officials.secretary_phone,
            "joint_secretary_name": officials.joint_secretary_name,
            "joint_secretary_phone": officials.joint_secretary_phone,
            "treasurer_name": officials.treasurer_name,
            "treasurer_phone": officials.treasurer_phone,
        }
    
    # Convert councilors to list of dicts
    councilors_list = [
        {
            "id": c.id,
            "unit_member_id": c.unit_member_id,
            "member_name": c.unit_member.name if c.unit_member else None,
            "member_phone": c.unit_member.number if c.unit_member else None,
            "member_gender": c.unit_member.gender if c.unit_member else None,
        }
        for c in councilors
    ]
    
    members_list = [serialize_member(m) for m in members]
    member_count = len(members)

    settings_result = await db.execute(select(SiteSettings).limit(1))
    settings = settings_result.scalar_one_or_none()
    unit_registration_fee = (
        settings.unit_registration_fee
        if settings and settings.unit_registration_fee is not None
        else 100
    )
    unit_member_fee = (
        settings.unit_member_fee
        if settings and settings.unit_member_fee is not None
        else 10
    )
    total_amount = unit_registration_fee + member_count * unit_member_fee
    if cycle and cycle.total_fee_at_submit is not None:
        total_amount = cycle.total_fee_at_submit

    clergy_district_name = None
    if user.unit_name and user.unit_name.district:
        clergy_district_name = user.unit_name.district.name
    elif user.clergy_district:
        clergy_district_name = user.clergy_district.name
    
    return {
        "user": {
            "id": user.id,
            "username": user.username,
            "unit_name": user.unit_name.name if user.unit_name else None,
            "clergy_district_name": clergy_district_name,
        },
        "registration_year": registration_year,
        "cycle_status": cycle.status if cycle else None,
        "path_type": cycle.path_type if cycle else None,
        "officials": officials_dict,
        "councilors": councilors_list,
        "members": members_list,
        "member_count": member_count,
        "unit_registration_fee": unit_registration_fee,
        "unit_member_fee": unit_member_fee,
        "total_amount": total_amount,
    }

