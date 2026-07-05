"""Pydantic schemas for units module."""

from datetime import date, datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.auth.models import ResidenceLocation
from app.common.phone_utils import normalize_optional_phone, validate_and_normalize_phone
from app.units.gender_utils import validate_member_gender


class RequestStatus(str, Enum):
    """Status enum for various request types."""
    
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class FileExtension(str, Enum):
    """Allowed file extensions for proof documents."""
    
    PDF = "pdf"
    PNG = "png"
    JPG = "jpg"
    JPEG = "jpeg"


# Archived Unit Member Schemas
class ArchivedUnitMemberBase(BaseModel):
    """Base schema for archived unit members."""
    
    name: str = Field(..., min_length=1, max_length=255)
    gender: Optional[str] = Field(None, max_length=10)
    dob: date
    number: str = Field(..., min_length=1, max_length=30)
    qualification: Optional[str] = Field(None, max_length=255)
    blood_group: Optional[str] = Field(None, max_length=10)

    @field_validator("gender", mode="before")
    @classmethod
    def normalize_gender(cls, v: Optional[str]) -> Optional[str]:
        return validate_member_gender(v)


class ArchivedUnitMemberResponse(ArchivedUnitMemberBase):
    """Response schema for archived unit members."""
    
    model_config = ConfigDict(from_attributes=True)
    
    id: int
    registered_user_id: int
    archived_at: datetime
    archive_year: Optional[str] = None
    archive_reason: Optional[str] = None


class ArchivedMembersSummary(BaseModel):
    """Gender and total counts for archived members."""

    total: int = 0
    male: int = 0
    female: int = 0


class ArchivedMemberConcernStatus(BaseModel):
    """Latest concern status for an archived member."""

    status: RequestStatus
    admin_response: Optional[str] = None


class RecentArchivedMembersResponse(BaseModel):
    """Recent archived members batch for a unit with summary stats."""

    archive_year: Optional[str] = None
    archive_reason: Optional[str] = None
    summary: ArchivedMembersSummary
    members: List[ArchivedUnitMemberResponse]
    pending_concern_member_ids: List[int] = []
    member_concerns: dict[str, ArchivedMemberConcernStatus] = {}


# Removed Unit Member Schemas
class RemovedUnitMemberBase(BaseModel):
    """Base schema for removed unit members."""
    
    name: str = Field(..., min_length=1, max_length=255)
    gender: Optional[str] = Field(None, max_length=10)
    dob: date
    number: str = Field(..., min_length=1, max_length=30)
    qualification: Optional[str] = Field(None, max_length=255)
    blood_group: Optional[str] = Field(None, max_length=10)


class RemovedUnitMemberResponse(RemovedUnitMemberBase):
    """Response schema for removed unit members."""
    
    model_config = ConfigDict(from_attributes=True)
    
    id: int
    registered_user_id: int
    archived_at: datetime
    delete_reason: Optional[str] = None
    deleted_by_id: Optional[int] = None
    original_member_id: Optional[int] = None
    notified_at: Optional[datetime] = None
    removal_type: Optional[str] = None
    removed_at: Optional[datetime] = None


class MemberRemoveRequest(BaseModel):
    """Admin request to remove an active unit member (not seasonal archival)."""

    reason: str = Field(..., min_length=5, max_length=5000)
    confirm_not_archival: bool = False


class BulkMemberRemoveRequest(BaseModel):
    """Admin request to remove multiple active unit members (not seasonal archival)."""

    member_ids: List[int] = Field(..., min_length=1)
    reason: str = Field(..., min_length=5, max_length=5000)
    confirm_not_archival: bool = False


class PendingRemovedMembersResponse(BaseModel):
    """Removed members awaiting unit acknowledgement."""

    summary: ArchivedMembersSummary
    members: List[RemovedUnitMemberResponse]


