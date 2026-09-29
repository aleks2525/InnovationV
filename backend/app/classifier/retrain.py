"""Дообучение классификатора на экспертной разметке (замыкание петли human-in-the-loop).

Петля: 👍/ в UI → expert_feedback → склейка с базовым датасетом (метка эксперта
приоритетнее вердикта системы) → версия v<N> + registry.json → predict.py подхватывает
активный артефакт по mtime БЕЗ рестарта → следующие прогоны учитывают expert_prior.

Запуск:
    python -m app.classifier.retrain                 # дообучить на накопленных метках
    python -m app.classifier.retrain --min-rows 20   # порог накопления новых меток
    python -m app.classifier.retrain --rollback 3    # откат активной модели к v3
"""
import argparse
import asyncio
import json
import os
from datetime import datetime, timezone

import joblib
import pandas as pd
from sqlalchemy import select

from app.classifier.features import NUMERIC_NAMES
from app.classifier.train import fit_and_score
from app.config import settings
from app.db import Candidate, ExpertFeedback, SessionLocal


def fetch_feedback_rows() -> list[dict]:
    """Экспертная разметка из БД → строки обучающего формата."""
    async def _q():
        async with SessionLocal() as s:
            return (await s.execute(
                select(Candidate, ExpertFeedback)
                .join(ExpertFeedback, ExpertFeedback.candidate_id == Candidate.id)
                .order_by(ExpertFeedback.id))).all()

    out = []
    for c, f in asyncio.run(_q()):
        system_weak = c.verdict == "weak_signal"
        out.append({"name": c.name_ru or c.name, "area": c.area or "",
                    "companies": ", ".join(c.companies or []),
                    "reason": c.why_weak_signal or c.why_not_mature or c.description or "",
                    "stage": c.stage or "", "trend": c.trend or "", "score": "",
                    "is_weak_signal": int(system_weak if f.agree else not system_weak)})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="data/train_full.csv")
    ap.add_argument("--min-rows", type=int, default=10)
    ap.add_argument("--rollback", type=int, default=0)
    a = ap.parse_args()

    reg_path = f"{settings.model_dir}/registry.json"
    reg = (json.load(open(reg_path, encoding="utf-8")) if os.path.exists(reg_path)
           else {"versions": [], "current": None})

    if a.rollback:
        src = f"{settings.model_dir}/weak_signal_clf_v{a.rollback}.joblib"
        if not os.path.exists(src):
            raise SystemExit(f"Версия v{a.rollback} не найдена")
        joblib.dump(joblib.load(src), f"{settings.model_dir}/weak_signal_clf.joblib")
        reg["current"] = a.rollback
        json.dump(reg, open(reg_path, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
        print(f"Активная модель откатана к v{a.rollback}")
        return

    fb = fetch_feedback_rows()
    if len(fb) < a.min_rows:
        raise SystemExit(f"Накоплено {len(fb)} экспертных меток (< {a.min_rows}) — "
                         f"дообучение отложено")

    df = pd.concat([pd.read_csv(a.base), pd.DataFrame(fb)], ignore_index=True)
    df = df.drop_duplicates(subset=["name"], keep="last")   # эксперт приоритетнее системы

    pipe, metrics = fit_and_score(df)
    v = len(reg["versions"]) + 1
    payload = {"pipeline": pipe, "numeric_names": NUMERIC_NAMES}
    joblib.dump(payload, f"{settings.model_dir}/weak_signal_clf_v{v}.joblib")
    joblib.dump(payload, f"{settings.model_dir}/weak_signal_clf.joblib")   # активная копия
    json.dump(metrics, open(f"{settings.model_dir}/metrics_v{v}.json", "w",
                            encoding="utf-8"), indent=2, ensure_ascii=False)

    reg["versions"].append({"v": v, "trained_at": datetime.now(timezone.utc).isoformat(),
                            "rows_total": int(len(df)), "rows_expert": len(fb),
                            "cv_f1": metrics["cv_metrics"]["f1"]["mean"],
                            "artifact": f"weak_signal_clf_v{v}.joblib"})
    reg["current"] = v
    json.dump(reg, open(reg_path, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    print(f"Дообучено: v{v}; строк {len(df)} (экспертных {len(fb)}); "
          f"CV F1 {metrics['cv_metrics']['f1']['mean']:.3f}")


if __name__ == "__main__":
    main()