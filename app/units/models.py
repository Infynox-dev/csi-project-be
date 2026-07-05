"""Units module models for managing unit members, transfers, and change requests."""

import enum
from datetime import date, datetime
from typing import Optional

from app.common.datetime_utils import now_ist, today_ist

from sqlalchemy import Date, DateTime, Enum, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.common.db import Base
from app.auth.models import ResidenceLocation


class RequestStatus(str, enum.Enum):
    """Status enum for various request types."""
    
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class MemberRemovalType(str, enum.Enum):
    """How a member landed in removed_unit_member (distinct from seasonal archival)."""

    ADMIN = "ADMIN"
    LEGACY = "LEGACY"


class ArchivedUnitMember(Base):
    """
    Stores information about former UnitMembers who exceed the age threshold.
    These members are archived and removed from the active UnitMembers table.
    """
    
    __tablename__ = "archived_unit_member"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    registered_user_id: Mapped[int] = mapped_column(
        ForeignKey("custom_user.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    gender: Mapped[Optional[str]] = mapped_column(String(10))
    dob: Mapped[date] = mapped_column(Date, nullable=False)
    number: Mapped[str] = mapped_column(String(30), nullable=False)
    qualification: Mapped[Optional[str]] = mapped_column(String(255))
    blood_group: Mapped[Optional[str]] = mapped_column(String(10))
    archived_at: Mapped[datetime] = mapped_column(DateTime, default=now_ist, nullable=False)
    archive_year: Mapped[Optional[str]] = mapped_column(String(20))
    archive_reason: Mapped[Optional[str]] = mapped_column(Text)

    @property
    def age(self) -> int:
        """Calculate current age from date of birth."""
        today = today_ist()
        return today.year - self.dob.year - ((today.month, today.day) < (self.dob.month, self.dob.day))


class RemovedUnitMember(Base):
    """
    Stores information about deliberately removed UnitMembers.
    These members are stored here and removed from the active UnitMembers table.
    """
    
    __tablename__ = "removed_unit_member"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    registered_user_id: Mapped[int] = mapped_column(
        ForeignKey("custom_user.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    gender: Mapped[Optional[str]] = mapped_column(String(10))
    dob: Mapped[date] = mapped_column(Date, nullable=False)
    number: Mapped[str] = mapped_column(String(30), nullable=False)
    qualification: Mapped[Optional[str]] = mapped_column(String(255))
    blood_group: Mapped[Optional[str]] = mapped_column(String(10))
    archived_at: Mapped[datetime] = mapped_column(DateTime, default=now_ist, nullable=False)
    delete_reason: Mapped[Optional[str]] = mapped_column(Text)
    deleted_by_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("custom_user.id"), nullable=True, index=True
    )
    original_member_id: Mapped[Optional[int]] = mapped_column(Integer)
    notified_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    removal_type: Mapped[MemberRemovalType] = mapped_column(
        Enum(MemberRemovalType),
        default=MemberRemovalType.LEGACY,
        nullable=False,
    )

    @property
    def removed_at(self) -> datetime:
        """Alias for archived_at — this table is not used for seasonal archival."""
        return self.archived_at

    @property
    def age(self) -> int:
        """Calculate current age from date of birth."""
        today = today_ist()
        return today.year - self.dob.year - ((today.month, today.day) < (self.dob.month, self.dob.day))


class UnitTransferRequest(Base):
    """
    Manages unit transfer requests for members moving between units.
    Tracks original and destination units along with approval status.
    """
    
    __tablename__ = "unit_transfer_request"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    unit_member_id: Mapped[int] = mapped_column(
        ForeignKey("unit_members.id"), nullable=False, index=True
    )
    current_unit_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("unit_name.id"), nullable=True
    )
    original_registered_user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("custom_user.id"), nullable=True
    )
    destination_unit_id: Mapped[int] = mapped_column(
        ForeignKey("unit_name.id"), nullable=False
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    proof: Mapped[str] = mapped_column(String(500), nullable=False)  # File path
    status: Mapped[RequestStatus] = mapped_column(
        Enum(RequestStatus), default=RequestStatus.PENDING, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_ist, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=now_ist, onupdate=now_ist, nullable=False
    )
    
    # Relationships
    unit_member = relationship("UnitMembers", foreign_keys=[unit_member_id])


class UnitMemberChangeRequest(Base):
    """
    Manages change requests for unit member information.
    Stores both new and original values for auditing and reversion.
    """
    
    __tablename__ = "unit_member_change_request"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    unit_member_id: Mapped[int] = mapped_column(
        ForeignKey("unit_members.id"), nullable=False, index=True
    )
    
    # New values
    name: Mapped[Optional[str]] = mapped_column(String(255))
    gender: Mapped[Optional[str]] = mapped_column(String(10))
    dob: Mapped[Optional[date]] = mapped_column(Date)
    blood_group: Mapped[Optional[str]] = mapped_column(String(10))
    qualification: Mapped[Optional[str]] = mapped_column(String(255))
    number: Mapped[Optional[str]] = mapped_column(String(30))
    residence_location: Mapped[Optional[ResidenceLocation]] = mapped_column(
        Enum(ResidenceLocation),
        nullable=True,
    )
    residence_state_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("state.id"), nullable=True, index=True
    )
    residence_city_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("city.id"), nullable=True, index=True
    )
    
    # Original values for reversion
    original_name: Mapped[Optional[str]] = mapped_column(String(255))
    original_gender: Mapped[Optional[str]] = mapped_column(String(10))
    original_dob: Mapped[Optional[date]] = mapped_column(Date)
    original_blood_group: Mapped[Optional[str]] = mapped_column(String(10))
    original_qualification: Mapped[Optional[str]] = mapped_column(String(255))
    original_number: Mapped[Optional[str]] = mapped_column(String(30))
    original_residence_location: Mapped[Optional[ResidenceLocation]] = mapped_column(
        Enum(ResidenceLocation),
        nullable=True,
    )
    original_residence_state_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("state.id"), nullable=True
    )
    original_residence_city_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("city.id"), nullable=True
    )
    
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    proof: Mapped[str] = mapped_column(String(500), nullable=False)  # File path
    status: Mapped[RequestStatus] = mapped_column(
        Enum(RequestStatus), default=RequestStatus.PENDING, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_ist, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=now_ist, onupdate=now_ist, nullable=False
    )