class AcknowledgeRemovedMembersRequest(BaseModel):
    """Mark removed-member notifications as seen by the unit."""

    removed_member_ids: Optional[List[int]] = None


# Unit Transfer Request Schemas
class UnitTransferRequestBase(BaseModel):
    """Base schema for unit transfer requests."""
    
    unit_member_id: int = Field(..., gt=0)
    destination_unit_id: int = Field(..., gt=0)
    reason: str = Field(..., min_length=5, max_length=5000)


class UnitTransferRequestCreate(UnitTransferRequestBase):
    """Create schema for unit transfer requests."""
    
    proof: str = Field(..., description="File path to proof document")
    
    @field_validator("proof")
    @classmethod
    def validate_proof_extension(cls, v: str) -> str:
        """Validate that proof has allowed extension."""
        if not v:
            raise ValueError("Proof document is required")
        if not any(v.lower().endswith(f".{ext.value}") for ext in FileExtension):
            raise ValueError(f"File must have one of these extensions: {', '.join(e.value for e in FileExtension)}")
        return v


class UnitTransferRequestUpdate(BaseModel):
    """Update schema for unit transfer requests."""
    
    status: RequestStatus


class TransferDestinationUnitResponse(BaseModel):
    """Registered unit available as a transfer destination."""

    id: int
    name: str
    clergy_district: str
    unit_number: str


class UnitTransferRequestResponse(BaseModel):
    """Response schema for unit transfer requests."""
    
    model_config = ConfigDict(from_attributes=True)
    
    id: int
    unit_member_id: int
    destination_unit_id: int
    reason: str  # No min_length for response - existing data may have shorter values
    current_unit_id: Optional[int]
    original_registered_user_id: Optional[int]
    proof: str
    status: RequestStatus
    created_at: datetime
    updated_at: datetime
    # Additional fields for frontend
    member_name: Optional[str] = None
    current_unit_name: Optional[str] = None
    destination_unit_name: Optional[str] = None


# Unit Member Change Request Schemas
class UnitMemberChangeRequestBase(BaseModel):
    """Base schema for unit member change requests."""
    
    unit_member_id: int = Field(..., gt=0)
    reason: str = Field(..., min_length=5, max_length=5000)


class UnitMemberChangeRequestCreate(UnitMemberChangeRequestBase):
    """Create schema for unit member change requests."""
    
    name: Optional[str] = Field(None, max_length=255)
    gender: Optional[str] = Field(None, max_length=10)
    dob: Optional[date] = None
    blood_group: Optional[str] = Field(None, max_length=10)
    qualification: Optional[str] = Field(None, max_length=255)
    number: Optional[str] = Field(None, max_length=30)
    residence_location: Optional[ResidenceLocation] = None
    residence_state_id: Optional[int] = None
    residence_city_id: Optional[int] = None
    proof: str = Field(..., description="File path to proof document")

    @field_validator("gender", mode="before")
    @classmethod
    def normalize_gender(cls, v: Optional[str]) -> Optional[str]:
        return validate_member_gender(v)

    @field_validator("number", mode="before")
    @classmethod
    def normalize_number(cls, v: Optional[str]) -> Optional[str]:
        if v is None or not str(v).strip():
            return None
        return validate_and_normalize_phone(str(v))
    
    @field_validator("proof")
    @classmethod
    def validate_proof_extension(cls, v: str) -> str:
        """Validate that proof has allowed extension."""
        if not any(v.lower().endswith(f".{ext.value}") for ext in FileExtension):
            raise ValueError(f"File must have one of these extensions: {', '.join(e.value for e in FileExtension)}")
        return v

    @model_validator(mode='after')
    def validate_residence(self):
        if self.residence_location is not None:
            _validate_residence_fields(
                self.residence_location,
                self.residence_state_id,
                self.residence_city_id,
            )
        return self


