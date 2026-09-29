"""Admin user management router - password resets and user administration."""

import base64
import asyncio
import secrets
import string
from concurrent.futures import ThreadPoolExecutor
from app.common.datetime_utils import format_timestamp_ist
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy import select, or_, func, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from pydantic import BaseModel, Field, EmailStr, field_validator

from app.common.db import get_async_db
from app.common.security import get_current_user, get_password_hash
from app.common.exporter import create_password_reset_credentials_excel
from app.common.phone_utils import normalize_optional_phone, phone_lookup_variants, validate_and_normalize_phone
from app.auth.models import CustomUser, UserType, ClergyDistrict, UnitName


router = APIRouter()

# Bcrypt is CPU-heavy; hash in parallel for bulk unit password resets.
_PASSWORD_HASH_EXECUTOR = ThreadPoolExecutor(max_workers=8, thread_name_prefix="pwd_hash")


# ============ SCHEMAS ============

class UserListItem(BaseModel):
    """Schema for user list item."""
    id: int
    username: str
    email: str
    phone_number: Optional[str] = None
    first_name: Optional[str] = None
    user_type: str
    is_active: bool
    unit_name: Optional[str] = None
    district_name: Optional[str] = None

    class Config:
        from_attributes = True


class PasswordResetRequest(BaseModel):
    """Schema for individual password reset."""
    user_id: int = Field(..., gt=0)
    new_password: str = Field(..., min_length=6, max_length=128)


class BulkPasswordResetRequest(BaseModel):
    """Schema for bulk password reset."""
    user_ids: List[int] = Field(..., min_length=1)
    new_password: str = Field(..., min_length=6, max_length=128)


class PasswordResetResponse(BaseModel):
    """Response schema for password reset."""
    message: str
    user_id: int
    username: str


class BulkPasswordResetResponse(BaseModel):
    """Response schema for bulk password reset."""
    message: str
    total_requested: int
    total_reset: int
    reset_users: List[dict]
    failed_users: List[dict]
    spreadsheet_filename: Optional[str] = None
    spreadsheet_base64: Optional[str] = None


def _generate_unique_password(length: int = 10) -> str:
    """Generate a random password for a single user account."""
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


async def _hash_passwords_parallel(plaintexts: List[str]) -> List[str]:
    """Hash many passwords concurrently to avoid bulk-reset timeouts."""
    loop = asyncio.get_running_loop()
    return list(await asyncio.gather(*(
        loop.run_in_executor(_PASSWORD_HASH_EXECUTOR, get_password_hash, plain)
        for plain in plaintexts
    )))


def _build_reset_user_entry(user: CustomUser, new_password: Optional[str] = None) -> dict:
    """Build export-friendly user details for password reset spreadsheets."""
    district_name = None
    unit_name = None

    if user.unit_name:
        unit_name = user.unit_name.name
        if user.unit_name.district:
            district_name = user.unit_name.district.name
    elif user.clergy_district:
        district_name = user.clergy_district.name

    entry = {
        "user_id": user.id,
        "username": user.username,
        "user_type": user.user_type.name,
        "unit_name": unit_name,
        "district_name": district_name,
        "phone_number": user.phone_number,
        "display_name": user.first_name,
    }
    if new_password is not None:
        entry["new_password"] = new_password
    return entry


def _strip_passwords_from_reset_users(reset_users: List[dict]) -> List[dict]:
    """Remove plaintext passwords before returning API response."""
    return [{key: value for key, value in user.items() if key != "new_password"} for user in reset_users]


async def _fetch_users_for_reset(
    db: AsyncSession,
    user_ids: Optional[List[int]] = None,
    target_type: Optional[UserType] = None,
    district_id: Optional[int] = None,
) -> List[CustomUser]:
    """Load users with unit/district relationships for password reset."""
    stmt = select(CustomUser).options(
        selectinload(CustomUser.unit_name).selectinload(UnitName.district),
        selectinload(CustomUser.clergy_district),
    )

    if user_ids is not None:
        stmt = stmt.where(CustomUser.id.in_(user_ids))
    elif target_type is not None:
        stmt = stmt.where(CustomUser.user_type == target_type)
        if district_id:
            if target_type == UserType.UNIT:
                stmt = stmt.join(UnitName, CustomUser.unit_name_id == UnitName.id).where(
                    UnitName.clergy_district_id == district_id
                )
            else:
                stmt = stmt.where(CustomUser.clergy_district_id == district_id)

    result = await db.execute(stmt)
    return list(result.scalars().unique().all())


