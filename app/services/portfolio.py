"""Operator risk triage: past-only features, peer comparisons and optional ML."""

import unicodedata
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import median

import joblib
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.core.config import get_settings
from app.models import Claim


FEATURES = [
    "price_ratio", "peer_reference_available", "provider_documents_30d",
    "beneficiary_documents_30d", "beneficiary_available", "pair_documents_day",
    "amount_difference_ratio", "duplicate_documents_30d", "ocr_confidence",
]
PROCESSED = {"awaiting_review", "ready_for_human_decision", "approved", "denied", "needs_correction"}
PENDING = {"awaiting_review", "ready_for_human_decision"}


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def key(value: str) -> str:
    return " ".join("".join(c for c in unicodedata.normalize("NFKD", value.casefold())
                            if not unicodedata.combining(c)).split())


def vector(features: dict[str, float]) -> list[float]:
    return [float(features[name]) for name in FEATURES]


def build_features(claim: Claim, history: list[Claim]) -> tuple[dict[str, float], dict[str, object]]:
    context = claim.analytics
    if context is None:
        raise ValueError("Informe o contexto da operadora para calcular o risco histórico.")
    at = utc(claim.created_at)
    # Strictly earlier submissions only, never current or future observations.
    past = [c for c in history if c.id != claim.id and utc(c.created_at) < at
            and c.status in PROCESSED and c.analytics
            and c.analytics.is_synthetic == context.is_synthetic]
    recent = [c for c in past if utc(c.created_at) >= at - timedelta(days=30)]
    cohort = (key(context.specialty), key(context.procedure_code), key(context.region))
    peers = [c for c in past if utc(c.created_at) >= at - timedelta(days=90)
             and (key(c.analytics.specialty), key(c.analytics.procedure_code), key(c.analytics.region)) == cohort
             and key(c.analytics.provider_ref) != key(context.provider_ref)]
    unit_amount = float(claim.requested_amount) / context.quantity
    peer_providers = len({key(c.analytics.provider_ref) for c in peers})
    enough = len(peers) >= get_settings().portfolio_min_peers and peer_providers >= 3
    peer_median = median(float(c.requested_amount) / c.analytics.quantity for c in peers) if enough else None
    provider = [c for c in recent if key(c.analytics.provider_ref) == key(context.provider_ref)]
    peer_volumes = Counter(key(c.analytics.provider_ref) for c in peers
                           if utc(c.created_at) >= at - timedelta(days=30))
    beneficiary = [c for c in recent if context.beneficiary_ref
                   and c.analytics.beneficiary_ref == context.beneficiary_ref
                   and key(c.analytics.procedure_code) == key(context.procedure_code)]
    pair = [c for c in past if context.beneficiary_ref
            and c.analytics.beneficiary_ref == context.beneficiary_ref
            and key(c.analytics.provider_ref) == key(context.provider_ref)
            and key(c.analytics.procedure_code) == key(context.procedure_code)
            and c.consultation_date == claim.consultation_date]
    yearly = [c for c in past if context.beneficiary_ref
              and c.analytics.beneficiary_ref == context.beneficiary_ref
              and c.consultation_date.year == claim.consultation_date.year]
    # Requested usage only; there is no paid/eligible ledger in this MVP.
    annual_requested = sum(float(c.requested_amount) for c in yearly) + float(claim.requested_amount)
    fields = claim.extracted_fields or {}
    confidence = float(fields.get("ocr_confidence", 0))
    extracted_amount = fields.get("amount")
    reliable_amount = extracted_amount is not None and confidence >= get_settings().low_ocr_confidence_threshold
    diff = abs(float(extracted_amount) - float(claim.requested_amount)) if reliable_amount else 0.0
    duplicate_count = sum(c.document_sha256 == claim.document_sha256 for c in recent) if claim.document_sha256 else 0
    features = {
        "price_ratio": unit_amount / peer_median if peer_median else 1.0,
        "peer_reference_available": float(enough),
        "provider_documents_30d": float(len(provider) + 1),
        "beneficiary_documents_30d": float(len(beneficiary) + 1) if context.beneficiary_ref else 0.0,
        "beneficiary_available": float(bool(context.beneficiary_ref)),
        "pair_documents_day": float(len(pair) + 1) if context.beneficiary_ref else 0.0,
        "amount_difference_ratio": diff / float(claim.requested_amount),
        "duplicate_documents_30d": float(duplicate_count),
        "ocr_confidence": confidence,
    }
    evidence = {
        "unit_amount": round(unit_amount, 2), "peer_median": round(peer_median, 2) if peer_median else None,
        "peer_sample_size": len(peers), "peer_providers": peer_providers,
        "peer_provider_volume_median": median(peer_volumes.values()) if len(peer_volumes) >= 3 else None,
        "peer_window_days": 90, "frequency_window_days": 30,
        "pair_amount_day": round(sum(float(c.requested_amount) for c in pair) + float(claim.requested_amount), 2),
        "annual_requested": round(annual_requested, 2),
        "annual_limit": float(context.annual_limit) if context.annual_limit else None,
        "annual_requested_limit_percent": round(100 * annual_requested / float(context.annual_limit), 1) if context.annual_limit else None,
        "declared_document_amount": extracted_amount,
        "amount_comparison_available": reliable_amount,
        "document_issue_date": context.document_issue_date.isoformat() if context.document_issue_date else None,
        "limitations": [
            "Frequência mede documentos apresentados à Erix; não todas as emissões do profissional.",
            "Uso anual soma valores solicitados conhecidos; não é saldo contratual nem pagamentos realizados.",
            "Pares usam especialidade, procedimento e região informados; não uma tabela oficial de preços.",
        ],
    }
    return features, evidence


