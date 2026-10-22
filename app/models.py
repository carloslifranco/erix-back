from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, JSON, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Claim(Base):
    __tablename__ = "claims"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    provider_name: Mapped[str] = mapped_column(String(200), nullable=False)
    consultation_date: Mapped[date] = mapped_column(Date, nullable=False)
    requested_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="awaiting_document", nullable=False)
    document_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    document_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    extracted_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    extracted_fields: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    risk_score: Mapped[float | None] = mapped_column(nullable=True)
    risk_signals: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    model_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    reviews: Mapped[list["Review"]] = relationship(back_populates="claim", cascade="all, delete-orphan")
    analytics: Mapped["ClaimAnalytics | None"] = relationship(
        back_populates="claim", cascade="all, delete-orphan", uselist=False,
    )


class ClaimAnalytics(Base):
    """Optional operator context, in a new table so existing claims need no ALTER."""

    __tablename__ = "claim_analytics"

    claim_id: Mapped[str] = mapped_column(ForeignKey("claims.id"), primary_key=True)
    provider_ref: Mapped[str] = mapped_column(String(120), index=True)
    beneficiary_ref: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    specialty: Mapped[str] = mapped_column(String(120))
    procedure_code: Mapped[str] = mapped_column(String(120))
    region: Mapped[str] = mapped_column(String(120))
    quantity: Mapped[int] = mapped_column(default=1)
    annual_limit: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    document_issue_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_synthetic: Mapped[bool] = mapped_column(default=False)
    report: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)

    claim: Mapped[Claim] = relationship(back_populates="analytics")


class Review(Base):
    __tablename__ = "reviews"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    claim_id: Mapped[str] = mapped_column(ForeignKey("claims.id"), index=True, nullable=False)
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    reviewer: Mapped[str] = mapped_column(String(120), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    claim: Mapped[Claim] = relationship(back_populates="reviews")
