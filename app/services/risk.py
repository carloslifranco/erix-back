import re
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.core.config import get_settings


class RiskAssessment:
    def __init__(self, score: float, signals: list[str], model_version: str | None) -> None:
        self.score = score
        self.signals = signals
        self.model_version = model_version


class RiskScorer:
    def __init__(self) -> None:
        self._model: Any = None
        self._model_version: str | None = None
        model_path = Path(get_settings().model_path)
        if model_path.is_file():
            import joblib

            artifact = joblib.load(model_path)
            self._model = artifact["model"]
            self._model_version = str(artifact["version"])

    def assess(
        self,
        text: str,
        requested_amount: float,
        consultation_date: date,
        ocr_confidence: float,
        duplicate_document: bool,
    ) -> RiskAssessment:
        settings = get_settings()
        signals: list[str] = []
        score = 0.0
        ocr_amount = _extract_amount(text)
        ocr_date = _extract_date(text)
        amount_mismatch = 0
        date_mismatch = 0

        if duplicate_document:
            signals.append("duplicate_document")
            score += 0.45
        if ocr_amount is not None and abs(ocr_amount - requested_amount) > 0.02:
            signals.append("amount_mismatch")
            amount_mismatch = 1
            score += 0.35
        if ocr_date is not None and ocr_date != consultation_date:
            signals.append("date_mismatch")
            date_mismatch = 1
            score += 0.30
        if ocr_confidence < settings.low_ocr_confidence_threshold:
            signals.append("low_ocr_confidence")
            score += 0.20

        if self._model is not None:
            features = [[amount_mismatch, date_mismatch, int(duplicate_document), ocr_confidence]]
            fraud_probability = float(self._model.predict_proba(features)[0][1])
            signals.append("model_assessment_available")
            score = max(score, fraud_probability)

        return RiskAssessment(
            score=min(score, 1.0),
            signals=signals,
            model_version=self._model_version,
        )


@lru_cache(maxsize=1)
def get_risk_scorer() -> RiskScorer:
    return RiskScorer()


def _extract_amount(text: str) -> float | None:
    candidates = re.findall(r"(?:R\$\s*)?(\d{1,3}(?:\.\d{3})*,\d{2}|\d+,\d{2})", text, re.IGNORECASE)
    if not candidates:
        return None
    normalized = candidates[-1].replace(".", "").replace(",", ".")
    try:
        return float(normalized)
    except ValueError:
        return None


def _extract_date(text: str) -> date | None:
    # Patient birth dates must not become the consultation date. Prefer an
    # explicitly labelled consultation/attendance date over other dates.
    lines = [line for line in text.splitlines() if "nascimento" not in line.casefold()]
    labelled = [line for line in lines if re.search(r"(?:consulta|atendimento)\s*:", line, re.IGNORECASE)]
    text = "\n".join(labelled or lines)
    match = re.search(r"\b(\d{2})[./-](\d{2})[./-](\d{4})\b", text)
    if not match:
        return None
    day, month, year = (int(part) for part in match.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return None