def _attach_spreadsheet_export(
    response: BulkPasswordResetResponse,
    reset_users: List[dict],
    export_label: str,
    fallback_password: Optional[str] = None,
) -> BulkPasswordResetResponse:
    """Attach a base64-encoded Excel spreadsheet to a reset response."""
    if not reset_users:
        return response

    excel_file = create_password_reset_credentials_excel(
        reset_users,
        fallback_password or "",
        export_label,
    )
    filename = f"{export_label.lower().replace(' ', '-')}-{format_timestamp_ist()}.xlsx"
    response.spreadsheet_filename = filename
    response.spreadsheet_base64 = base64.b64encode(excel_file.getvalue()).decode("ascii")
    return response


class DistrictOfficialCreateRequest(BaseModel):
    """Schema for creating a district official with district name as username."""
    district_id: int = Field(..., gt=0, description="ID of the clergy district")
    official_name: str = Field(..., min_length=1, max_length=255, description="Name of the official")
    phone_number: str = Field(..., min_length=10, max_length=20, description="Phone number (also used as default password)")
    password: Optional[str] = Field(None, min_length=6, max_length=128, description="Custom password (optional, defaults to phone number)")

    @field_validator("phone_number")
    @classmethod
    def normalize_phone_number(cls, v: str) -> str:
        return validate_and_normalize_phone(v)


class DistrictOfficialResponse(BaseModel):
    """Response schema for district official creation."""
    message: str
    official_id: int
    username: str
    district_name: str
    default_password_hint: str


class BloodBankUserCreateRequest(BaseModel):
    """Schema for creating a dedicated blood bank user."""
    username: str = Field(..., min_length=2, max_length=150)
    email: EmailStr
    first_name: Optional[str] = Field(None, max_length=150)
    phone_number: Optional[str] = Field(None, min_length=10, max_length=20)
    password: str = Field(..., min_length=6, max_length=128)

    @field_validator("phone_number")
    @classmethod
    def normalize_phone_number(cls, v: Optional[str]) -> Optional[str]:
        return normalize_optional_phone(v)


class BloodBankUserUpdateRequest(BaseModel):
    """Schema for updating a blood bank user."""
    email: Optional[EmailStr] = None
    first_name: Optional[str] = Field(None, max_length=150)
    phone_number: Optional[str] = Field(None, min_length=10, max_length=20)
    is_active: Optional[bool] = None
    password: Optional[str] = Field(None, min_length=6, max_length=128)

    @field_validator("phone_number")
    @classmethod
    def normalize_phone_number(cls, v: Optional[str]) -> Optional[str]:
        return normalize_optional_phone(v)


class BloodBankUserResponse(BaseModel):
    """Response schema for blood bank user operations."""
    message: str
    user_id: int
    username: str


# ============ DEPENDENCIES ============

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


# ============ USER LISTING ENDPOINTS ============

class UserListResponse(BaseModel):
    """Paginated user list response."""
    data: List[UserListItem]
    total: int
    page: int
    page_size: int
    pages: int


