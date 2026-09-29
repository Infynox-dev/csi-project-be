"""Conference service layer - business logic for conference operations."""

from typing import List, Optional, Dict, Any
from collections import defaultdict
from sqlalchemy import select, and_, or_, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from fastapi import HTTPException, status

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
    ConferencePaymentCreate,
    ConferenceSettingsUpdate,
)
from app.common.security import get_password_hash


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
    Delete a conference.
    
    Args:
        db: Database session
        conference_id: ID of the conference
    
    Returns:
        True if deleted
    
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
    
    await db.delete(official)
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
    
    # Update payment status to PENDING if it was PAID
    stmt = select(ConferencePayment).where(
        and_(
            ConferencePayment.conference_id == conference_id,
            ConferencePayment.uploaded_by_id == official_user_id
        )
    ).order_by(ConferencePayment.date.desc())
    result = await db.execute(stmt)
    payment = result.scalar_one_or_none()
    
    if payment and payment.status == "PAID":
        payment.status = "PENDING"
    
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
    
    await db.delete(delegate)
    await db.commit()
    
    return True


# Payment Management
async def create_conference_payment(
    db: AsyncSession,
    conference_id: int,
    user_id: int,
    data: ConferencePaymentCreate,
) -> ConferencePayment:
    """Create a conference payment record."""
    stmt = select(CustomUser).where(CustomUser.id == user_id)
    result = await db.execute(stmt)
    user = result.scalar_one()

    # Members this official added
    stmt = select(func.count()).select_from(ConferenceDelegate).where(
        and_(
            ConferenceDelegate.conference_id == conference_id,
            ConferenceDelegate.officials_id == user_id,
            ConferenceDelegate.members_id.isnot(None),
        )
    )
    result = await db.execute(stmt)
    member_count = result.scalar() or 0

    # District officials (same set shown as registered officials)
    stmt = select(func.count()).select_from(CustomUser).where(
        and_(
            CustomUser.clergy_district_id == user.clergy_district_id,
            CustomUser.user_type == UserType.DISTRICT_OFFICIAL,
        )
    )
    result = await db.execute(stmt)
    official_count = result.scalar() or 0

    fee = await get_delegate_fee(db)
    amount = (member_count + official_count) * fee
    
    payment = ConferencePayment(
        conference_id=conference_id,
        amount_to_pay=amount,
        uploaded_by_id=user_id,
        proof_path=data.proof_path,
        payment_reference=data.payment_reference,
        status=PaymentStatusEnum.PROOF_UPLOADED if data.proof_path else PaymentStatusEnum.PENDING,
    )
    
    db.add(payment)
    await db.commit()
    await db.refresh(payment)
    
    return payment


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


async def get_payment_info(
    db: AsyncSession,
    conference_id: int,
) -> Dict[str, Any]:
    """Get aggregated payment information by district."""
    # Similar to get_all_conference_info but focused on payments
    # Implementation would follow the same pattern as get_all_conference_info
    # but include payment details
    
    # Get all delegates
    stmt = select(ConferenceDelegate).where(
        ConferenceDelegate.conference_id == conference_id
    )
    result = await db.execute(stmt)
    delegates = list(result.scalars().all())
    
    district_info = defaultdict(lambda: {
        'officials': [],
        'members': [],
        'payments': [],
        'count_of_officials': 0,
        'count_of_members': 0,
    })
    
    seen_officials = set()
    
    for delegate in delegates:
        # Get official with district
        stmt = select(CustomUser).where(CustomUser.id == delegate.officials_id).options(
            selectinload(CustomUser.clergy_district)
        )
        result = await db.execute(stmt)
        official = result.scalar_one()
        
        district_name = official.clergy_district.name if official.clergy_district else 'Unknown District'
        
        # Add unique official
        if official.id not in seen_officials:
            district_info[district_name]['officials'].append({
                'name': official.first_name,
                'phone': official.phone_number,
                'id': official.id,
            })
            district_info[district_name]['count_of_officials'] += 1
            seen_officials.add(official.id)
            
            # Get payments for this official
            stmt = select(ConferencePayment).where(
                and_(
                    ConferencePayment.conference_id == conference_id,
                    ConferencePayment.uploaded_by_id == official.id
                )
            ).distinct()
            result = await db.execute(stmt)
            payments = list(result.scalars().all())
            
            for payment in payments:
                district_info[district_name]['payments'].append({
                    'amount_to_pay': float(payment.amount_to_pay) if payment.amount_to_pay else 0,
                    'uploaded_by': official.first_name,
                    'date': payment.date,
                    'status': payment.status,
                    'proof_path': payment.proof_path,
                    'payment_reference': payment.payment_reference,
                })
        
        # Add member if present
        if delegate.members_id:
            stmt = select(UnitMembers).where(UnitMembers.id == delegate.members_id)
            result = await db.execute(stmt)
            member = result.scalar_one()
            
            district_info[district_name]['members'].append({
                'name': member.name,
                'phone': member.number,
                'id': member.id,
            })
            district_info[district_name]['count_of_members'] += 1
    
    fee = await get_delegate_fee(db)
    result_dict = {}
    for district, info in district_info.items():
        info['amount_due'] = (
            info['count_of_officials'] + info['count_of_members']
        ) * fee
        result_dict[district] = info

    return result_dict
