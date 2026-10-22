import hashlib
import io
import logging
import uuid
from collections.abc import Iterator
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile, status
from PIL import Image, UnidentifiedImageError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import require_api_key
from app.db.session import SessionLocal
from app.models import Claim, Review
from app.schemas import ClaimCreate, ClaimRead, ReviewCreate, ReviewRead, VerificationCreate
from app.services.analyzer import analyze_claim
from app.services.verification import verify_document

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1", dependencies=[Depends(require_api_key)])
ALLOWED_FORMATS = {"JPEG": ".jpg", "PNG": ".png"}


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.post("/claims", response_model=ClaimRead, status_code=status.HTTP_201_CREATED)
def create_claim(payload: ClaimCreate, db: Session = Depends(get_db)) -> Claim:
    claim = Claim(
        id=str(uuid.uuid4()),
        provider_name=payload.provider_name,
        consultation_date=payload.consultation_date,
        requested_amount=payload.requested_amount,
    )
    db.add(claim)
    db.commit()
    db.refresh(claim)
    return claim


@router.get("/claims", response_model=list[ClaimRead])
def list_claims(db: Session = Depends(get_db)) -> list[Claim]:
    return list(db.scalars(select(Claim).order_by(Claim.created_at.desc())).all())


@router.get("/claims/{claim_id}", response_model=ClaimRead)
def get_claim(claim_id: str, db: Session = Depends(get_db)) -> Claim:
    claim = db.get(Claim, claim_id)
    if claim is None:
        raise HTTPException(status_code=404, detail="Solicitação não encontrada.")
    return claim


@router.post("/claims/{claim_id}/document", response_model=ClaimRead, status_code=202)
async def upload_document(
    claim_id: str,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> Claim:
    claim = db.get(Claim, claim_id)
    if claim is None:
        raise HTTPException(status_code=404, detail="Solicitação não encontrada.")
    if claim.status not in {"awaiting_document", "processing_error"}:
        raise HTTPException(status_code=409, detail="Esta solicitação não aceita novo documento neste estado.")

    settings = get_settings()
    content = await file.read(settings.max_upload_bytes + 1)
    await file.close()
    if not content:
        raise HTTPException(status_code=400, detail="O arquivo enviado está vazio.")
    if len(content) > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail="O arquivo excede o limite permitido.")

    try:
        image = Image.open(io.BytesIO(content))
        if image.width * image.height > 25_000_000:
            raise HTTPException(status_code=413, detail="A imagem excede a resolução permitida.")
        image.verify()
        image_format = image.format
    except HTTPException:
        raise
    except (UnidentifiedImageError, OSError) as exc:
        raise HTTPException(status_code=400, detail="Envie uma imagem JPEG ou PNG válida.") from exc
    if image_format not in ALLOWED_FORMATS:
        raise HTTPException(status_code=415, detail="Formato não suportado. Envie JPEG ou PNG.")

    digest = hashlib.sha256(content).hexdigest()
    duplicate = db.scalar(
        select(Claim.id).where(Claim.document_sha256 == digest, Claim.id != claim.id).limit(1)
    )
    upload_dir = Path(settings.upload_dir).resolve()
    upload_dir.mkdir(parents=True, exist_ok=True)
    document_path = upload_dir / f"{claim.id}{ALLOWED_FORMATS[image_format]}"
    document_path.write_bytes(content)

    claim.document_path = str(document_path)
    claim.document_sha256 = digest
    claim.status = "processing"
    claim.risk_signals = ["duplicate_document"] if duplicate else []
    db.commit()
    background_tasks.add_task(_analyze_in_background, claim.id, bool(duplicate))
    db.refresh(claim)
    return claim


def _analyze_in_background(claim_id: str, duplicate: bool) -> None:
    db = SessionLocal()
    try:
        claim = db.get(Claim, claim_id)
        if claim is not None:
            analyze_claim(claim, db, duplicate)
    except Exception:
        db.rollback()
        logger.exception("Erro não tratado no processamento da solicitação %s", claim_id)
    finally:
        db.close()


@router.get("/reviews", response_model=list[ClaimRead])
def list_reviews(db: Session = Depends(get_db)) -> list[Claim]:
    query = (
        select(Claim)
        .where(Claim.status.in_(["awaiting_review", "ready_for_human_decision"]))
        .order_by(Claim.created_at.asc())
    )
    return list(db.scalars(query).all())


@router.post("/claims/{claim_id}/reviews", response_model=ReviewRead, status_code=201)
def create_review(
    claim_id: str,
    payload: ReviewCreate,
    db: Session = Depends(get_db),
) -> Review:
    claim = db.get(Claim, claim_id)
    if claim is None:
        raise HTTPException(status_code=404, detail="Solicitação não encontrada.")
    if claim.status not in {"awaiting_review", "ready_for_human_decision"}:
        raise HTTPException(status_code=409, detail="A solicitação ainda não pode ser revisada.")

    review = Review(
        id=str(uuid.uuid4()),
        claim_id=claim_id,
        decision=payload.decision,
        rationale=payload.rationale,
        reviewer=payload.reviewer,
    )
    claim.status = payload.decision
    db.add(review)
    db.commit()
    db.refresh(review)
    return review


@router.post("/claims/{claim_id}/verification")
def verify_claim(
    claim_id: str,
    payload: VerificationCreate,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    claim = db.get(Claim, claim_id)
    if claim is None:
        raise HTTPException(status_code=404, detail="Solicitação não encontrada.")
    if claim.status not in {"awaiting_review", "ready_for_human_decision"} or not claim.extracted_fields:
        raise HTTPException(status_code=409, detail="Aguarde o OCR e use uma solicitação pendente de revisão.")
    report = verify_document(
        text=claim.extracted_text or "",
        ocr_confidence=float(claim.extracted_fields.get("ocr_confidence", 0)),
        **payload.model_dump(),
    )
    # Store results, not the supplied CPF/name/birth date. Existing raw OCR is
    # already persisted by the document pipeline. No registry request is made.
    claim.extracted_fields = {**claim.extracted_fields, "verification": report}
    if report["needs_review"]:
        claim.status = "awaiting_review"
        claim.risk_signals = list(dict.fromkeys([*claim.risk_signals, "local_verification_needs_review"]))
    db.commit()
    return report


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
