"""Conference service layer - business logic for conference operations."""

from types import SimpleNamespace
from typing import List, Optional, Dict, Any
from collections import defaultdict
from sqlalchemy import select, and_, or_, func, delete, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from fastapi import HTTPException, UploadFile, status

from app.auth.models import (
    CustomUser,
    UnitMembers,
    UnitName,
    ClergyDistrict,
    UserType,
)
from app.conference.models import (
    Conference,
    ConferenceRegistrationData,
    ConferenceDelegate,
    ConferencePayment,
    ConferenceSettings,
    FoodPreference,
    PaymentStatusEnum,
)
from app.conference.schemas import (
    ConferenceCreate,
    ConferenceUpdate,
    DistrictOfficialCreate,
    FoodPreferenceCreate,
    ConferenceSettingsUpdate,
)
from app.common.security import get_password_hash
from app.common.datetime_utils import now_ist
from app.common.storage import save_upload_file
from app.admin.models import SiteSettings
from app.admin.routers.site import get_public_file_url
from app.units.registration_cycle_service import (
    compute_total_paid_for_approved_payments,
    recalculate_latest_approved_balance,
)


DEFAULT_DELEGATE_FEE = 300


async def get_or_create_conference_settings(db: AsyncSession) -> ConferenceSettings:
    """Return the singleton conference settings row, creating it if missing."""
    result = await db.execute(select(ConferenceSettings).limit(1))
    settings = result.scalar_one_or_none()
    if settings is None:
        settings = ConferenceSettings(delegate_fee=DEFAULT_DELEGATE_FEE)
        db.add(settings)
        await db.commit()
        await db.refresh(settings)
    return settings


async def get_delegate_fee(db: AsyncSession) -> int:
    settings = await get_or_create_conference_settings(db)
    return settings.delegate_fee


async def update_conference_settings(
    db: AsyncSession,
    data: ConferenceSettingsUpdate,
) -> ConferenceSettings:
    settings = await get_or_create_conference_settings(db)
    settings.delegate_fee = data.delegate_fee
    await db.commit()
    await db.refresh(settings)
    return settings


# Conference Management Functions
async def create_conference(
    db: AsyncSession,
    data: ConferenceCreate,
) -> Conference:
    """
    Create a new conference.
    
    Args:
        db: Database session
        data: Conference creation data
    
    Returns:
        Created conference
    """
    conference = Conference(
        title=data.title,
        details=data.details,
        status="Active",
    )
    
    db.add(conference)
    await db.commit()
    await db.refresh(conference)
    
    return conference


async def update_conference(
    db: AsyncSession,
    conference_id: int,
    data: ConferenceUpdate,
) -> Conference:
    """
    Update a conference.
    
    Args:
        db: Database session
        conference_id: ID of the conference
        data: Update data
    
    Returns:
        Updated conference
    
    Raises:
        HTTPException: If conference not found
    """
    stmt = select(Conference).where(Conference.id == conference_id)
    result = await db.execute(stmt)
    conference = result.scalar_one_or_none()
    
    if not conference:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conference not found"
        )
    
    if data.title:
        conference.title = data.title
    if data.details:
        conference.details = data.details
    if data.status:
        conference.status = data.status.value
    
    await db.commit()
    await db.refresh(conference)
    
    return conference


async def delete_conference(
    db: AsyncSession,
    conference_id: int,
) -> bool:
    """
    Delete a conference and its related rows (delegates, payments, food prefs).
    Clears custom_user.conference_id for officials assigned to this conference.
    """
    stmt = select(Conference).where(Conference.id == conference_id)
    result = await db.execute(stmt)
    conference = result.scalar_one_or_none()
    
    if not conference:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conference not found"
        )

    # Child tables FK to conference without ON DELETE CASCADE
    await db.execute(
        delete(ConferenceDelegate).where(ConferenceDelegate.conference_id == conference_id)
    )
    await db.execute(
        delete(ConferencePayment).where(ConferencePayment.conference_id == conference_id)
    )
    await db.execute(
        delete(FoodPreference).where(FoodPreference.conference_id == conference_id)
    )
    await db.execute(
        update(CustomUser)
        .where(CustomUser.conference_id == conference_id)
        .values(conference_id=None)
    )

    await db.delete(conference)
    await db.commit()
    
    return True


async def get_conference_by_id(
    db: AsyncSession,
    conference_id: int,
) -> Conference:
    """Get conference by ID."""
    stmt = select(Conference).where(Conference.id == conference_id)
    result = await db.execute(stmt)
    conference = result.scalar_one_or_none()
    
    if not conference:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conference not found"
        )
    
    return conference


