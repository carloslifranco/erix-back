"""Offline temporal training; runtime inference never fits on the current claim."""

from datetime import datetime, timezone
from pathlib import Path

import joblib
from sklearn.ensemble import IsolationForest

from app.models import Claim
from app.services.portfolio import FEATURES, PROCESSED, build_features, utc, vector


def train_portfolio(claims: list[Claim], output: Path, synthetic: bool) -> dict[str, object]:
    ordered = sorted([c for c in claims if c.analytics and c.status in PROCESSED
                      and c.analytics.is_synthetic == synthetic], key=lambda c: utc(c.created_at))
    samples, dates = [], []
    for index, claim in enumerate(ordered):
        features, _ = build_features(claim, ordered[:index])
        if features["peer_reference_available"]:
            samples.append(vector(features))
            dates.append(utc(claim.created_at))
    if len(samples) < 40:
        raise ValueError("Treino requer ao menos 40 pedidos com referência de preço (5 documentos de 3 outros prestadores).")
    if dates[-1] > datetime.now(timezone.utc):
        raise ValueError("Histórico contém recebimentos futuros; corrija as datas antes do treino.")
    split = int(len(samples) * 0.8)
    # Keep tied timestamps on one side of the temporal holdout.
    boundary = dates[split]
    train = [row for row, at in zip(samples, dates) if at < boundary]
    holdout = [row for row, at in zip(samples, dates) if at >= boundary]
    if len(train) < 30 or not holdout:
        raise ValueError("Histórico precisa de períodos distintos com pelo menos 30 exemplos de treino e um posterior de avaliação.")
    model = IsolationForest(n_estimators=200, contamination=0.05, random_state=42, n_jobs=1)
    model.fit(train)
    holdout_flags = model.predict(holdout)
    now = datetime.now(timezone.utc).isoformat()
    artifact = {
        "kind": "portfolio-isolation-forest-v1", "version": f"portfolio-iforest-{now}",
        "model": model, "features": FEATURES, "synthetic": synthetic,
        "trained_at": now, "training_rows": len(train), "holdout_rows": len(holdout),
        "training_end": max(at for at in dates if at < boundary).isoformat(),
        "holdout_start": boundary.isoformat(),
        "reference_scores": sorted((-model.score_samples(train)).tolist()),
        "holdout_anomaly_rate": sum(int(flag == -1) for flag in holdout_flags) / len(holdout),
        "evaluation_note": "Taxa de alertas em período posterior; sem rótulos não mede acurácia, recall ou fraude confirmada.",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".joblib.tmp")
    joblib.dump(artifact, temporary)
    temporary.replace(output)
    return {k: v for k, v in artifact.items() if k not in {"model", "reference_scores"}}