class UnitOfficialsChangeRequest(Base):
    """
    Manages change requests for unit officials information.
    Stores both new and original values for all official positions.
    """
    
    __tablename__ = "unit_officials_change_request"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    unit_official_id: Mapped[int] = mapped_column(
        ForeignKey("unit_officials.id"), nullable=False, index=True
    )
    
    # President fields
    president_designation: Mapped[Optional[str]] = mapped_column(String(50))
    original_president_designation: Mapped[Optional[str]] = mapped_column(String(50))
    president_name: Mapped[Optional[str]] = mapped_column(String(255))
    original_president_name: Mapped[Optional[str]] = mapped_column(String(255))
    president_phone: Mapped[Optional[str]] = mapped_column(String(30))
    original_president_phone: Mapped[Optional[str]] = mapped_column(String(30))
    
    # Vice President fields
    vice_president_name: Mapped[Optional[str]] = mapped_column(String(255))
    original_vice_president_name: Mapped[Optional[str]] = mapped_column(String(255))
    vice_president_phone: Mapped[Optional[str]] = mapped_column(String(30))
    original_vice_president_phone: Mapped[Optional[str]] = mapped_column(String(30))
    
    # Secretary fields
    secretary_name: Mapped[Optional[str]] = mapped_column(String(255))
    original_secretary_name: Mapped[Optional[str]] = mapped_column(String(255))
    secretary_phone: Mapped[Optional[str]] = mapped_column(String(30))
    original_secretary_phone: Mapped[Optional[str]] = mapped_column(String(30))
    
    # Joint Secretary fields
    joint_secretary_name: Mapped[Optional[str]] = mapped_column(String(255))
    original_joint_secretary_name: Mapped[Optional[str]] = mapped_column(String(255))
    joint_secretary_phone: Mapped[Optional[str]] = mapped_column(String(30))
    original_joint_secretary_phone: Mapped[Optional[str]] = mapped_column(String(30))
    
    # Treasurer fields
    treasurer_name: Mapped[Optional[str]] = mapped_column(String(255))
    original_treasurer_name: Mapped[Optional[str]] = mapped_column(String(255))
    treasurer_phone: Mapped[Optional[str]] = mapped_column(String(30))
    original_treasurer_phone: Mapped[Optional[str]] = mapped_column(String(30))
    
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    proof: Mapped[str] = mapped_column(String(500), nullable=False)  # File path
    status: Mapped[RequestStatus] = mapped_column(
        Enum(RequestStatus), default=RequestStatus.PENDING, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_ist, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=now_ist, onupdate=now_ist, nullable=False
    )


class UnitCouncilorChangeRequest(Base):
    """
    Manages change requests for unit councilor assignments.
    Allows changing which member is assigned as a councilor.
    """
    
    __tablename__ = "unit_councilor_change_request"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    # Kept for reference and approve/revert when the councilor still exists (no FK).
    unit_councilor_id: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, index=True
    )

    # New unit member selection
    unit_member_id: Mapped[Optional[int]] = mapped_column(ForeignKey("unit_members.id"))
    original_unit_member_id: Mapped[Optional[int]] = mapped_column(ForeignKey("unit_members.id"))

    # Snapshotted at request creation for audit when councilor/member rows are removed.
    original_member_name: Mapped[Optional[str]] = mapped_column(String(255))
    new_member_name: Mapped[Optional[str]] = mapped_column(String(255))
    
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    proof: Mapped[str] = mapped_column(String(500), nullable=False)  # File path
    status: Mapped[RequestStatus] = mapped_column(
        Enum(RequestStatus), default=RequestStatus.PENDING, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_ist, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=now_ist, onupdate=now_ist, nullable=False
    )