async def get_active_conferences(
    db: AsyncSession,
) -> List[Conference]:
    """Get all active conferences."""
    stmt = select(Conference).where(Conference.status == "Active")
    result = await db.execute(stmt)
    return list(result.scalars().all())


# District Official Management
async def add_conference_delegate_official(
    db: AsyncSession,
    conference_id: int,
    data: DistrictOfficialCreate,
) -> CustomUser:
    """
    Create a district official account for conference delegation.
    
    Username will be the DISTRICT NAME (e.g., 'THIRUVALLA', 'ADOOR') to enable
    district-wise login for Kalamela and Conference modules.
    
    Args:
        db: Database session
        conference_id: ID of the conference
        data: Official creation data
    
    Returns:
        Created official user
    
    Raises:
        HTTPException: If conference or member not found, or district already has an official
    """
    # Verify conference exists
    conference = await get_conference_by_id(db, conference_id)
    
    # Get member
    stmt = select(UnitMembers).where(UnitMembers.id == data.member_id)
    result = await db.execute(stmt)
    member = result.scalar_one_or_none()
    
    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Unit member not found"
        )
    
    # Get member's district
    stmt = select(CustomUser).where(CustomUser.id == member.registered_user_id).options(
        selectinload(CustomUser.unit_name).selectinload(UnitName.district)
    )
    result = await db.execute(stmt)
    registered_user = result.scalar_one()
    
    member_district_id = registered_user.unit_name.clergy_district_id
    
    # Get district
    stmt = select(ClergyDistrict).where(ClergyDistrict.id == member_district_id)
    result = await db.execute(stmt)
    district = result.scalar_one()
    
    # Check if district already has an official for this conference
    stmt = select(CustomUser).where(
        and_(
            CustomUser.clergy_district_id == member_district_id,
            CustomUser.user_type == UserType.DISTRICT_OFFICIAL,
            CustomUser.conference_id == conference_id
        )
    )
    result = await db.execute(stmt)
    existing_official = result.scalar_one_or_none()
    
    if existing_official:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"District '{district.name}' already has an official for this conference. "
                   f"Use the update endpoint to modify or reset password."
        )
    
    # Determine max conference member count based on district
    under_30_list = ['ADOOR', 'KOTTAYAM', 'KUMPALAMPOIKA', 'MALLAPPALLY', 'MAVELIKKARA', 'PALLOM', 'THIRUVALLA']
    under_25_list = ['ELANTHOOR', 'EATTUMANOOR', 'KODUKULANJI', 'MUNDAKKAYAM', 'PUNNAVELY']
    
    max_conference_member_count = 20  # default
    if district.name in under_30_list:
        max_conference_member_count = 25
    elif district.name in under_25_list:
        max_conference_member_count = 20
    
    # Reuse User-Management district official if username already exists for this district
    stmt = select(CustomUser).where(CustomUser.username == district.name)
    result = await db.execute(stmt)
    existing_username = result.scalar_one_or_none()

    if existing_username:
        if (
            existing_username.user_type != UserType.DISTRICT_OFFICIAL
            or existing_username.clergy_district_id != member_district_id
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Username '{district.name}' is already in use"
            )
        official_user = existing_username
        official_user.conference_id = conference_id
        official_user.first_name = member.name
        official_user.phone_number = str(member.number)
        official_user.conference_official_count = 5
        official_user.conference_member_count = max_conference_member_count
        official_user.is_active = True
        official_user.hashed_password = get_password_hash(str(member.number))
        await db.flush()
    else:
        # Create official user with DISTRICT NAME as username
        official_user = CustomUser(
            username=district.name,
            email=f"{district.name.lower().replace(' ', '_')}@district.local",
            first_name=member.name,
            phone_number=str(member.number),
            conference_id=conference_id,
            clergy_district_id=member_district_id,
            conference_official_count=5,
            conference_member_count=max_conference_member_count,
            user_type=UserType.DISTRICT_OFFICIAL,
            hashed_password=get_password_hash(str(member.number)),
            is_active=True,
        )
        db.add(official_user)
        await db.flush()

    # Ensure registration + official delegate rows exist for this conference
    stmt = select(ConferenceRegistrationData).where(
        ConferenceRegistrationData.district_official_id == official_user.id
    )
    result = await db.execute(stmt)
    if result.scalar_one_or_none() is None:
        db.add(ConferenceRegistrationData(
            district_official_id=official_user.id,
            status="Registration Started",
        ))

    stmt = select(ConferenceDelegate).where(
        and_(
            ConferenceDelegate.conference_id == conference_id,
            ConferenceDelegate.officials_id == official_user.id,
            ConferenceDelegate.members_id.is_(None),
        )
    )
    result = await db.execute(stmt)
    if result.scalar_one_or_none() is None:
        db.add(ConferenceDelegate(
            conference_id=conference_id,
            officials_id=official_user.id,
            members_id=None,
        ))

    await db.flush()
    await recalculate_district_ledger(db, conference_id, member_district_id)
    await db.commit()
    await db.refresh(official_user)

    return official_user


