"""Explicit opt-in synthetic history + training + new cases for a classroom demo."""

import random
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import joinedload

from app.core.config import get_settings
from app.db.base import Base
from app.db.session import SessionLocal, engine
from app.models import Claim, ClaimAnalytics
from app.services.portfolio import assess_portfolio
from app.services.portfolio_training import train_portfolio


def main() -> None:
    Base.metadata.create_all(bind=engine)
    rng = random.Random(42)
    run = uuid.uuid4().hex[:8]
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        def create(index, amount, provider, beneficiary, at, procedure="consulta", specialty="Dermatologia", consult_date=None, declared=None):
            claim = Claim(
                id=str(uuid.uuid4()), provider_name=f"Prestador fictício {provider}",
                consultation_date=consult_date or at.date(), requested_amount=Decimal(str(amount)),
                created_at=at, status="ready_for_human_decision",
                extracted_text="DADOS SINTÉTICOS — SEM COMPROVANTE REAL",
                extracted_fields={"amount": declared if declared is not None else amount, "ocr_confidence": 0.99},
            )
            claim.analytics = ClaimAnalytics(
                provider_ref=f"DEMO-{run}-{provider}", beneficiary_ref=f"DEMO-{run}-{beneficiary}",
                specialty=specialty, procedure_code=procedure, region="São Paulo / Pinheiros",
                quantity=1, annual_limit=Decimal("6000.00"), document_issue_date=at.date(), is_synthetic=True,
            )
            db.add(claim)
            db.commit()
            claim.analytics.report = assess_portfolio(claim, db)
            if claim.analytics.report["review_recommended"]:
                claim.status = "awaiting_review"
            db.commit()
            return claim

        for i in range(150):
            psychology = i % 2 == 0
            create(i, round(rng.uniform(180, 240) if psychology else rng.uniform(280, 380), 2),
                   f"PSI-{(i // 2) % 8}" if psychology else f"MED-{(i // 2) % 8}",
                   f"BEN-{i % 35}", now - timedelta(days=75) + timedelta(hours=i * 10),
                   "sessao_individual" if psychology else "consulta",
                   "Psicologia" if psychology else "Dermatologia")
        claims = list(db.scalars(select(Claim).options(joinedload(Claim.analytics))).all())
        metadata = train_portfolio(claims, Path(get_settings().portfolio_demo_model_path), synthetic=True)
        # Generated after training, so these cases are genuinely unseen by ML.
        for i in range(8):
            at = datetime.now(timezone.utc)
            create(150 + i, 950 if i == 0 else (330 if i < 4 else 350),
                   "MED-0" if i < 4 else "MED-1", "BEN-ALERTA" if i < 4 else f"BEN-NOVO-{i}",
                   at, consult_date=at.date(), declared=180 if i == 7 else None)
        print(f"Demo {run}: 150 pedidos históricos + 8 novos; todos marcados como sintéticos.")
        print(f"ML: {metadata['training_rows']} amostras de treino; {metadata['holdout_rows']} posteriores para avaliação.")
        print("Abra Painel da operadora e selecione Dados sintéticos. Nenhum documento ou pagamento real foi criado.")


if __name__ == "__main__":
    main()