class UnitMemberChangeRequestResponse(BaseModel):
    """Response schema for unit member change requests."""
    
    model_config = ConfigDict(from_attributes=True)
    
    id: int
    unit_member_id: int
    reason: str  # No min_length for response - existing data may have shorter values
    name: Optional[str]
    gender: Optional[str]
    dob: Optional[date]
    blood_group: Optional[str]
    qualification: Optional[str]
    number: Optional[str]
    residence_location: Optional[ResidenceLocation] = None
    residence_state_id: Optional[int] = None
    residence_city_id: Optional[int] = None
    original_name: Optional[str]
    original_gender: Optional[str]
    original_dob: Optional[date]
    original_blood_group: Optional[str]
    original_qualification: Optional[str]
    original_number: Optional[str]
    original_residence_location: Optional[ResidenceLocation] = None
    original_residence_state_id: Optional[int] = None
    original_residence_city_id: Optional[int] = None
    proof: str
    status: RequestStatus
    created_at: datetime
    updated_at: datetime
    unit_name: Optional[str] = None


# Unit Officials Change Request Schemas
OFFICIAL_PHONE_FIELDS = (
    "president_phone",
    "vice_president_phone",
    "secretary_phone",
    "joint_secretary_phone",
    "treasurer_phone",
)


def _normalize_official_phone(value: Optional[str]) -> Optional[str]:
    if value is None or not str(value).strip():
        return value
    return validate_and_normalize_phone(str(value))


class UnitOfficialsChangeRequestBase(BaseModel):
    """Base schema for unit officials change requests."""
    
    unit_official_id: int = Field(..., gt=0)
    reason: str = Field(..., min_length=5, max_length=5000)


class UnitOfficialsChangeRequestCreate(UnitOfficialsChangeRequestBase):
    """Create schema for unit officials change requests."""
    
    president_designation: Optional[str] = Field(None, max_length=50)
    president_name: Optional[str] = Field(None, max_length=255)
    president_phone: Optional[str] = Field(None, max_length=30)
    vice_president_name: Optional[str] = Field(None, max_length=255)
    vice_president_phone: Optional[str] = Field(None, max_length=30)
    secretary_name: Optional[str] = Field(None, max_length=255)
    secretary_phone: Optional[str] = Field(None, max_length=30)
    joint_secretary_name: Optional[str] = Field(None, max_length=255)
    joint_secretary_phone: Optional[str] = Field(None, max_length=30)
    treasurer_name: Optional[str] = Field(None, max_length=255)
    treasurer_phone: Optional[str] = Field(None, max_length=30)
    proof: str = Field(..., description="File path to proof document")
    
    @field_validator("proof")
    @classmethod
    def validate_proof_extension(cls, v: str) -> str:
        """Validate that proof has allowed extension."""
        if not any(v.lower().endswith(f".{ext.value}") for ext in FileExtension):
            raise ValueError(f"File must have one of these extensions: {', '.join(e.value for e in FileExtension)}")
        return v

    @field_validator(*OFFICIAL_PHONE_FIELDS)
    @classmethod
    def normalize_official_phones(cls, v: Optional[str]) -> Optional[str]:
        return _normalize_official_phone(v)


class UnitOfficialsChangeRequestResponse(BaseModel):
    """Response schema for unit officials change requests."""
    
    model_config = ConfigDict(from_attributes=True)
    
    id: int
    unit_official_id: int
    reason: str  # No min_length for response - existing data may have shorter values
    president_designation: Optional[str]
    president_name: Optional[str]
    president_phone: Optional[str]
    original_president_designation: Optional[str]
    original_president_name: Optional[str]
    original_president_phone: Optional[str]
    vice_president_name: Optional[str]
    vice_president_phone: Optional[str]
    original_vice_president_name: Optional[str]
    original_vice_president_phone: Optional[str]
    secretary_name: Optional[str]
    secretary_phone: Optional[str]
    original_secretary_name: Optional[str]
    original_secretary_phone: Optional[str]
    joint_secretary_name: Optional[str]
    joint_secretary_phone: Optional[str]
    original_joint_secretary_name: Optional[str]
    original_joint_secretary_phone: Optional[str]
    treasurer_name: Optional[str]
    treasurer_phone: Optional[str]
    original_treasurer_name: Optional[str]
    original_treasurer_phone: Optional[str]
    proof: str
    status: RequestStatus
    created_at: datetime
    updated_at: datetime
    unit_name: Optional[str] = None


