import argparse
import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import joinedload

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models import Claim
from app.services.portfolio_training import train_portfolio


def main() -> None:
    parser = argparse.ArgumentParser(description="Treina Isolation Forest com histórico temporal da operadora.")
    parser.add_argument("--synthetic", action="store_true", help="Usar apenas pedidos marcados como sintéticos.")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.output is None:
        args.output = Path(get_settings().portfolio_demo_model_path if args.synthetic else get_settings().portfolio_model_path)
    with SessionLocal() as db:
        claims = list(db.scalars(select(Claim).options(joinedload(Claim.analytics))).all())
        try:
            result = train_portfolio(claims, args.output, args.synthetic)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