async def update_district_official(
    db: AsyncSession,
    official_id: int,
    conference_official_count: int,
    conference_member_count: int,
) -> CustomUser:
    """
    Update district official and propagate counts to all district users.
    
    Args:
        db: Database session
        official_id: ID of the official to update
        conference_official_count: New official count
        conference_member_count: New member count
    
    Returns:
        Updated official
    
    Raises:
        HTTPException: If official not found
    """
    # Get official
    stmt = select(CustomUser).where(CustomUser.id == official_id)
    result = await db.execute(stmt)
    official = result.scalar_one_or_none()
    
    if not official:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="District official not found"
        )
    
    # Update official
    official.conference_official_count = conference_official_count
    official.conference_member_count = conference_member_count
    
    # Get all users in the same district
    user_district_id = official.clergy_district_id
    
    stmt = select(CustomUser).where(CustomUser.clergy_district_id == user_district_id)
    result = await db.execute(stmt)
    district_users = list(result.scalars().all())
    
    # Update all district users
    for user in district_users:
        user.conference_official_count = conference_official_count
        user.conference_member_count = conference_member_count
    
    await db.commit()
    await db.refresh(official)
    
    return official


async def delete_district_official(
    db: AsyncSession,
    official_id: int,
) -> bool:
    """Delete a district official."""
    stmt = select(CustomUser).where(CustomUser.id == official_id)
    result = await db.execute(stmt)
    official = result.scalar_one_or_none()
    
    if not official:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="District official not found"
        )

    district_id = official.clergy_district_id
    conference_id = official.conference_id
    await db.delete(official)
    await db.flush()
    if conference_id is not None:
        await recalculate_district_ledger(db, conference_id, district_id)
    await db.commit()

    return True


# Delegate Management
async def add_conference_delegate_member(
    db: AsyncSession,
    conference_id: int,
    member_id: int,
    official_user_id: int,
) -> ConferenceDelegate:
    """
    Add a member as a conference delegate.
    
    Args:
        db: Database session
        conference_id: ID of the conference
        member_id: ID of the member to add
        official_user_id: ID of the official adding the member
    
    Returns:
        Created delegate entry
    
    Raises:
        HTTPException: If member already delegated or limits exceeded
    """
    # Get official user
    stmt = select(CustomUser).where(CustomUser.id == official_user_id)
    result = await db.execute(stmt)
    official = result.scalar_one()
    
    # Check if member already delegated
    stmt = select(ConferenceDelegate).where(
        and_(
            ConferenceDelegate.conference_id == conference_id,
            ConferenceDelegate.members_id == member_id
        )
    )
    result = await db.execute(stmt)
    existing = result.scalar_one_or_none()
    
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Member is already a delegate"
        )
    
    # Check delegate count limits
    stmt = select(func.count()).select_from(ConferenceDelegate).where(
        and_(
            ConferenceDelegate.conference_id == conference_id,
            ConferenceDelegate.officials_id == official_user_id,
            ConferenceDelegate.members_id.isnot(None)
        )
    )
    result = await db.execute(stmt)
    current_count = result.scalar()
    
    if current_count >= official.conference_member_count:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Maximum delegate count reached"
        )
    
    # Create delegate
    delegate = ConferenceDelegate(
        conference_id=conference_id,
        officials_id=official_user_id,
        members_id=member_id,
    )
    
    db.add(delegate)
    await db.flush()
    await recalculate_district_ledger(
        db, conference_id, official.clergy_district_id
    )
    await db.commit()
    await db.refresh(delegate)

    return delegate


