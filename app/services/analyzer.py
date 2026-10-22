import logging
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import Claim
from app.services.ocr import get_ocr_service
from app.services.risk import get_risk_scorer, _extract_amount, _extract_date
from app.services.verification import verify_document

logger = logging.getLogger(__name__)


def analyze_claim(claim: Claim, db: Session, duplicate_document: bool) -> None:
    if not claim.document_path:
        raise ValueError("Solicitação sem documento armazenado.")

    try:
        text, confidence = get_ocr_service().extract(Path(claim.document_path))
        assessment = get_risk_scorer().assess(
            text=text,
            requested_amount=float(claim.requested_amount),
            consultation_date=claim.consultation_date,
            ocr_confidence=confidence,
            duplicate_document=duplicate_document,
        )
        extracted_date = _extract_date(text)
        verification = verify_document(text, confidence)
        claim.extracted_text = text
        claim.extracted_fields = {
            "amount": _extract_amount(text),
            "consultation_date": extracted_date.isoformat() if extracted_date else None,
            "ocr_confidence": round(confidence, 4),
            "verification": verification,
        }
        claim.risk_score = assessment.score
        claim.risk_signals = assessment.signals
        claim.model_version = assessment.model_version
        claim.status = (
            "awaiting_review"
            if assessment.score >= get_settings().risk_review_threshold or not text or verification["needs_review"]
            else "ready_for_human_decision"
        )
        if verification["needs_review"]:
            claim.risk_signals = [*claim.risk_signals, "local_verification_needs_review"]
        if not text and "no_text_extracted" not in claim.risk_signals:
            claim.risk_signals.append("no_text_extracted")
            claim.status = "awaiting_review"
        db.commit()
    except Exception:
        db.rollback()
        claim.status = "processing_error"
        db.add(claim)
        db.commit()
        logger.exception("Falha ao analisar solicitação %s", claim.id)
