import argparse
import csv
from pathlib import Path

import joblib
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import classification_report, roc_auc_score
from sklearn.model_selection import train_test_split
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

FEATURES = ["amount_mismatch", "date_mismatch", "duplicate_document", "ocr_confidence"]


def main() -> None:
    parser = argparse.ArgumentParser(description="Treina uma baseline de apoio à triagem.")
    parser.add_argument("csv_path", type=Path)
    parser.add_argument("--output", type=Path, default=Path("models/reimbursement-risk.joblib"))
    args = parser.parse_args()

    with args.csv_path.open(newline="", encoding="utf-8") as csv_file:
        rows = list(csv.DictReader(csv_file))
    required = set(FEATURES + ["label"])
    if not rows or not required.issubset(rows[0]):
        raise SystemExit(f"CSV deve conter as colunas: {', '.join(sorted(required))}")

    labels = [int(row["label"]) for row in rows]
    if set(labels) != {0, 1}:
        raise SystemExit("O conjunto precisa conter os dois rótulos: 0=regular confirmado, 1=fraude confirmada.")
    if min(labels.count(0), labels.count(1)) < 4:
        raise SystemExit("São necessárias pelo menos quatro amostras de cada classe para separar treino e teste.")

    features = [[float(row[name]) for name in FEATURES] for row in rows]
    x_train, x_test, y_train, y_test = train_test_split(
        features, labels, test_size=0.25, random_state=42, stratify=labels
    )
    model = make_pipeline(StandardScaler(), LogisticRegression(class_weight="balanced", max_iter=1000))
    model.fit(x_train, y_train)
    predictions = model.predict(x_test)
    probabilities = model.predict_proba(x_test)[:, 1]

    print(classification_report(y_test, predictions, zero_division=0))
    if len(set(y_test)) == 2:
        print(f"ROC AUC: {roc_auc_score(y_test, probabilities):.3f}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump({"model": model, "version": "baseline-v1", "features": FEATURES}, args.output)
    print(f"Modelo salvo em {args.output}")


if __name__ == "__main__":
    main()