def history_for(claim: Claim, db: Session) -> list[Claim]:
    start = min(utc(claim.created_at) - timedelta(days=90),
                datetime(claim.consultation_date.year, 1, 1, tzinfo=timezone.utc))
    return list(db.scalars(select(Claim).options(joinedload(Claim.analytics)).where(
        Claim.created_at < claim.created_at, Claim.created_at >= start,
        Claim.status.in_(PROCESSED),
    )).all())


def assess_ml(features: dict[str, float], claim: Claim) -> dict[str, object]:
    settings = get_settings()
    path = Path(settings.portfolio_demo_model_path if claim.analytics.is_synthetic else settings.portfolio_model_path)
    if not path.is_file():
        return {"status": "not_trained", "priority": None, "message": "Modelo histórico ainda não treinado."}
    try:
        artifact = joblib.load(path)
        if artifact["features"] != FEATURES or artifact["kind"] != "portfolio-isolation-forest-v1":
            raise ValueError("Contrato de features incompatível.")
        if artifact["synthetic"] != claim.analytics.is_synthetic:
            return {"status": "different_dataset", "priority": None, "message": "Modelo e pedido pertencem a bases diferentes (sintética/operacional)."}
        if utc(claim.created_at) <= datetime.fromisoformat(artifact["trained_at"]):
            return {"status": "outside_training_period", "priority": None,
                    "message": "Pedido anterior ao treino: sem pontuação ML para evitar avaliar o próprio treino."}
        raw = -float(artifact["model"].score_samples([vector(features)])[0])
        reference = artifact["reference_scores"]
        percentile = 100 * sum(float(v) <= raw for v in reference) / len(reference)
        anomalous = bool(artifact["model"].predict([vector(features)])[0] == -1)
        # Explicit policy mapping, not a calibrated fraud probability.
        priority = max(80, round(percentile)) if anomalous else min(79, round(percentile * 0.79))
        return {"status": "available", "version": artifact["version"], "priority": priority,
                "anomalous": anomalous, "raw_anomaly_score": round(raw, 6),
                "reference_percentile": round(percentile, 1), "training_rows": artifact["training_rows"],
                "trained_at": artifact["trained_at"]}
    except Exception:
        # A bad optional artifact must not discard the OCR or become a low-risk score.
        return {"status": "model_error", "priority": None,
                "message": "Modelo indisponível ou incompatível. Regras continuam; ML não concluído."}


def assess_portfolio(claim: Claim, db: Session) -> dict[str, object]:
    features, evidence = build_features(claim, history_for(claim, db))
    signals: list[dict[str, object]] = []

    def add(code: str, category: str, priority: int, message: str) -> None:
        signals.append({"code": code, "category": category, "priority": priority, "message": message})

    if features["peer_reference_available"] and features["price_ratio"] >= 1.8:
        add("price_above_peers", "Preço fora do padrão", 85,
            f"Preço unitário {features['price_ratio']:.2f} vezes a mediana dos pares.")
    if features["peer_reference_available"] and features["price_ratio"] <= 0.45:
        add("price_below_peers", "Preço fora do padrão", 65, "Preço unitário abaixo de 45% da mediana; conferir contexto e quantidade.")
    if features["beneficiary_documents_30d"] > get_settings().portfolio_patient_monthly_threshold:
        add("beneficiary_frequency", "Recorrência para análise", 80,
            f"{int(features['beneficiary_documents_30d'])} documentos do mesmo procedimento em 30 dias. Pode haver tratamento recorrente legítimo.")
    peer_volume = evidence["peer_provider_volume_median"]
    if peer_volume and features["provider_documents_30d"] >= 20 and features["provider_documents_30d"] > 3 * peer_volume:
        add("provider_frequency", "Recorrência para análise", 80,
            "Volume de documentos do prestador acima de três vezes a mediana dos pares em 30 dias (mínimo 20).")
    if features["pair_documents_day"] >= 3:
        add("possible_splitting", "Possível fracionamento", 85,
            f"{int(features['pair_documents_day'])} pedidos do mesmo beneficiário, prestador, procedimento e data. Conferir antes de concluir.")
    if features["amount_difference_ratio"] > 0 and abs(float(evidence["declared_document_amount"]) - float(claim.requested_amount)) > 0.02:
        add("document_amount_mismatch", "Valor divergente do documento", 85, "Valor solicitado difere do valor lido com OCR suficiente.")
    if features["duplicate_documents_30d"]:
        add("duplicate_document", "Documento repetido", 90, "Mesmo arquivo apresentado em outro pedido nos últimos 30 dias.")
    if features["ocr_confidence"] < get_settings().low_ocr_confidence_threshold or not evidence["amount_comparison_available"]:
        add("document_quality", "Dados insuficientes", 50, "Valor ausente ou OCR insuficiente; comparação documental inconclusiva.")
    visual = (claim.extracted_fields or {}).get("visual_analysis") or {}
    if visual.get("status") == "completed" and visual.get("verdict") == "indications_found":
        add("visual_indications", "Sinais visuais para conferência", 85,
            "Modelo visual apontou possível edição ou inconsistência de layout. Não comprova adulteração; veja as observações do Gemini na revisão.")
    elif visual.get("review_recommended"):
        add("visual_inconclusive", "Dados insuficientes", 50, "Análise visual solicitada ficou inconclusiva ou indisponível. Conferir manualmente.")
    ml = assess_ml(features, claim)
    if ml.get("anomalous"):
        add("ml_anomaly", "Padrão histórico atípico", int(ml["priority"]), "Combinação de frequência, preço e consistência atípica para o modelo histórico.")
    priority = max([int(s["priority"]) for s in signals] + [int(ml.get("priority") or 0)])
    return {
        "version": "portfolio-triage-v1", "assessed_at": datetime.now(timezone.utc).isoformat(),
        "priority": priority, "review_recommended": priority >= get_settings().portfolio_review_threshold,
        "threshold": get_settings().portfolio_review_threshold,
        "features": features, "evidence": evidence, "signals": signals, "ml": ml,
        "score_meaning": "Prioridade de revisão de 0 a 100; não é probabilidade de fraude.",
    }