@router.get("", response_model=UserListResponse)
async def list_users(
    user_type: Optional[str] = Query(None, description="Filter by user type: UNIT, DISTRICT_OFFICIAL, or all"),
    district_id: Optional[int] = Query(None, description="Filter by district ID"),
    search: Optional[str] = Query(None, description="Search by username, email, or phone"),
    is_active: Optional[bool] = Query(None, description="Filter by active status"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(50, ge=1, le=200, description="Items per page"),
    current_user: CustomUser = Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    List users with optional filters and pagination.

    User types:
    - UNIT (2): Unit officials login users
    - DISTRICT_OFFICIAL (3): Conference/Kalamela officials
    - BLOOD_BANK (4): Dedicated blood bank search users
    """
    non_admin_types = [UserType.UNIT, UserType.DISTRICT_OFFICIAL, UserType.BLOOD_BANK]

    def _apply_filters(s, *, join_unit_name: bool = False):
        if user_type:
            type_upper = user_type.upper()
            if type_upper == "UNIT":
                s = s.where(CustomUser.user_type == UserType.UNIT)
            elif type_upper == "DISTRICT_OFFICIAL":
                s = s.where(CustomUser.user_type == UserType.DISTRICT_OFFICIAL)
            elif type_upper == "BLOOD_BANK":
                s = s.where(CustomUser.user_type == UserType.BLOOD_BANK)
            else:
                s = s.where(CustomUser.user_type.in_(non_admin_types))
        else:
            s = s.where(CustomUser.user_type.in_(non_admin_types))

        if district_id:
            if not join_unit_name:
                s = s.outerjoin(UnitName, CustomUser.unit_name_id == UnitName.id)
            s = s.where(
                or_(
                    CustomUser.clergy_district_id == district_id,
                    UnitName.clergy_district_id == district_id,
                )
            )
        if is_active is not None:
            s = s.where(CustomUser.is_active == is_active)
        if search:
            pattern = f"%{search}%"
            s = s.where(
                or_(
                    CustomUser.username.ilike(pattern),
                    CustomUser.email.ilike(pattern),
                    CustomUser.phone_number.ilike(pattern),
                )
            )
        return s

    count_stmt = _apply_filters(select(func.count()).select_from(CustomUser))
    total = (await db.execute(count_stmt)).scalar() or 0

    offset = (page - 1) * page_size
    stmt = _apply_filters(
        select(CustomUser).options(
            selectinload(CustomUser.unit_name).selectinload(UnitName.district),
            selectinload(CustomUser.clergy_district),
        )
    ).order_by(CustomUser.username).offset(offset).limit(page_size)

    result = await db.execute(stmt)
    users = list(result.scalars().unique().all())

    return UserListResponse(
        data=[
            UserListItem(
                id=user.id,
                username=user.username,
                email=user.email,
                phone_number=user.phone_number,
                first_name=user.first_name,
                user_type=user.user_type.name,
                is_active=user.is_active,
                unit_name=user.unit_name.name if user.unit_name else None,
                district_name=(
                    user.clergy_district.name
                    if user.clergy_district
                    else (
                        user.unit_name.district.name
                        if user.unit_name and user.unit_name.district
                        else None
                    )
                ),
            )
            for user in users
        ],
        total=total,
        page=page,
        page_size=page_size,
        pages=(total + page_size - 1) // page_size,
    )


@router.get("/summary", response_model=dict)
async def get_users_summary(
    current_user: CustomUser = Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Get summary counts of users by type."""
    # Count users by type
    stmt = select(
        CustomUser.user_type,
        func.count(CustomUser.id).label('count')
    ).where(
        CustomUser.user_type.in_([UserType.UNIT, UserType.DISTRICT_OFFICIAL, UserType.BLOOD_BANK])
    ).group_by(CustomUser.user_type)
    
    result = await db.execute(stmt)
    counts = {row[0].name: row[1] for row in result.all()}
    
    return {
        "unit_officials": counts.get("UNIT", 0),
        "district_officials": counts.get("DISTRICT_OFFICIAL", 0),
        "blood_bank_users": counts.get("BLOOD_BANK", 0),
        "total": sum(counts.values()),
    }


# ============ PASSWORD RESET ENDPOINTS ============

@router.post("/reset-password", response_model=PasswordResetResponse)
async def reset_user_password(
    data: PasswordResetRequest,
    current_user: CustomUser = Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Reset password for a single user.
    
    Only allows resetting passwords for UNIT and DISTRICT_OFFICIAL users.
    """
    # Fetch the user
    stmt = select(CustomUser).where(CustomUser.id == data.user_id)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    
    # Prevent resetting admin passwords through this endpoint
    if user.user_type == UserType.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot reset admin passwords through this endpoint"
        )
    
    # Hash and update the password
    user.hashed_password = get_password_hash(data.new_password)
    await db.commit()
    
    return PasswordResetResponse(
        message="Password reset successfully",
        user_id=user.id,
        username=user.username,
    )


@router.post("/bulk-reset-password", response_model=BulkPasswordResetResponse)
async def bulk_reset_passwords(
    data: BulkPasswordResetRequest,
    current_user: CustomUser = Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Reset passwords for multiple users at once.
    
    All selected users will be assigned the same new password.
    Only allows resetting passwords for UNIT and DISTRICT_OFFICIAL users.
    """
    # Fetch all requested users
    users = await _fetch_users_for_reset(db, user_ids=data.user_ids)
    
    # Track results
    reset_users = []
    failed_users = []
    hashed_password = get_password_hash(data.new_password)
    
    # Find users that weren't found
    found_ids = {user.id for user in users}
    not_found_ids = set(data.user_ids) - found_ids
    
    for user_id in not_found_ids:
        failed_users.append({
            "user_id": user_id,
            "reason": "User not found"
        })
    
    # Process found users
    for user in users:
        # Skip admin users
        if user.user_type == UserType.ADMIN:
            failed_users.append({
                "user_id": user.id,
                "username": user.username,
                "reason": "Cannot reset admin passwords"
            })
            continue
        
        # Reset password
        user.hashed_password = hashed_password
        reset_users.append(_build_reset_user_entry(user))
    
    await db.commit()
    
    return BulkPasswordResetResponse(
        message=f"Password reset completed for {len(reset_users)} users",
        total_requested=len(data.user_ids),
        total_reset=len(reset_users),
        reset_users=reset_users,
        failed_users=failed_users,
    )


@router.post("/reset-all-by-type", response_model=BulkPasswordResetResponse)
async def reset_all_passwords_by_type(
    user_type: str = Query(..., description="User type: UNIT or DISTRICT_OFFICIAL"),
    new_password: Optional[str] = Query(None, min_length=6, max_length=128),
    district_id: Optional[int] = Query(None, description="Optional: limit to specific district"),
    export_spreadsheet: bool = Query(False, description="Include Excel spreadsheet in response"),
    unique_passwords: bool = Query(False, description="Generate a unique password per user (UNIT reset-all)"),
    current_user: CustomUser = Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Reset passwords for all users of a specific type.
    
    Use with caution - this affects all users of the specified type.
    
    User types:
    - UNIT: All unit officials
    - DISTRICT_OFFICIAL: All conference/kalamela officials
    """
    # Validate user type
    if user_type.upper() == "UNIT":
        target_type = UserType.UNIT
    elif user_type.upper() == "DISTRICT_OFFICIAL":
        target_type = UserType.DISTRICT_OFFICIAL
    elif user_type.upper() == "BLOOD_BANK":
        target_type = UserType.BLOOD_BANK
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid user_type. Must be UNIT, DISTRICT_OFFICIAL, or BLOOD_BANK"
        )
    
    # Build query
    users = await _fetch_users_for_reset(
        db,
        target_type=target_type,
        district_id=district_id,
    )
    
    if not users:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No users found for type {user_type}"
        )

    use_unique_passwords = unique_passwords and target_type == UserType.UNIT
    if not use_unique_passwords and not new_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="new_password is required unless unique_passwords is enabled for UNIT users",
        )

    reset_users = []

    if use_unique_passwords:
        plaintexts = [_generate_unique_password() for _ in users]
        hashed_passwords = await _hash_passwords_parallel(plaintexts)
        for user, plain_password, hashed_password in zip(users, plaintexts, hashed_passwords):
            user.hashed_password = hashed_password
            reset_users.append(_build_reset_user_entry(user, plain_password))
    else:
        hashed_password = get_password_hash(new_password)
        for user in users:
            user.hashed_password = hashed_password
            reset_users.append(_build_reset_user_entry(user, new_password))
    
    await db.commit()

    export_labels = {
        UserType.UNIT: "Unit Password Reset",
        UserType.DISTRICT_OFFICIAL: "District Password Reset",
        UserType.BLOOD_BANK: "Blood Bank Password Reset",
    }
    export_label = export_labels.get(target_type, "Password Reset")

    export_rows = reset_users
    response_reset_users = _strip_passwords_from_reset_users(reset_users)
    
    response = BulkPasswordResetResponse(
        message=f"Password reset completed for all {user_type} users",
        total_requested=len(users),
        total_reset=len(users),
        reset_users=response_reset_users,
        failed_users=[],
    )

    if export_spreadsheet:
        response = _attach_spreadsheet_export(
            response,
            export_rows,
            export_label,
            fallback_password=new_password,
        )

    return response


# ============ DISTRICT OFFICIAL MANAGEMENT ENDPOINTS ============

@router.post("/district-officials", response_model=DistrictOfficialResponse)
async def create_district_official(
    data: DistrictOfficialCreateRequest,
    current_user: CustomUser = Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Create a district official with DISTRICT NAME as username.
    
    This enables district-wise login for Kalamela and Conference modules.
    Each district can have only ONE district official account.
    
    Login credentials:
    - Username: District name (e.g., 'THIRUVALLA', 'ADOOR')
    - Password: Phone number (or custom password if provided)
    """
    # Get district
    stmt = select(ClergyDistrict).where(ClergyDistrict.id == data.district_id)
    result = await db.execute(stmt)
    district = result.scalar_one_or_none()
    
    if not district:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="District not found"
        )
    
    # Check if district already has an official
    stmt = select(CustomUser).where(
        and_(
            CustomUser.clergy_district_id == data.district_id,
            CustomUser.user_type == UserType.DISTRICT_OFFICIAL
        )
    )
    result = await db.execute(stmt)
    existing_official = result.scalar_one_or_none()
    
    if existing_official:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"District '{district.name}' already has an official. "
                   f"Use password reset to update credentials."
        )
    
    # Check if username (district name) is already taken
    stmt = select(CustomUser).where(CustomUser.username == district.name)
    result = await db.execute(stmt)
    existing_username = result.scalar_one_or_none()
    
    if existing_username:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Username '{district.name}' is already in use"
        )
    
    # Check if phone number is already taken
    phone_variants = phone_lookup_variants(data.phone_number)
    stmt = select(CustomUser).where(CustomUser.phone_number.in_(phone_variants))
    result = await db.execute(stmt)
    existing_phone = result.scalar_one_or_none()
    
    if existing_phone:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Phone number is already in use"
        )
    
    # Use custom password or phone number as default
    password = data.password if data.password else data.phone_number
    
    # Create district official with DISTRICT NAME as username
    official = CustomUser(
        username=district.name,  # District name as username
        email=f"{district.name.lower().replace(' ', '_')}@district.local",
        first_name=data.official_name,
        phone_number=data.phone_number,
        clergy_district_id=data.district_id,
        user_type=UserType.DISTRICT_OFFICIAL,
        hashed_password=get_password_hash(password),
        is_active=True,
    )
    
    db.add(official)
    await db.commit()
    await db.refresh(official)
    
    return DistrictOfficialResponse(
        message="District official created successfully",
        official_id=official.id,
        username=official.username,
        district_name=district.name,
        default_password_hint="Phone number" if not data.password else "Custom password provided",
    )


