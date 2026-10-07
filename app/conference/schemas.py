"""Pydantic schemas for conference module."""

from datetime import datetime
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class ConferenceStatus(str, Enum):
    """Status enum for conferences."""
    
    ACTIVE = "Active"
    INACTIVE = "Inactive"
    COMPLETED = "Completed"


# Conference Schemas
class ConferenceBase(BaseModel):
    """Base schema for conferences."""
    
    title: str = Field(..., min_length=1, max_length=255)
    details: str = Field(..., min_length=1)


class ConferenceCreate(ConferenceBase):
    """Create schema for conferences."""
    pass


class ConferenceUpdate(BaseModel):
    """Update schema for conferences."""
    
    title: Optional[str] = Field(None, min_length=1, max_length=255)
    details: Optional[str] = Field(None, min_length=1)
    status: Optional[ConferenceStatus] = None


class ConferenceResponse(ConferenceBase):
    """Response schema for conferences."""
    
    model_config = ConfigDict(from_attributes=True)
    
    id: int
    added_on: datetime
    status: str


# Conference Registration Data Schemas
class ConferenceRegistrationDataBase(BaseModel):
    """Base schema for conference registration data."""
    
    district_official_id: int = Field(..., gt=0)
    status: str = Field(default="Registration Started", max_length=100)


class ConferenceRegistrationDataCreate(ConferenceRegistrationDataBase):
    """Create schema for conference registration data."""
    pass


class ConferenceRegistrationDataResponse(ConferenceRegistrationDataBase):
    """Response schema for conference registration data."""
    
    model_config = ConfigDict(from_attributes=True)
    
    id: int


# Conference Delegate Schemas
class ConferenceDelegateBase(BaseModel):
    """Base schema for conference delegates."""
    
    conference_id: int = Field(..., gt=0)
    officials_id: int = Field(..., gt=0)
    members_id: Optional[int] = Field(None, gt=0)


class ConferenceDelegateCreate(ConferenceDelegateBase):
    """Create schema for conference delegates."""
    pass


class ConferenceDelegateResponse(ConferenceDelegateBase):
    """Response schema for conference delegates."""
    
    model_config = ConfigDict(from_attributes=True)
    
    id: int


class AttendeeAdd(BaseModel):
    """Body for a district official adding an official or delegate."""

    role: Literal["official", "delegate"] = "delegate"
    food_preference: Optional[Literal["veg", "non-veg"]] = None
    accommodation_required: Optional[bool] = None


class AttendeePreferenceUpdate(BaseModel):
    """Per-person food and stay. Null means not set yet."""

    food_preference: Optional[Literal["veg", "non-veg"]] = None
    accommodation_required: Optional[bool] = None


# Food Preference Schemas
class FoodPreferenceBase(BaseModel):
    """Base schema for food preferences."""
    
    conference_id: int = Field(..., gt=0)
    veg_count: Optional[int] = Field(None, ge=0)
    non_veg_count: Optional[int] = Field(None, ge=0)


class FoodPreferenceCreate(FoodPreferenceBase):
    """Create schema for food preferences."""
    pass


class FoodPreferenceUpdate(BaseModel):
    """Update schema for food preferences."""
    
    veg_count: Optional[int] = Field(None, ge=0)
    non_veg_count: Optional[int] = Field(None, ge=0)


class FoodPreferenceResponse(FoodPreferenceBase):
    """Response schema for food preferences."""
    
    model_config = ConfigDict(from_attributes=True)
    
    id: int
    uploaded_by_id: Optional[int]
    created_at: datetime
    updated_at: datetime


# District Official Create Schema
class DistrictOfficialCreate(BaseModel):
    """Schema for creating district officials."""
    
    conference_id: int = Field(..., gt=0)
    member_id: int = Field(..., gt=0, description="Unit member to be made official")


class DistrictOfficialUpdate(BaseModel):
    """Schema for updating district officials."""
    
    conference_official_count: int = Field(..., ge=0)
    conference_member_count: int = Field(..., ge=0)


# Aggregated Conference Info Schema
class DistrictConferenceInfo(BaseModel):
    """Schema for district-wise conference information."""
    
    district_name: str
    officials: list
    members: list
    count_of_officials: int
    count_of_members: int
    count_of_male_members: int
    count_of_female_members: int
    count_of_male_officials: int
    count_of_female_officials: int
    count_of_total_male: int
    count_of_total_female: int
    total_count: int
    veg_count: int
    non_veg_count: int


class ConferenceInfoResponse(BaseModel):
    """Response schema for complete conference information."""
    
    conference_id: int
    district_info: dict


# Payment Info Schema
class DistrictPaymentInfo(BaseModel):
    """Schema for district-wise payment information."""
    
    district_name: str
    officials: list
    members: list
    payments: list
    count_of_officials: int
    count_of_members: int


class ConferenceSettingsResponse(BaseModel):
    """Conference module settings (singleton)."""

    model_config = ConfigDict(from_attributes=True)

    delegate_fee: int


class ConferenceSettingsUpdate(BaseModel):
    """Update conference module settings."""

    delegate_fee: int = Field(..., ge=0)