async def remove_conference_delegate_member(
    db: AsyncSession,
    member_id: int,
    conference_id: int,
) -> bool:
    """Remove a member from conference delegates."""
    stmt = select(ConferenceDelegate).where(
        and_(
            ConferenceDelegate.conference_id == conference_id,
            ConferenceDelegate.members_id == member_id
        )
    )
    result = await db.execute(stmt)
    delegate = result.scalar_one_or_none()
    
    if not delegate:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Delegate member not found"
        )
    
    official_result = await db.execute(
        select(CustomUser.clergy_district_id).where(CustomUser.id == delegate.officials_id)
    )
    district_id = official_result.scalar_one_or_none()
    await db.delete(delegate)
    await db.flush()
    if district_id is not None:
        await recalculate_district_ledger(db, conference_id, district_id)
    await db.commit()

    return True


PENDING_PROOF_STATUSES = (PaymentStatusEnum.PROOF_UPLOADED, PaymentStatusEnum.PENDING)


def approved_proofs(payments: list[ConferencePayment]) -> list[ConferencePayment]:
    return [p for p in payments if p.status == PaymentStatusEnum.PAID]


def pending_proofs(payments: list[ConferencePayment]) -> list[ConferencePayment]:
    return [p for p in payments if p.status in PENDING_PROOF_STATUSES]


def build_conference_payment_summary(
    *,
    fee_owed: int,
    official_count: int,
    member_count: int,
    approved: list[ConferencePayment],
) -> dict[str, Any]:
    cycle = SimpleNamespace(
        total_fee_at_submit=fee_owed,
        member_count_at_submit=official_count + member_count,
    )
    if approved:
        recalculate_latest_approved_balance(cycle, approved)
    total_paid = compute_total_paid_for_approved_payments(approved, fee_owed=fee_owed)
    balance_due = max(0, fee_owed - total_paid)
    payment_credit = max(0, total_paid - fee_owed)
    return {
        "official_count": official_count,
        "member_count": member_count,
        "delegate_count": official_count + member_count,
        "fee_owed": fee_owed,
        "total_paid": total_paid,
        "balance_due": balance_due,
        "payment_credit": payment_credit,
        "is_fully_paid": bool(approved) and balance_due == 0 and total_paid > 0,
    }


def overall_conference_status(
    payments: list[ConferencePayment],
    summary: dict[str, Any],
) -> str:
    approved = approved_proofs(payments)
    pending = pending_proofs(payments)
    if approved:
        if summary["balance_due"] > 0:
            return "partial"
        return "paid"
    if pending:
        return "pending"
    if payments:
        return "declined"
    return "not_submitted"


def has_blocking_pending(
    payments: list[ConferencePayment],
    *,
    fee_owed: int,
) -> bool:
    pending = pending_proofs(payments)
    if not pending:
        return False
    for payment in pending:
        if payment.total_amount is None or payment.total_amount <= fee_owed:
            return True
    return False


async def district_delegate_counts(
    db: AsyncSession,
    conference_id: int,
    district_id: int,
) -> dict[str, int]:
    official_result = await db.execute(
        select(func.count())
        .select_from(CustomUser)
        .where(
            CustomUser.clergy_district_id == district_id,
            CustomUser.user_type == UserType.DISTRICT_OFFICIAL,
        )
    )
    official_count = official_result.scalar() or 0

    member_result = await db.execute(
        select(func.count())
        .select_from(ConferenceDelegate)
        .join(CustomUser, ConferenceDelegate.officials_id == CustomUser.id)
        .where(
            ConferenceDelegate.conference_id == conference_id,
            ConferenceDelegate.members_id.isnot(None),
            CustomUser.clergy_district_id == district_id,
        )
    )
    member_count = member_result.scalar() or 0
    fee = await get_delegate_fee(db)
    fee_owed = (official_count + member_count) * fee
    return {
        "official_count": official_count,
        "member_count": member_count,
        "fee_owed": fee_owed,
        "delegate_fee": fee,
    }


async def get_district_payments(
    db: AsyncSession,
    conference_id: int,
    district_id: int,
) -> list[ConferencePayment]:
    result = await db.execute(
        select(ConferencePayment)
        .where(
            ConferencePayment.conference_id == conference_id,
            ConferencePayment.clergy_district_id == district_id,
        )
        .order_by(ConferencePayment.date.asc())
    )
    return list(result.scalars().all())


async def get_site_payment_qr_url(db: AsyncSession) -> Optional[str]:
    result = await db.execute(select(SiteSettings).limit(1))
    site = result.scalar_one_or_none()
    if site and site.payment_qr_url:
        return get_public_file_url(site.payment_qr_url)
    return None


