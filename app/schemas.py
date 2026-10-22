from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from typing_extensions import Annotated


ProviderName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=200)]


class AnalyticsContext(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    provider_ref: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=120)]
    beneficiary_ref: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=120)] | None = None
    specialty: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=120)]
    procedure_code: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=120)]
    region: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=120)]
    quantity: int = Field(default=1, ge=1, le=1000)
    annual_limit: Decimal | None = Field(default=None, gt=0, max_digits=12, decimal_places=2)
    document_issue_date: date | None = None
    is_synthetic: bool = False


class AnalyticsRead(AnalyticsContext):
    report: dict[str, object] | None = None


class ClaimCreate(BaseModel):
    provider_name: ProviderName
    consultation_date: date
    requested_amount: Decimal = Field(gt=0, max_digits=10, decimal_places=2)
    analytics: AnalyticsContext | None = None


class ReviewCreate(BaseModel):
    decision: Literal["approved", "denied", "needs_correction"]
    rationale: str = Field(min_length=5, max_length=2000)
    reviewer: str = Field(min_length=2, max_length=120)


class VerificationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    patient_name: ProviderName | None = None
    patient_cpf: str | None = Field(default=None, min_length=11, max_length=14, pattern=r"^[0-9.\-]+$")
    patient_birth_date: date | None = None


class ReviewRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    claim_id: str
    decision: str
    rationale: str
    reviewer: str
    created_at: datetime


class ClaimRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    provider_name: str
    consultation_date: date
    requested_amount: Decimal
    status: str
    extracted_fields: dict[str, object] | None
    risk_score: float | None
    risk_signals: list[str]
    model_version: str | None
    created_at: datetime
    updated_at: datetime
    reviews: list[ReviewRead] = Field(default_factory=list)
    analytics: AnalyticsRead | None = None