class ArchivedMemberConcernRequest(Base):
    """
    Unit-raised concerns about recently archived members for admin review.
    """

    __tablename__ = "archived_member_concern_request"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    archived_unit_member_id: Mapped[int] = mapped_column(
        ForeignKey("archived_unit_member.id"), nullable=False, index=True
    )
    registered_user_id: Mapped[int] = mapped_column(
        ForeignKey("custom_user.id"), nullable=False, index=True
    )
    concern_text: Mapped[str] = mapped_column(Text, nullable=False)
    admin_response: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[RequestStatus] = mapped_column(
        Enum(RequestStatus), default=RequestStatus.PENDING, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_ist, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=now_ist, onupdate=now_ist, nullable=False
    )


class UnitMemberAddRequest(Base):
    """
    Manages requests to add new members to a unit.
    Stores all member information until approved by admin.
    """
    
    __tablename__ = "unit_member_add_request"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    registered_user_id: Mapped[int] = mapped_column(
        ForeignKey("custom_user.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    gender: Mapped[str] = mapped_column(String(10), nullable=False)
    dob: Mapped[date] = mapped_column(Date, nullable=False)
    number: Mapped[str] = mapped_column(String(30), nullable=False)
    qualification: Mapped[Optional[str]] = mapped_column(String(255))
    blood_group: Mapped[Optional[str]] = mapped_column(String(10))
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    proof: Mapped[Optional[str]] = mapped_column(String(500))  # File path
    residence_location: Mapped[Optional[ResidenceLocation]] = mapped_column(
        Enum(ResidenceLocation),
        nullable=True,
    )
    residence_state_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("state.id"), nullable=True, index=True
    )
    residence_city_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("city.id"), nullable=True, index=True
    )
    status: Mapped[RequestStatus] = mapped_column(
        Enum(RequestStatus), default=RequestStatus.PENDING, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_ist, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=now_ist, onupdate=now_ist, nullable=False
    )


class RegistrationCyclePathType(str, enum.Enum):
    """Whether a registration cycle is a first-time or renewal registration."""

    FRESH = "fresh"
    RENEWAL = "renewal"


class UnitRegistrationCycle(Base):
    """
    Tracks per-year registration status for each unit.
    One row per unit per registration year (ending year, e.g. 2025 = 2024-2025).
    """

    __tablename__ = "unit_registration_cycle"
    __table_args__ = (
        UniqueConstraint(
            "registered_user_id",
            "registration_year",
            name="uq_unit_registration_cycle_user_year",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    registered_user_id: Mapped[int] = mapped_column(
        ForeignKey("custom_user.id"), nullable=False, index=True
    )
    registration_year: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(64), default="Registration Started", nullable=False)
    path_type: Mapped[str] = mapped_column(String(16), default="fresh", nullable=False)
    member_count_at_submit: Mapped[Optional[int]] = mapped_column(Integer)
    total_fee_at_submit: Mapped[Optional[int]] = mapped_column(Integer)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=now_ist, nullable=False)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    registered_user = relationship("CustomUser", back_populates="registration_cycles")
    payments = relationship("UnitRegistrationPayment", back_populates="registration_cycle")


class PaymentProofStatus(str, enum.Enum):
    """Status enum for unit registration payment proof submissions."""

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class UnitRegistrationPayment(Base):
    """
    Tracks payment proof uploads for a unit's registration fee.
    Multiple submissions are allowed per registration (partial proofs,
    re-uploads after rejection, etc.). The registration is considered
    paid once at least one entry reaches APPROVED status.
    """

    __tablename__ = "unit_registration_payment"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    registered_user_id: Mapped[int] = mapped_column(
        ForeignKey("custom_user.id"), nullable=False, index=True
    )
    registration_cycle_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("unit_registration_cycle.id"), nullable=True, index=True
    )
    file_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)  # B2 object key
    total_amount: Mapped[Optional[int]] = mapped_column(Integer)  # Amount at time of submission
    balance_amount: Mapped[Optional[int]] = mapped_column(Integer)  # Remaining balance after approval
    approved_paid_amount: Mapped[Optional[int]] = mapped_column(Integer)  # Admin-entered amount on approval
    detected_paid_amount: Mapped[Optional[int]] = mapped_column(Integer)  # OCR-detected amount at upload
    status: Mapped[PaymentProofStatus] = mapped_column(
        Enum(PaymentProofStatus), default=PaymentProofStatus.PENDING, nullable=False
    )
    rejection_note: Mapped[Optional[str]] = mapped_column(Text)
    submitted_at: Mapped[datetime] = mapped_column(DateTime, default=now_ist, nullable=False)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    reviewed_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("custom_user.id"))

    registration_cycle = relationship("UnitRegistrationCycle", back_populates="payments")

