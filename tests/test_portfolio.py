from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import Claim, ClaimAnalytics
from app.services.portfolio import assess_ml, assess_portfolio, build_features, dashboard
from app.services.portfolio_training import train_portfolio


@pytest.fixture
def settings(monkeypatch, tmp_path):
    settings = SimpleNamespace(
        portfolio_model_path=str(tmp_path / "model.joblib"),
        portfolio_demo_model_path=str(tmp_path / "demo.joblib"),
        portfolio_min_peers=5, portfolio_patient_monthly_threshold=12,
        low_ocr_confidence_threshold=0.65, portfolio_review_threshold=80,
    )
    monkeypatch.setattr("app.services.portfolio.get_settings", lambda: settings)
    return settings


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def record(at, provider="MED-1", beneficiary="BEN-1", amount=200, quantity=1, region="Pinheiros", synthetic=True):
    claim = Claim(
        id=str(uuid4()), provider_name=provider, consultation_date=at.date(),
        requested_amount=Decimal(str(amount)), created_at=at, status="ready_for_human_decision",
        extracted_fields={"amount": amount, "ocr_confidence": 0.99},
    )
    claim.analytics = ClaimAnalytics(
        provider_ref=provider, beneficiary_ref=beneficiary, specialty="Dermatologia",
        procedure_code="consulta", region=region, quantity=quantity,
        annual_limit=Decimal("5000"), is_synthetic=synthetic,
    )
    return claim


def test_features_are_past_only_and_peer_prices_are_comparable(settings):
    now = datetime.now(timezone.utc)
    current = record(now, amount=600, quantity=2)
    peers = [record(now - timedelta(days=1, hours=i), provider=f"PEER-{i % 3}", amount=400, quantity=2) for i in range(6)]
    ignored = [record(now + timedelta(days=1), amount=1), current,
               record(now - timedelta(days=1), provider="SELF-OTHER-REGION", amount=99999, region="Santana"),
               record(now - timedelta(days=1), provider="OPERATIONAL", amount=99999, synthetic=False),
               record(now - timedelta(days=91), provider="OLD", amount=99999)]
    own_price = record(now - timedelta(days=1), amount=99999)
    features, evidence = build_features(current, peers + ignored + [own_price])
    assert evidence["peer_median"] == 200
    assert evidence["peer_sample_size"] == 6
    assert evidence["unit_amount"] == 300
    assert features["price_ratio"] == 1.5
    assert features["provider_documents_30d"] == 2


def test_one_other_provider_cannot_be_a_peer_baseline(settings):
    now = datetime.now(timezone.utc)
    current = record(now)
    features, evidence = build_features(current, [record(now - timedelta(hours=i + 1), provider="SINGLE") for i in range(10)])
    assert features["peer_reference_available"] == 0
    assert evidence["peer_median"] is None


def test_price_splitting_and_quality_have_explanations(settings, db):
    now = datetime.now(timezone.utc)
    peers = [record(now - timedelta(days=1, hours=i), provider=f"PEER-{i % 3}", beneficiary=f"P-{i}") for i in range(6)]
    pair = [record(now - timedelta(minutes=i + 1)) for i in range(2)]
    current = record(now, amount=1000)
    db.add_all(peers + pair + [current])
    db.commit()
    report = assess_portfolio(current, db)
    codes = {s["code"] for s in report["signals"]}
    assert {"price_above_peers", "possible_splitting"}.issubset(codes)
    assert report["review_recommended"] is True
    assert report["ml"]["status"] == "not_trained"
    assert "probabilidade" in report["score_meaning"]


def test_frequent_treatment_is_context_not_confirmed_fraud(settings, db):
    now = datetime.now(timezone.utc)
    past = [record(now - timedelta(days=i + 1), provider=f"MED-{i % 3}") for i in range(13)]
    current = record(now)
    db.add_all(past + [current])
    db.commit()
    report = assess_portfolio(current, db)
    assert report["features"]["beneficiary_documents_30d"] == 14
    assert any(s["code"] == "beneficiary_frequency" and "legítimo" in s["message"] for s in report["signals"])
    assert report["evidence"]["annual_requested_limit_percent"] == 56


def test_corrupt_optional_model_is_inconclusive(settings):
    Path(settings.portfolio_demo_model_path).write_bytes(b"not a model")
    now = datetime.now(timezone.utc)
    current = record(now)
    features, _ = build_features(current, [])
    assert assess_ml(features, current)["status"] == "model_error"


def test_temporal_training_scores_only_new_cases_and_separates_datasets(settings):
    now = datetime.now(timezone.utc)
    claims = [record(now - timedelta(days=70) + timedelta(hours=i * 8),
                     provider=f"MED-{i % 8}", beneficiary=f"BEN-{i % 31}", amount=200 + i % 31)
              for i in range(120)]
    metadata = train_portfolio(claims, Path(settings.portfolio_demo_model_path), True)
    assert metadata["training_rows"] >= 30
    assert metadata["training_end"] < metadata["holdout_start"]
    old_features, _ = build_features(claims[-1], claims)
    assert assess_ml(old_features, claims[-1])["status"] == "outside_training_period"
    current = record(datetime.now(timezone.utc) + timedelta(seconds=1), amount=1500)
    features, _ = build_features(current, claims)
    report = assess_ml(features, current)
    assert report["status"] == "available"
    assert 0 <= report["priority"] <= 100
    assert "probability" not in report
    # Operational data cannot accidentally load the demo model.
    current.analytics.is_synthetic = False
    assert assess_ml(features, current)["status"] == "not_trained"


def test_training_without_enough_history_does_not_publish_a_model(settings, tmp_path):
    path = tmp_path / "empty.joblib"
    with pytest.raises(ValueError, match="40 pedidos"):
        train_portfolio([], path, True)
    assert not path.exists()


def test_dashboard_does_not_double_count_money_or_mix_synthetic_and_operational(settings, db):
    now = datetime.now(timezone.utc)
    claims = [record(now - timedelta(hours=2), amount=400), record(now - timedelta(hours=1), amount=300), record(now, amount=900, synthetic=False)]
    for c in claims:
        db.add(c)
        db.flush()
        report = assess_portfolio(c, db)
        report.update(priority=90, review_recommended=True)
        report["signals"] = [
            {"code": "price", "category": "Preço", "priority": 85, "message": "Teste"},
            {"code": "duplicate", "category": "Duplicidade", "priority": 90, "message": "Teste"},
        ]
        c.analytics.report = report
    claims[1].status = "approved"
    db.commit()
    data = dashboard(db, synthetic=True)
    assert data["summary"]["documents"] == 2
    assert data["summary"]["amount_in_review"] == 400
    assert sum(c["amount"] for c in data["categories"]) == 400
    assert data["summary"]["pending_review"] == 1
    assert dashboard(db, synthetic=False)["summary"]["documents"] == 1
    assert dashboard(db, synthetic=True, start=(now + timedelta(days=1)).date())["summary"]["documents"] == 0