def dashboard(db: Session, synthetic: bool, start: date | None = None, end: date | None = None) -> dict[str, object]:
    # Snapshot reports keep historical results stable when more documents arrive.
    claims = list(db.scalars(select(Claim).options(joinedload(Claim.analytics))
                            .where(Claim.status.in_(PROCESSED)).order_by(Claim.created_at)).all())
    claims = [c for c in claims if c.analytics and c.analytics.report
              and c.analytics.is_synthetic == synthetic
              and (start is None or utc(c.created_at).date() >= start)
              and (end is None or utc(c.created_at).date() <= end)]
    rows, categories = [], defaultdict(lambda: {"count": 0, "amount": 0.0})
    for c in claims:
        a, r = c.analytics, c.analytics.report
        pending = c.status in PENDING and r["review_recommended"]
        signals = r["signals"]
        primary = max(signals, key=lambda s: s["priority"])["category"] if signals else "Sem alerta prioritário"
        if pending:
            categories[primary]["count"] += 1
            categories[primary]["amount"] += float(c.requested_amount)
        rows.append({
            "claim_id": c.id, "provider_ref": a.provider_ref, "provider_name": c.provider_name,
            "beneficiary_ref": a.beneficiary_ref, "specialty": a.specialty, "procedure_code": a.procedure_code,
            "region": a.region, "amount": float(c.requested_amount), "status": c.status,
            "received_date": utc(c.created_at).date().isoformat(), "consultation_date": c.consultation_date.isoformat(),
            "received_at": utc(c.created_at).isoformat(),
            "priority": r["priority"], "pending_review": pending, "primary_category": primary,
            "unit_amount": r["evidence"]["unit_amount"], "peer_median": r["evidence"]["peer_median"],
            "provider_documents_30d": r["features"]["provider_documents_30d"],
            "beneficiary_documents_30d": r["features"]["beneficiary_documents_30d"],
            "annual_requested_limit_percent": r["evidence"]["annual_requested_limit_percent"],
            "report": r,
        })
    def profiles(field: str) -> list[dict[str, object]]:
        groups = defaultdict(list)
        for row in rows:
            if row[field]:
                groups[row[field]].append(row)
        return sorted([{"reference": ref, "documents": len(items),
                        "amount": round(sum(i["amount"] for i in items), 2),
                        "priority": max(i["priority"] for i in items),
                        "pending_review": sum(i["pending_review"] for i in items)}
                       for ref, items in groups.items()], key=lambda x: x["priority"], reverse=True)
    return {
        "synthetic": synthetic, "period_basis": "Data de recebimento na Erix",
        "summary": {"documents": len(rows), "providers": len({r['provider_ref'] for r in rows}),
                    "beneficiaries": len({r['beneficiary_ref'] for r in rows if r['beneficiary_ref']}),
                    "pending_review": sum(r["pending_review"] for r in rows),
                    "amount_in_review": round(sum(r["amount"] for r in rows if r["pending_review"]), 2),
                    "ml_scored": sum(r["report"]["ml"]["status"] == "available" for r in rows)},
        "categories": [{"category": k, "count": v["count"], "amount": round(v["amount"], 2)} for k, v in categories.items()],
        "providers": profiles("provider_ref"), "beneficiaries": profiles("beneficiary_ref"),
        "claims": sorted(rows, key=lambda r: r["priority"], reverse=True),
        "unavailable": ["Pagamento/bloqueio Pix", "Economia auditada", "Confirmação de fraude"],
    }
