"""Обучение интерпретируемого классификатора слабых сигналов (этап 1, ТЗ 7.1).

Модель: LogisticRegression + TF-IDF — осознанный выбор: прозрачность признаков
(вклад = коэффициент × значение; для линейной модели это в точности SHAP) и работа
на стандартном ноутбуке (ТЗ 3.1).

Запуск:
    python -m app.classifier.train --data data/train_full.csv --synthesize-negatives
Формат CSV: name, area, companies, reason, stage, trend, score, is_weak_signal
"""
import argparse
import json
import os

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import classification_report
from sklearn.model_selection import StratifiedKFold, cross_validate
from sklearn.pipeline import Pipeline

from app.classifier.features import NUMERIC_NAMES, numeric_features
from app.config import settings

# Демо-негативы для локальной проверки пайплайна (зрелые технологии / стандарты).
# В боевом датасете используется скрытая разметка организаторов.
DEMO_MATURE = [
    ("Классический IAM (Okta, CyberArk, Microsoft Entra)", "ИТ-безопасность", "Okta, CyberArk, Microsoft",
     "Сформированный рынок, выраженные лидеры, устойчивое конкурентное разделение, категория в Gartner MQ",
     "Массовое внедрение", "Стабильный", 0),
    ("WAF для веб-приложений", "ИТ-безопасность", "Cloudflare, Imperva, F5",
     "Массово внедрённая технология, десятки вендоров, устоявшиеся бюджеты, отраслевой стандарт",
     "Массовое внедрение", "Стабильный", 0),
    ("Разовый red-teaming моделей перед релизом", "ИИ-безопасность", "множество консалтингов",
     "Отраслевой стандарт, закреплён в NIST AI RMF, массовая практика AI-labs",
     "Массовое внедрение", "Стабильный", 0),
    ("MLOps-мониторинг (MLflow, Weights & Biases)", "ИИ-инфраструктура", "MLflow, W&B, Neptune",
     "Сформированный рынок с лидерами и устойчивым конкурентным разделением",
     "Массовое внедрение", "Стабильный", 0),
    ("Контейнеризация (Docker, Kubernetes)", "Инфраструктура", "Docker, Red Hat, VMware",
     "Массовое внедрение, отраслевой стандарт де-факто более 10 лет",
     "Массовое внедрение", "Стабильный", 0),
    ("Облачные вычисления IaaS", "Инфраструктура", "AWS, Azure, Google Cloud",
     "Сформированный рынок, выраженные лидеры, устойчивое конкурентное разделение",
     "Массовое внедрение", "Стабильный", 0),
    ("Чат-боты поддержки клиентов", "Финтех", "множество вендоров",
     "Массово используемая технология, зрелый рынок, низкая новизна",
     "Массовое внедрение", "Стабильный", 0),
    ("Биометрический KYC по лицу", "Финтех", "IDRnD, VisionLabs",
     "Массово внедрена в банках, устойчивый рынок, стандарты регуляторов",
     "Массовое внедрение", "Стабильный", 0),
    ("5G-сети", "Телеком", "Ericsson, Nokia, Huawei",
     "Массовое развёртывание, зрелый рынок оборудования, стандарты 3GPP",
     "Массовое внедрение", "Стабильный", 0),
    ("Стейблкоины в USD (массовые)", "Финтех", "Tether, Circle",
     "Сформированный рынок с лидерами, объёмы сотни миллиардов, устойчивое разделение",
     "Массовое внедрение", "Стабильный", 0),
    ("SIEM-системы", "ИТ-безопасность", "Splunk, IBM QRadar, Exabeam",
     "Отраслевой стандарт, зрелый рынок, выраженное конкурентное разделение",
     "Массовое внедрение", "Стабильный", 0),
    ("Голосовые ассистенты", "Потребительский ИИ", "Apple, Google, Amazon, Яндекс",
     "Массово используемая технология, мейнстрим более 10 лет",
     "Массовое внедрение", "Стабильный", 0),
]


def build_X(df: pd.DataFrame) -> pd.DataFrame:
    """Прозрачные признаки: 5 числовых + текст «название + обоснование»."""
    rows = []
    for _, r in df.iterrows():
        nf = numeric_features(r["name"], r["reason"],
                              len(str(r.get("companies", "")).split(",")),
                              r.get("stage", ""), r.get("trend", ""))
        nf["text"] = f"{r['name']}. {r['reason']}"
        rows.append(nf)
    return pd.DataFrame(rows)


def _make_pipeline() -> Pipeline:
    pre = ColumnTransformer([
        ("num", "passthrough", NUMERIC_NAMES),
        ("tfidf", TfidfVectorizer(max_features=1500, ngram_range=(1, 2)), "text"),
    ])
    clf = LogisticRegression(max_iter=2000, C=1.0, class_weight="balanced")
    return Pipeline([("pre", pre), ("clf", clf)])


def fit_and_score(df: pd.DataFrame):
    """CV-метрики + обучение. Возвращает (pipe, metrics) — используется train и retrain."""
    X, y = build_X(df), df["is_weak_signal"].astype(int).values
    n_splits = max(2, min(5, int(min(y.sum(), len(y) - y.sum()))))
    pipe = _make_pipeline()
    cv = StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=42)
    res = cross_validate(pipe, X, y, cv=cv, scoring=("precision", "recall", "f1"))
    metrics = {"cv_metrics": {k: {"mean": round(float(res[f"test_{k}"].mean()), 4),
                                  "std": round(float(res[f"test_{k}"].std()), 4),
                                  "folds": [round(float(v), 4) for v in res[f"test_{k}"]]}
                              for k in ("precision", "recall", "f1")}}
    pipe.fit(X, y)
    return pipe, metrics


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--synthesize-negatives", action="store_true",
                    help="добавить демо-зрелые технологии как отрицательные примеры")
    args = ap.parse_args()

    df = pd.read_csv(args.data)
    if args.synthesize_negatives:
        demo = pd.DataFrame(DEMO_MATURE, columns=[
            "name", "area", "companies", "reason", "stage", "trend", "is_weak_signal"])
        demo["score"] = 1
        df = pd.concat([df, demo], ignore_index=True)
    if "is_weak_signal" not in df.columns:
        raise SystemExit("В CSV нет колонки is_weak_signal (0/1). См. README, раздел «Обучение».")

    pipe, metrics = fit_and_score(df)
    y = df["is_weak_signal"].astype(int).values
    print(classification_report(y, pipe.predict(build_X(df)), digits=3,
                                target_names=["не сигнал", "слабый сигнал"]))
    f1 = metrics["cv_metrics"]["f1"]["mean"]
    metrics["threshold"] = {"required_by_tz": "0.75–0.80", "achieved_f1": f1,
                            "status": "PASS" if f1 >= 0.75 else "BELOW"}
    metrics["dataset"] = {"file": args.data, "rows": int(len(df)),
                          "weak_signals": int(y.sum()), "non_signals": int(len(y) - y.sum())}
    print("CV-метрики:", json.dumps(metrics["cv_metrics"], ensure_ascii=False))

    os.makedirs(settings.model_dir, exist_ok=True)
    joblib.dump({"pipeline": pipe, "numeric_names": NUMERIC_NAMES},
                f"{settings.model_dir}/weak_signal_clf.joblib")
    with open(f"{settings.model_dir}/metrics.json", "w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=2, ensure_ascii=False)
    print(f"Модель и отчёт сохранены в {settings.model_dir}/")


if __name__ == "__main__":
    main()