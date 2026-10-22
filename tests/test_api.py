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


def test_verification_persists_results_without_reference_identity() -> None:
    with TestClient(app) as client:
        created = client.post("/v1/claims", json={
            "provider_name": "Prestador de teste", "consultation_date": "2026-10-20",
            "requested_amount": "180.00",
        })
        claim_id = created.json()["id"]
        assert client.post(f"/v1/claims/{claim_id}/verification", json={}).status_code == 409
        with TestingSessionLocal() as db:
            claim = db.get(Claim, claim_id)
            claim.status = "ready_for_human_decision"
            claim.extracted_text = "Paciente: Pessoa Exemplo\nCRM-SP 12345"
            claim.extracted_fields = {"ocr_confidence": 0.99}
            db.commit()
        result = client.post(f"/v1/claims/{claim_id}/verification", json={
            "patient_name": "Outro Nome", "patient_cpf": "52998224725",
            "patient_birth_date": "1990-02-01",
        })
        assert result.status_code == 200
        assert result.json()["needs_review"] is True
        assert "52998224725" not in result.text and "1990-02-01" not in result.text
        stored = client.get(f"/v1/claims/{claim_id}").json()
        assert stored["extracted_fields"]["verification"] == result.json()
        assert stored["status"] == "awaiting_review"
        assert "local_verification_needs_review" in stored["risk_signals"]
        assert client.post("/v1/claims/missing/verification", json={}).status_code == 404
        assert client.post(f"/v1/claims/{claim_id}/verification", json={"patient_cpf": "abc"}).status_code == 422
        with TestingSessionLocal() as db:
            claim = db.get(Claim, claim_id)
            claim.status = "approved"
            db.commit()
        assert client.post(f"/v1/claims/{claim_id}/verification", json={}).status_code == 409


def test_analysis_includes_verification_and_routes_invalid_cpf(monkeypatch) -> None:
    from types import SimpleNamespace

    from app.services.analyzer import analyze_claim
    from app.services.risk import RiskAssessment

    monkeypatch.setattr("app.services.analyzer.get_ocr_service", lambda: SimpleNamespace(
        extract=lambda _: ("CPF: 111.111.111-11\nConsulta: 20/10/2026\nR$ 180,00", 0.99),
    ))
    monkeypatch.setattr("app.services.analyzer.get_risk_scorer", lambda: SimpleNamespace(
        assess=lambda **_: RiskAssessment(0.0, [], None),
    ))
    with TestingSessionLocal() as db:
        claim = Claim(
            id="automatic-verification-test", provider_name="Prestador de teste",
            consultation_date=date(2026, 10, 20), requested_amount=Decimal("180.00"),
            status="processing", document_path="test-image.png",
        )
        db.add(claim)
        db.commit()
        analyze_claim(claim, db, False)
        db.refresh(claim)
        assert claim.status == "awaiting_review"
        assert claim.risk_score == 0.0
        assert claim.extracted_fields["verification"]["needs_review"] is True
        assert "local_verification_needs_review" in claim.risk_signals


def test_upload_adds_operator_risk_and_dashboard(monkeypatch, tmp_path) -> None:
    import io
    from types import SimpleNamespace
    from PIL import Image
    from app.services.risk import RiskAssessment
    from app.core.config import get_settings

    upload_settings = get_settings().model_copy(update={"upload_dir": str(tmp_path / "uploads")})
    monkeypatch.setattr("app.api.get_settings", lambda: upload_settings)
    monkeypatch.setattr("app.api.SessionLocal", TestingSessionLocal)
    monkeypatch.setattr("app.services.analyzer.get_ocr_service", lambda: SimpleNamespace(
        extract=lambda _: ("Consulta: 20/10/2026\nValor: R$ 250,00", 0.99),
    ))
    monkeypatch.setattr("app.services.analyzer.get_risk_scorer", lambda: SimpleNamespace(
        assess=lambda **_: RiskAssessment(0.0, [], None),
    ))
    image = io.BytesIO()
    Image.new("RGB", (8, 8)).save(image, format="PNG")
    with TestClient(app) as client:
        created = client.post("/v1/claims", json={
            "provider_name": "Prestador ficticio API", "consultation_date": "2026-10-20",
            "requested_amount": "500.00", "analytics": {
                "provider_ref": "API-PROVIDER", "beneficiary_ref": "API-BENEFICIARY",
                "specialty": "Dermatologia", "procedure_code": "consulta", "region": "Teste API",
                "quantity": 1, "is_synthetic": True,
            },
        })
        assert created.status_code == 201
        assert created.json()["analytics"]["report"] is None
        claim_id = created.json()["id"]
        upload = client.post(f"/v1/claims/{claim_id}/document", files={"file": ("receipt.png", image.getvalue(), "image/png")})
        assert upload.status_code == 202
        claim = client.get(f"/v1/claims/{claim_id}").json()
        assert claim["status"] == "awaiting_review"
        assert claim["analytics"]["report"]["priority"] >= 80
        data = client.get("/v1/risk/dashboard?synthetic=true").json()
        assert any(row["claim_id"] == claim_id for row in data["claims"])
        assert client.get("/v1/risk/dashboard?start=2026-10-22&end=2026-01-01").status_code == 422
        assert client.post(f"/v1/claims/{claim_id}/portfolio-risk").status_code == 200
        assert client.post("/v1/claims/missing/portfolio-risk").status_code == 404
