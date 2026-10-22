from datetime import date
from types import SimpleNamespace

from app.services.risk import RiskScorer, _extract_amount, _extract_date


def test_extract_amount_from_brazilian_currency() -> None:
    assert _extract_amount("Consulta médica total R$ 1.234,56") == 1234.56


def test_extract_date_from_receipt() -> None:
    assert _extract_date("Data: 20/10/2026") == date(2026, 10, 20)


def test_consultation_date_is_not_patient_birth_date() -> None:
    assert _extract_date("Nascimento do paciente: 01/02/1990\nData da consulta: 20/10/2026") == date(2026, 10, 20)
    assert _extract_date("Nascimento do paciente: 01/02/1990") is None


def test_mismatch_routes_for_human_review(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.risk.get_settings",
        lambda: SimpleNamespace(
            model_path="missing.joblib",
            low_ocr_confidence_threshold=0.65,
        ),
    )
    result = RiskScorer().assess(
        text="Consulta 20/10/2026 R$ 200,00",
        requested_amount=150.0,
        consultation_date=date(2026, 10, 20),
        ocr_confidence=0.95,
        duplicate_document=False,
    )
    assert result.score == 0.35
    assert "amount_mismatch" in result.signals