def serialize_conference_proof(payment: ConferencePayment) -> dict[str, Any]:
    return {
        "id": payment.id,
        "file_url": get_public_file_url(payment.proof_path) if payment.proof_path else None,
        "total_amount": payment.total_amount,
        "balance_amount": payment.balance_amount,
        "approved_paid_amount": payment.approved_paid_amount,
        "status": payment.status.value if payment.status else None,
        "rejection_note": payment.rejection_note,
        "payment_reference": payment.payment_reference,
        "submitted_at": payment.date.isoformat() if payment.date else None,
        "reviewed_at": payment.reviewed_at.isoformat() if payment.reviewed_at else None,
        "uploaded_by_id": payment.uploaded_by_id,
    }


async def get_district_payment_ledger(
    db: AsyncSession,
    conference_id: int,
    district_id: int,
) -> dict[str, Any]:
    counts = await district_delegate_counts(db, conference_id, district_id)
    payments = await get_district_payments(db, conference_id, district_id)
    approved = approved_proofs(payments)
    summary = build_conference_payment_summary(
        fee_owed=counts["fee_owed"],
        official_count=counts["official_count"],
        member_count=counts["member_count"],
        approved=approved,
    )
    latest_rejection = None
    for payment in payments:
        if payment.status == PaymentStatusEnum.DECLINED and payment.rejection_note:
            latest_rejection = payment.rejection_note
    return {
        **counts,
        **summary,
        "overall_status": overall_conference_status(payments, summary),
        "latest_rejection_note": latest_rejection,
        "has_blocking_pending": has_blocking_pending(payments, fee_owed=counts["fee_owed"]),
        "qr_url": await get_site_payment_qr_url(db),
        "submissions": [serialize_conference_proof(p) for p in payments],
        "payments": payments,
    }


def ledger_api_payload(ledger: dict[str, Any]) -> dict[str, Any]:
    payload = dict(ledger)
    payload.pop("payments", None)
    return payload


def overall_status_legacy(status: str) -> Optional[str]:
    return {
        "paid": "PAID",
        "partial": "PARTIAL",
        "pending": "PENDING",
        "declined": "DECLINED",
        "not_submitted": None,
    }.get(status)


async def supersede_stale_pending_payments(
    db: AsyncSession,
    payments: list[ConferencePayment],
    *,
    fee_owed: int,
) -> int:
    stale = [
        p
        for p in pending_proofs(payments)
        if p.total_amount is not None and p.total_amount > fee_owed
    ]
    for payment in stale:
        payment.status = PaymentStatusEnum.DECLINED
        payment.rejection_note = (
            "Superseded — conference fee was revised after a delegate update. "
            "Please submit a new proof for the updated amount."
        )
        payment.reviewed_at = now_ist()
    return len(stale)


async def recalculate_district_ledger(
    db: AsyncSession,
    conference_id: int,
    district_id: Optional[int],
) -> None:
    if district_id is None:
        return
    counts = await district_delegate_counts(db, conference_id, district_id)
    payments = await get_district_payments(db, conference_id, district_id)
    approved = approved_proofs(payments)
    if approved:
        cycle = SimpleNamespace(total_fee_at_submit=counts["fee_owed"])
        recalculate_latest_approved_balance(cycle, approved)
    await supersede_stale_pending_payments(db, payments, fee_owed=counts["fee_owed"])


async def create_conference_payment(
    db: AsyncSession,
    conference_id: int,
    user: CustomUser,
    file: UploadFile,
    payment_reference: Optional[str] = None,
) -> ConferencePayment:
    if not user.clergy_district_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="District official has no district assigned.",
        )

    await recalculate_district_ledger(db, conference_id, user.clergy_district_id)
    ledger = await get_district_payment_ledger(db, conference_id, user.clergy_district_id)
    fee_owed = ledger["fee_owed"]

    if ledger["is_fully_paid"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Payment for this district has already been fully approved.",
        )
    if fee_owed <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Add delegates before submitting payment.",
        )
    if ledger["has_blocking_pending"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "A payment proof is already awaiting admin review. "
                "Please wait for approval or rejection before submitting another."
            ),
        )

    object_key, _ = save_upload_file(file, subdir="conference/payments")
    payment = ConferencePayment(
        conference_id=conference_id,
        clergy_district_id=user.clergy_district_id,
        amount_to_pay=fee_owed,
        total_amount=fee_owed,
        uploaded_by_id=user.id,
        proof_path=object_key,
        payment_reference=payment_reference,
        status=PaymentStatusEnum.PROOF_UPLOADED,
    )
    db.add(payment)
    await db.commit()
    await db.refresh(payment)
    return payment


