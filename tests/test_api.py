from collections.abc import Iterator
from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import get_db
from app.core.security import require_api_key
from app.db.base import Base
from app.models import Claim
import app.main as main_module

test_engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(bind=test_engine, autoflush=False, expire_on_commit=False)
Base.metadata.create_all(bind=test_engine)
app = main_module.app
main_module.engine = test_engine


def override_get_db() -> Iterator:
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db
app.dependency_overrides[require_api_key] = lambda: None


def test_create_and_read_claim() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/v1/claims",
            json={
                "provider_name": "Clínica Exemplo",
                "consultation_date": date(2026, 10, 20).isoformat(),
                "requested_amount": "180.00",
            },
        )
        assert response.status_code == 201
        claim_id = response.json()["id"]
        assert response.json()["status"] == "awaiting_document"

        read_response = client.get(f"/v1/claims/{claim_id}")
        assert read_response.status_code == 200
        assert Decimal(read_response.json()["requested_amount"]) == Decimal("180.00")

        with TestingSessionLocal() as db:
            claim = db.get(Claim, claim_id)
            assert claim is not None
            claim.status = "ready_for_human_decision"
            db.commit()

        review_response = client.post(
            f"/v1/claims/{claim_id}/reviews",
            json={
                "decision": "needs_correction",
                "rationale": "Confirme a data legível no comprovante.",
                "reviewer": "Analista de teste",
            },
        )
        assert review_response.status_code == 201
        assert client.get(f"/v1/claims/{claim_id}").json()["status"] == "needs_correction"


def test_rejects_invalid_image_upload() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/v1/claims",
            json={
                "provider_name": "Clínica Exemplo",
                "consultation_date": date(2026, 10, 20).isoformat(),
                "requested_amount": "180.00",
            },
        )
        claim_id = response.json()["id"]

        upload_response = client.post(
            f"/v1/claims/{claim_id}/document",
            files={"file": ("comprovante.jpg", b"not an image", "image/jpeg")},
        )
        assert upload_response.status_code == 400
        assert upload_response.json()["detail"] == "Envie uma imagem JPEG ou PNG válida."


def test_rejects_invalid_claim_values() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/v1/claims",
            json={
                "provider_name": "x",
                "consultation_date": date(2026, 10, 20).isoformat(),
                "requested_amount": "-1",
            },
        )
        assert response.status_code == 422