# Unit Councilor Change Request Schemas
class UnitCouncilorChangeRequestBase(BaseModel):
    """Base schema for unit councilor change requests."""
    
    unit_councilor_id: int = Field(..., gt=0)
    reason: str = Field(..., min_length=5, max_length=5000)


class UnitCouncilorChangeRequestCreate(UnitCouncilorChangeRequestBase):
    """Create schema for unit councilor change requests."""
    
    unit_member_id: Optional[int] = Field(None, gt=0)
    proof: str = Field(..., description="File path to proof document")
    
    @field_validator("proof")
    @classmethod
    def validate_proof_extension(cls, v: str) -> str:
        """Validate that proof has allowed extension."""
        if not any(v.lower().endswith(f".{ext.value}") for ext in FileExtension):
            raise ValueError(f"File must have one of these extensions: {', '.join(e.value for e in FileExtension)}")
        return v


class UnitCouncilorChangeRequestResponse(BaseModel):
    """Response schema for unit councilor change requests."""
    
    model_config = ConfigDict(from_attributes=True)
    
    id: int
    unit_councilor_id: Optional[int] = None
    reason: str  # No min_length for response - existing data may have shorter values
    unit_member_id: Optional[int]
    original_unit_member_id: Optional[int]
    original_member_name: Optional[str] = None
    new_member_name: Optional[str] = None
    proof: str
    status: RequestStatus
    created_at: datetime
    updated_at: datetime
    unit_id: Optional[int] = None
    unit_name: Optional[str] = None
    original_member_name: Optional[str] = None
    new_member_name: Optional[str] = None


# Unit Member Add Request Schemas
VALID_BLOOD_GROUPS = {"A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"}


# Archived Member Concern Request Schemas
class ArchivedMemberConcernRequestCreate(BaseModel):
    """Create schema for archived member concern requests."""

    archived_unit_member_id: int = Field(..., gt=0)
    concern_text: str = Field(..., min_length=20, max_length=5000)