async def approve_conference_payment(
    db: AsyncSession,
    payment_id: int,
    paid_amount: int,
    admin_id: int,
    conference_id: Optional[int] = None,
) -> dict[str, Any]:
    if paid_amount < 0:
        raise HTTPException(status_code=400, detail="Paid amount cannot be negative")

    result = await db.execute(
        select(ConferencePayment).where(ConferencePayment.id == payment_id)
    )
    payment = result.scalar_one_or_none()
    if not payment or (conference_id is not None and payment.conference_id != conference_id):
        raise HTTPException(status_code=404, detail="Payment submission not found")
    if payment.status not in PENDING_PROOF_STATUSES:
        raise HTTPException(status_code=400, detail="Only pending proofs can be approved")
    if payment.clergy_district_id is None:
        raise HTTPException(status_code=400, detail="Payment is missing a district")

    counts = await district_delegate_counts(
        db, payment.conference_id, payment.clergy_district_id
    )
    fee_owed = counts["fee_owed"]
    payments = await get_district_payments(
        db, payment.conference_id, payment.clergy_district_id
    )
    prior_approved = [p for p in approved_proofs(payments) if p.id != payment.id]
    total_paid_so_far = compute_total_paid_for_approved_payments(
        prior_approved, fee_owed=fee_owed
    )
    current_balance = max(0, fee_owed - total_paid_so_far)
    if paid_amount > current_balance:
        raise HTTPException(
            status_code=400,
            detail="Paid amount cannot exceed the remaining balance",
        )

    payment.status = PaymentStatusEnum.PAID
    payment.approved_paid_amount = paid_amount
    payment.rejection_note = None
    payment.reviewed_at = now_ist()
    payment.reviewed_by_id = admin_id

    all_approved = prior_approved + [payment]
    cycle = SimpleNamespace(total_fee_at_submit=fee_owed)
    recalculate_latest_approved_balance(cycle, all_approved)
    await db.commit()

    return {
        "message": "Payment approved successfully",
        "id": payment_id,
        "paid_amount": paid_amount,
        "balance_amount": payment.balance_amount,
    }


async def decline_conference_payment(
    db: AsyncSession,
    payment_id: int,
    rejection_note: str,
    admin_id: int,
    conference_id: Optional[int] = None,
) -> dict[str, Any]:
    if not rejection_note.strip():
        raise HTTPException(status_code=400, detail="Rejection note is required")

    result = await db.execute(
        select(ConferencePayment).where(ConferencePayment.id == payment_id)
    )
    payment = result.scalar_one_or_none()
    if not payment or (conference_id is not None and payment.conference_id != conference_id):
        raise HTTPException(status_code=404, detail="Payment submission not found")
    if payment.status not in PENDING_PROOF_STATUSES:
        raise HTTPException(status_code=400, detail="Only pending proofs can be declined")

    payment.status = PaymentStatusEnum.DECLINED
    payment.rejection_note = rejection_note.strip()
    payment.reviewed_at = now_ist()
    payment.reviewed_by_id = admin_id
    await db.commit()
    return {"message": "Payment declined", "id": payment_id}


# Food Preference Management
async def set_food_preference(
    db: AsyncSession,
    conference_id: int,
    user_id: int,
    data: FoodPreferenceCreate,
) -> FoodPreference:
    """Set or update food preferences for a district."""
    # Check if preference already exists
    stmt = select(FoodPreference).where(
        and_(
            FoodPreference.conference_id == conference_id,
            FoodPreference.uploaded_by_id == user_id
        )
    )
    result = await db.execute(stmt)
    existing = result.scalar_one_or_none()
    
    if existing:
        # Update existing
        existing.veg_count = data.veg_count
        existing.non_veg_count = data.non_veg_count
        await db.commit()
        await db.refresh(existing)
        return existing
    
    # Create new
    preference = FoodPreference(
        conference_id=conference_id,
        veg_count=data.veg_count,
        non_veg_count=data.non_veg_count,
        uploaded_by_id=user_id,
    )
    
    db.add(preference)
    await db.commit()
    await db.refresh(preference)
    
    return preference


