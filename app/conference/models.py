"""Conference module models."""

from datetime import datetime
from typing import Optional
import enum

from app.common.datetime_utils import now_ist

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.common.db import Base


class PaymentStatusEnum(str, enum.Enum):
    """Payment status enum matching database type."""
    PENDING = "PENDING"
    PROOF_UPLOADED = "PROOF_UPLOADED"
    PAID = "PAID"
    DECLINED = "DECLINED"


class ConferenceSettings(Base):
    """Singleton settings for the conference module."""

    __tablename__ = "conference_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Per-delegate fee in INR (officials + added members)
    delegate_fee: Mapped[int] = mapped_column(Integer, default=300, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=now_ist, onupdate=now_ist, nullable=False
    )


class Conference(Base):
    """Conference model for managing conferences."""
    
    __tablename__ = "conference"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    details: Mapped[str] = mapped_column(Text, nullable=False)
    added_on: Mapped[datetime] = mapped_column(DateTime, default=now_ist, nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="Active", nullable=False)


class ConferenceRegistrationData(Base):
    """Registration status tracking for conference district officials."""
    
    __tablename__ = "conference_registration_data"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    district_official_id: Mapped[int] = mapped_column(
        ForeignKey("custom_user.id"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(100), default="Registration Started", nullable=False)


class ConferenceDelegate(Base):
    """
    Conference delegates model linking officials and their member delegates.
    Each delegate entry represents either an official or a member delegated by an official.
    """
    
    __tablename__ = "conference_delegate"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    conference_id: Mapped[int] = mapped_column(
        ForeignKey("conference.id"), nullable=False, index=True
    )
    officials_id: Mapped[int] = mapped_column(
        ForeignKey("custom_user.id"), nullable=False, index=True
    )
    members_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("unit_members.id"), nullable=True, index=True
    )


class ConferencePayment(Base):
    """Payment proof rows for a district conference ledger."""

    __tablename__ = "conference_payment"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    conference_id: Mapped[int] = mapped_column(
        ForeignKey("conference.id"), nullable=False, index=True
    )
    clergy_district_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("clergy_district.id"), nullable=True, index=True
    )
    amount_to_pay: Mapped[Optional[float]] = mapped_column(Numeric(10, 2), nullable=True)
    total_amount: Mapped[Optional[int]] = mapped_column(Integer)
    balance_amount: Mapped[Optional[int]] = mapped_column(Integer)
    approved_paid_amount: Mapped[Optional[int]] = mapped_column(Integer)
    uploaded_by_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("custom_user.id"), nullable=True, index=True
    )
    proof_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    date: Mapped[datetime] = mapped_column(DateTime, default=now_ist, nullable=False)
    status: Mapped[Optional[PaymentStatusEnum]] = mapped_column(
        Enum(PaymentStatusEnum, name='paymentstatus', create_type=False),
        nullable=True
    )
    payment_reference: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    rejection_note: Mapped[Optional[str]] = mapped_column(Text)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    reviewed_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("custom_user.id"))

    @property
    def submitted_at(self) -> datetime:
        """Alias so Units ledger helpers can sort conference proofs."""
        return self.date


class FoodPreference(Base):
    """Food preference tracking for conference delegates by district."""
    
    __tablename__ = "food_preference"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    conference_id: Mapped[int] = mapped_column(
        ForeignKey("conference.id"), nullable=False, index=True
    )
    veg_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    non_veg_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    uploaded_by_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("custom_user.id"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_ist, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=now_ist, onupdate=now_ist, nullable=False
    )