@router.get("/district-officials", response_model=List[dict])
async def list_district_officials(
    current_user: CustomUser = Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    List all district officials.
    
    Shows username (district name), official name, phone, and district info.
    """
    stmt = select(CustomUser).where(
        CustomUser.user_type == UserType.DISTRICT_OFFICIAL
    ).options(selectinload(CustomUser.clergy_district))
    
    result = await db.execute(stmt)
    officials = list(result.scalars().all())
    
    return [
        {
            "id": official.id,
            "username": official.username,  # District name
            "official_name": official.first_name,
            "phone_number": official.phone_number,
            "district_id": official.clergy_district_id,
            "district_name": official.clergy_district.name if official.clergy_district else None,
            "is_active": official.is_active,
            "conference_id": official.conference_id,
        }
        for official in officials
    ]


@router.get("/districts", response_model=List[dict])
async def list_districts_for_officials(
    current_user: CustomUser = Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    List all districts with their official status.
    
    Shows which districts have officials and which don't.
    """
    # Get all districts
    stmt = select(ClergyDistrict)
    result = await db.execute(stmt)
    districts = list(result.scalars().all())
    
    # Map district_id -> official (one per district)
    stmt = select(CustomUser).where(
        CustomUser.user_type == UserType.DISTRICT_OFFICIAL
    )
    result = await db.execute(stmt)
    officials_by_district = {
        official.clergy_district_id: official
        for official in result.scalars().all()
        if official.clergy_district_id is not None
    }
    
    return [
        {
            "id": district.id,
            "name": district.name,
            "has_official": district.id in officials_by_district,
            "login_username": (
                officials_by_district[district.id].username
                if district.id in officials_by_district
                else None
            ),
            "official_id": (
                officials_by_district[district.id].id
                if district.id in officials_by_district
                else None
            ),
            "official_name": (
                officials_by_district[district.id].first_name
                if district.id in officials_by_district
                else None
            ),
            "official_phone": (
                officials_by_district[district.id].phone_number
                if district.id in officials_by_district
                else None
            ),
            "official_username": (
                officials_by_district[district.id].username
                if district.id in officials_by_district
                else None
            ),
        }
        for district in districts
    ]


# ============ BLOOD BANK USER MANAGEMENT ENDPOINTS ============

@router.post("/blood-bank-users", response_model=BloodBankUserResponse)
async def create_blood_bank_user(
    data: BloodBankUserCreateRequest,
    current_user: CustomUser = Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Create a dedicated blood bank user with access to Blood Donor Search only."""
    stmt = select(CustomUser).where(CustomUser.username == data.username)
    result = await db.execute(stmt)
    if result.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Username already in use")

    stmt = select(CustomUser).where(CustomUser.email == data.email)
    result = await db.execute(stmt)
    if result.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already in use")

    if data.phone_number:
        phone_variants = phone_lookup_variants(data.phone_number)
        stmt = select(CustomUser).where(CustomUser.phone_number.in_(phone_variants))
        result = await db.execute(stmt)
        if result.scalar_one_or_none():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Phone number already in use")

    user = CustomUser(
        username=data.username,
        email=data.email,
        first_name=data.first_name,
        phone_number=data.phone_number,
        user_type=UserType.BLOOD_BANK,
        hashed_password=get_password_hash(data.password),
        is_active=True,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    return BloodBankUserResponse(
        message="Blood bank user created successfully",
        user_id=user.id,
        username=user.username,
    )


@router.put("/blood-bank-users/{user_id}", response_model=BloodBankUserResponse)
async def update_blood_bank_user(
    user_id: int,
    data: BloodBankUserUpdateRequest,
    current_user: CustomUser = Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Update a blood bank user (profile fields, active status, and optional password)."""
    stmt = select(CustomUser).where(
        and_(CustomUser.id == user_id, CustomUser.user_type == UserType.BLOOD_BANK)
    )
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Blood bank user not found")

    if data.email is not None and data.email != user.email:
        stmt = select(CustomUser).where(
            and_(CustomUser.email == data.email, CustomUser.id != user_id)
        )
        result = await db.execute(stmt)
        if result.scalar_one_or_none():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already in use")
        user.email = data.email

    if data.first_name is not None:
        user.first_name = data.first_name

    if data.phone_number is not None:
        user_variants = set(phone_lookup_variants(user.phone_number or ""))
        new_variants = set(phone_lookup_variants(data.phone_number))
        if user_variants.isdisjoint(new_variants):
            stmt = select(CustomUser).where(
                CustomUser.phone_number.in_(list(new_variants)),
                CustomUser.id != user_id,
            )
            result = await db.execute(stmt)
            if result.scalar_one_or_none():
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Phone number already in use")
        user.phone_number = data.phone_number

    if data.is_active is not None:
        user.is_active = data.is_active

    if data.password:
        user.hashed_password = get_password_hash(data.password)

    await db.commit()
    await db.refresh(user)

    return BloodBankUserResponse(
        message="Blood bank user updated successfully",
        user_id=user.id,
        username=user.username,
    )