# Aggregated Data Functions
async def get_all_conference_info(
    db: AsyncSession,
    conference_id: int,
) -> Dict[str, Any]:
    """
    Get aggregated conference information by district.
    
    Args:
        db: Database session
        conference_id: ID of the conference
    
    Returns:
        Dictionary with district-wise information
    """
    # Get all delegates for the conference
    stmt = select(ConferenceDelegate).where(
        ConferenceDelegate.conference_id == conference_id
    )
    result = await db.execute(stmt)
    delegates = list(result.scalars().all())
    
    # Aggregate by district
    district_info = defaultdict(lambda: {
        'officials': [],
        'members': [],
        'count_of_members': 0,
        'count_of_officials': 0,
        'count_of_male_members': 0,
        'count_of_female_members': 0,
        'count_of_male_officials': 0,
        'count_of_female_officials': 0,
        'count_of_total_male': 0,
        'count_of_total_female': 0,
        'total_count': 0,
        'veg_count': 0,
        'non_veg_count': 0,
        'seen_officials': set(),
    })
    
    for delegate in delegates:
        # Get official with district
        stmt = select(CustomUser).where(CustomUser.id == delegate.officials_id).options(
            selectinload(CustomUser.clergy_district)
        )
        result = await db.execute(stmt)
        official = result.scalar_one()
        
        district_name = official.clergy_district.name if official.clergy_district else 'Unknown District'
        official_district_id = official.clergy_district_id
        
        # Add unique officials
        if official.id not in district_info[district_name]['seen_officials']:
            # Get official's unit info
            stmt = select(UnitMembers).where(
                and_(
                    UnitMembers.name == official.first_name,
                    UnitMembers.registered_user.has(
                        CustomUser.unit_name.has(UnitName.clergy_district_id == official_district_id)
                    )
                )
            ).limit(1)
            result = await db.execute(stmt)
            unit_member_official = result.scalar_one_or_none()
            
            if unit_member_official:
                stmt = select(UnitName).where(
                    UnitName.id == unit_member_official.registered_user.unit_name_id
                )
                result = await db.execute(stmt)
                unit_name_obj = result.scalar_one()
                
                district_info[district_name]['officials'].append({
                    'name': official.first_name,
                    'phone': official.phone_number,
                    'id': official.id,
                    'unit': unit_name_obj.name,
                    'gender': unit_member_official.gender,
                })
                
                district_info[district_name]['seen_officials'].add(official.id)
                
                if unit_member_official.gender == 'M':
                    district_info[district_name]['count_of_male_officials'] += 1
                elif unit_member_official.gender in ['F', 'Female']:
                    district_info[district_name]['count_of_female_officials'] += 1
        
        # Get food preferences
        stmt = select(FoodPreference).where(
            and_(
                FoodPreference.conference_id == conference_id,
                FoodPreference.uploaded_by_id.in_(
                    select(CustomUser.id).where(CustomUser.clergy_district_id == official_district_id)
                )
            )
        ).order_by(FoodPreference.created_at.desc())
        result = await db.execute(stmt)
        food_pref = result.scalar_one_or_none()
        
        if food_pref:
            district_info[district_name]['veg_count'] = food_pref.veg_count or 0
            district_info[district_name]['non_veg_count'] = food_pref.non_veg_count or 0
        
        # Add member if present
        if delegate.members_id:
            stmt = select(UnitMembers).where(UnitMembers.id == delegate.members_id).options(
                selectinload(UnitMembers.registered_user).selectinload(CustomUser.unit_name)
            )
            result = await db.execute(stmt)
            member = result.scalar_one()
            
            district_info[district_name]['members'].append({
                'name': member.name,
                'phone': member.number,
                'id': member.id,
                'unit': member.registered_user.unit_name.name,
                'gender': member.gender,
            })
            district_info[district_name]['count_of_members'] += 1
            
            if member.gender == 'M':
                district_info[district_name]['count_of_male_members'] += 1
            elif member.gender == 'F':
                district_info[district_name]['count_of_female_members'] += 1
        
        # Update totals
        district_info[district_name]['count_of_total_male'] = (
            district_info[district_name]['count_of_male_officials'] +
            district_info[district_name]['count_of_male_members']
        )
        district_info[district_name]['count_of_total_female'] = (
            district_info[district_name]['count_of_female_officials'] +
            district_info[district_name]['count_of_female_members']
        )
        district_info[district_name]['total_count'] = (
            district_info[district_name]['count_of_total_male'] +
            district_info[district_name]['count_of_total_female']
        )
    
    # Convert to dict and remove seen_officials set
    result_dict = {}
    for district, info in district_info.items():
        info_copy = dict(info)
        info_copy.pop('seen_officials', None)
        result_dict[district] = info_copy
    
    return result_dict