class ArchivedMemberConcernRequestResponse(BaseModel):
    """Response schema for archived member concern requests."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    archived_unit_member_id: int
    registered_user_id: int
    concern_text: str
    admin_response: Optional[str] = None
    status: RequestStatus
    created_at: datetime
    updated_at: datetime
    # Enriched fields for admin/unit views
    archived_member_name: Optional[str] = None
    archived_member_gender: Optional[str] = None
    archived_member_dob: Optional[date] = None
    unit_name: Optional[str] = None
    archive_year: Optional[str] = None


class UnitMemberAddRequestBase(BaseModel):
    """Base schema for unit member add requests."""
    
    name: str = Field(..., min_length=1, max_length=255)
    gender: str = Field(..., max_length=10)
    dob: date
    number: str = Field(..., min_length=1, max_length=30)
    qualification: Optional[str] = Field(None, max_length=255)
    blood_group: str = Field(..., min_length=1, max_length=10)
    reason: str = Field(..., min_length=5, max_length=5000)
    residence_location: ResidenceLocation
    residence_state_id: Optional[int] = None
    residence_city_id: Optional[int] = None

    @field_validator("blood_group")
    @classmethod
    def validate_blood_group(cls, v: str) -> str:
        if v not in VALID_BLOOD_GROUPS:
            raise ValueError(f"Invalid blood group. Must be one of: {', '.join(sorted(VALID_BLOOD_GROUPS))}")
        return v

    @field_validator("gender", mode="before")
    @classmethod
    def normalize_gender(cls, v: str) -> str:
        normalized = validate_member_gender(v)
        if normalized is None:
            raise ValueError("Gender is required")
        return normalized

    @model_validator(mode='after')
    def validate_residence(self):
        _validate_residence_fields(
            self.residence_location,
            self.residence_state_id,
            self.residence_city_id,
        )
        self.number = validate_and_normalize_phone(self.number)
        return self


class UnitMemberAddRequestCreate(UnitMemberAddRequestBase):
    """Create schema for unit member add requests."""
    
    proof: Optional[str] = Field(None, description="File path to proof document")
    
    @field_validator("proof")
    @classmethod
    def validate_proof_extension(cls, v: Optional[str]) -> Optional[str]:
        """Validate that proof has allowed extension if provided."""
        if v and not any(v.lower().endswith(f".{ext.value}") for ext in FileExtension):
            raise ValueError(f"File must have one of these extensions: {', '.join(e.value for e in FileExtension)}")
        return v


class UnitMemberAddRequestResponse(BaseModel):
    """Response schema for unit member add requests."""
    
    model_config = ConfigDict(from_attributes=True)
    
    id: int
    registered_user_id: int
    name: str
    gender: str
    dob: date
    number: str
    qualification: Optional[str]
    blood_group: Optional[str]
    reason: str  # No min_length for response - existing data may have shorter values
    proof: Optional[str]
    status: RequestStatus
    created_at: datetime
    updated_at: datetime
    unit_name: Optional[str] = None
    username: Optional[str] = None
    residence_location: Optional[ResidenceLocation] = None
    residence_state_id: Optional[int] = None
    residence_city_id: Optional[int] = None
    residence_state_name: Optional[str] = None
    residence_city_name: Optional[str] = None
    residence_country_name: Optional[str] = None


# Unit Details Schemas
class UnitDetailsCreate(BaseModel):
    """Create schema for unit details."""
    
    president_designation: str = Field(..., min_length=1, max_length=50)
    president_name: str = Field(..., min_length=1, max_length=255)
    president_phone: str = Field(..., min_length=1, max_length=30)

    @field_validator("president_phone")
    @classmethod
    def normalize_president_phone(cls, v: str) -> str:
        return validate_and_normalize_phone(v)


class UnitDetailsResponse(BaseModel):
    """Response schema for unit details."""
    
    model_config = ConfigDict(from_attributes=True)
    
    id: int
    registered_user_id: int
    registration_year: Optional[int]
    number_of_unit_members: Optional[int]


# Unit Members Schemas (for regular operations)
class UnitMemberBase(BaseModel):
    """Base schema for unit members."""
    
    name: str = Field(..., min_length=1, max_length=255)
    gender: Optional[str] = Field(None, max_length=10)
    dob: Optional[date] = None
    number: Optional[str] = Field(None, max_length=30)
    qualification: Optional[str] = Field(None, max_length=255)
    blood_group: Optional[str] = Field(None, max_length=10)
    residence_location: Optional[ResidenceLocation] = None
    residence_state_id: Optional[int] = None
    residence_city_id: Optional[int] = None
    residence_state_name: Optional[str] = None
    residence_city_name: Optional[str] = None
    residence_country_name: Optional[str] = None
    residence_country_id: Optional[int] = None

    @field_validator("gender", mode="before")
    @classmethod
    def normalize_gender(cls, v: Optional[str]) -> Optional[str]:
        return validate_member_gender(v)


def _validate_residence_fields(
    residence_location: Optional[ResidenceLocation],
    residence_state_id: Optional[int],
    residence_city_id: Optional[int],
) -> None:
    if residence_location is None:
        raise ValueError('Living location is required')
    if residence_location == ResidenceLocation.WITHIN_KERALA:
        return
    if not residence_state_id:
        raise ValueError('State is required when the member does not live in Kerala')


class UnitMemberCreate(UnitMemberBase):
    """Create schema for unit members."""

    @model_validator(mode='after')
    def require_member_fields(self):
        _validate_residence_fields(
            self.residence_location,
            self.residence_state_id,
            self.residence_city_id,
        )
        if not self.blood_group:
            raise ValueError('Blood group is required')
        if self.blood_group not in VALID_BLOOD_GROUPS:
            raise ValueError(
                f"Invalid blood group. Must be one of: {', '.join(sorted(VALID_BLOOD_GROUPS))}"
            )
        if self.number:
            self.number = validate_and_normalize_phone(self.number)
        return self


class UnitMemberUpdate(BaseModel):
    """Update schema for unit members."""
    
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    gender: Optional[str] = Field(None, max_length=10)
    dob: Optional[date] = None
    number: Optional[str] = Field(None, max_length=30)
    qualification: Optional[str] = Field(None, max_length=255)
    blood_group: Optional[str] = Field(None, max_length=10)
    residence_location: Optional[ResidenceLocation] = None
    residence_state_id: Optional[int] = None
    residence_city_id: Optional[int] = None

    @field_validator("gender", mode="before")
    @classmethod
    def normalize_gender(cls, v: Optional[str]) -> Optional[str]:
        return validate_member_gender(v)

    @model_validator(mode='after')
    def validate_residence_update(self):
        if self.residence_location is not None:
            _validate_residence_fields(
                self.residence_location,
                self.residence_state_id,
                self.residence_city_id,
            )
        if self.number:
            self.number = validate_and_normalize_phone(self.number)
        return self


class UnitMemberResponse(UnitMemberBase):
    """Response schema for unit members."""
    
    model_config = ConfigDict(from_attributes=True)
    
    id: int
    registered_user_id: int
    added_registration_cycle_id: Optional[int] = None


# Unit Officials Schemas (for regular operations)
class UnitOfficialsUpdate(BaseModel):
    """Update schema for unit officials."""
    
    position: str = Field(..., description="Position: President, Vice President, Secretary, Joint Secretary, or Treasurer")
    name: str = Field(..., min_length=1, max_length=255)
    phone: str = Field(..., min_length=1, max_length=30)
    designation: Optional[str] = Field(None, max_length=50, description="Only for President position")

    @field_validator("phone")
    @classmethod
    def normalize_phone(cls, v: str) -> str:
        return validate_and_normalize_phone(v)


class UnitOfficialsResponse(BaseModel):
    """Response schema for unit officials."""
    
    model_config = ConfigDict(from_attributes=True)
    
    id: int
    registered_user_id: int
    president_designation: Optional[str]
    president_name: Optional[str]
    president_phone: Optional[str]
    vice_president_name: Optional[str]
    vice_president_phone: Optional[str]
    secretary_name: Optional[str]
    secretary_phone: Optional[str]
    joint_secretary_name: Optional[str]
    joint_secretary_phone: Optional[str]
    treasurer_name: Optional[str]
    treasurer_phone: Optional[str]


# Unit Councilor Schemas
class UnitCouncilorCreate(BaseModel):
    """Create schema for unit councilors."""
    
    unit_member_id: int = Field(..., gt=0)


class UnitCouncilorResponse(BaseModel):
    """Response schema for unit councilors."""
    
    model_config = ConfigDict(from_attributes=True)
    
    id: int
    registered_user_id: int
    unit_member_id: int


# Status Update Schema
class StatusUpdate(BaseModel):
    """Schema for updating registration status."""
    
    status: str = Field(..., min_length=1, max_length=100)


# Request Action Schema (for approve/reject/revert with optional remarks)
class RequestActionSchema(BaseModel):
    """Schema for request actions (approve, reject, revert) with optional remarks."""
    
    remarks: Optional[str] = Field(None, max_length=1000)