async def conference_district_ids(
    db: AsyncSession,
    conference_id: int,
) -> list[int]:
    official_rows = await db.execute(
        select(CustomUser.clergy_district_id).where(
            CustomUser.user_type == UserType.DISTRICT_OFFICIAL,
            CustomUser.clergy_district_id.isnot(None),
            or_(
                CustomUser.conference_id == conference_id,
                CustomUser.id.in_(
                    select(ConferenceDelegate.officials_id).where(
                        ConferenceDelegate.conference_id == conference_id
                    )
                ),
            ),
        )
    )
    payment_rows = await db.execute(
        select(ConferencePayment.clergy_district_id).where(
            ConferencePayment.conference_id == conference_id,
            ConferencePayment.clergy_district_id.isnot(None),
        )
    )
    ids = {row[0] for row in official_rows.all() if row[0]}
    ids.update(row[0] for row in payment_rows.all() if row[0])
    return sorted(ids)


async def get_payment_info(
    db: AsyncSession,
    conference_id: int,
) -> Dict[str, Any]:
    """District ledgers for a conference: fee, approved total, remaining, proofs."""
    district_ids = await conference_district_ids(db, conference_id)
    if not district_ids:
        return {}

    districts = list(
        (
            await db.execute(select(ClergyDistrict).where(ClergyDistrict.id.in_(district_ids)))
        ).scalars().all()
    )
    district_by_id = {d.id: d for d in districts}

    officials = list(
        (
            await db.execute(
                select(CustomUser).where(
                    CustomUser.clergy_district_id.in_(district_ids),
                    CustomUser.user_type == UserType.DISTRICT_OFFICIAL,
                )
            )
        ).scalars().all()
    )
    officials_by_district: dict[int, list[CustomUser]] = defaultdict(list)
    for official in officials:
        if official.clergy_district_id is not None:
            officials_by_district[official.clergy_district_id].append(official)

    member_rows = (
        await db.execute(
            select(UnitMembers, CustomUser.clergy_district_id)
            .join(ConferenceDelegate, ConferenceDelegate.members_id == UnitMembers.id)
            .join(CustomUser, ConferenceDelegate.officials_id == CustomUser.id)
            .where(
                ConferenceDelegate.conference_id == conference_id,
                ConferenceDelegate.members_id.isnot(None),
                CustomUser.clergy_district_id.in_(district_ids),
            )
        )
    ).all()
    members_by_district: dict[int, list[UnitMembers]] = defaultdict(list)
    for member, district_id in member_rows:
        members_by_district[district_id].append(member)

    result_dict: dict[str, Any] = {}
    for district_id in district_ids:
        district = district_by_id.get(district_id)
        district_name = district.name if district else f"District {district_id}"
        ledger = await get_district_payment_ledger(db, conference_id, district_id)
        uploader_ids = {
            sub["uploaded_by_id"] for sub in ledger["submissions"] if sub["uploaded_by_id"]
        }
        uploader_names: dict[int, Optional[str]] = {}
        if uploader_ids:
            uploaders = list(
                (
                    await db.execute(select(CustomUser).where(CustomUser.id.in_(uploader_ids)))
                ).scalars().all()
            )
            uploader_names = {u.id: u.first_name for u in uploaders}

        payments_out = []
        for sub in ledger["submissions"]:
            payments_out.append({
                "id": sub["id"],
                "amount_to_pay": sub["total_amount"] or 0,
                "uploaded_by": uploader_names.get(sub["uploaded_by_id"]),
                "date": sub["submitted_at"],
                "status": sub["status"],
                "proof_path": sub["file_url"],
                "file_url": sub["file_url"],
                "payment_reference": sub["payment_reference"],
                "approved_paid_amount": sub["approved_paid_amount"],
                "balance_amount": sub["balance_amount"],
                "rejection_note": sub["rejection_note"],
            })

        result_dict[district_name] = {
            "district_id": district_id,
            "officials": [
                {"id": o.id, "name": o.first_name, "phone": o.phone_number}
                for o in officials_by_district[district_id]
            ],
            "members": [
                {"id": m.id, "name": m.name, "phone": m.number}
                for m in members_by_district[district_id]
            ],
            "payments": payments_out,
            "count_of_officials": ledger["official_count"],
            "count_of_members": ledger["member_count"],
            "amount_due": ledger["fee_owed"],
            "fee_owed": ledger["fee_owed"],
            "total_paid": ledger["total_paid"],
            "balance_due": ledger["balance_due"],
            "payment_credit": ledger["payment_credit"],
            "overall_status": ledger["overall_status"],
            "latest_rejection_note": ledger["latest_rejection_note"],
        }

    return result_dict
