"""Подготовка обучающего CSV из эталонного Excel организаторов (100 слабых сигналов).

Запуск:
    python -m app.classifier.prepare_data
    python -m app.classifier.prepare_data --xlsx data/100_...xlsx --out data/train_full.csv

Результат: CSV с колонками name,area,companies,reason,stage,trend,score,is_weak_signal
(все строки эталона — слабые сигналы, поэтому is_weak_signal=1; служебный «№» не переносится,
«Источники» остаются в Excel/экспорте, а не в признаках). Отрицательные примеры добавляет
обучение флагом --synthesize-negatives.
"""
import argparse

import pandas as pd

from app.classifier.exemplars import COL_TECH, load_signals_xlsx, parse_sources


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--xlsx",
                    default="data/100_слабых_технологических_сигналов_сентябрь_2026.xlsx")
    ap.add_argument("--out", default="data/train_full.csv")
    a = ap.parse_args()

    df = load_signals_xlsx(a.xlsx)          # устойчивое чтение: титульные строки пропускаются
    out = pd.DataFrame({
        "name": df[COL_TECH],
        "area": df.get("Область", ""),
        "companies": df.get("Компании", ""),
        "reason": df.get("Почему это слабый сигнал", ""),
        "stage": df.get("Стадия развития", ""),
        "trend": df.get("Тренд упоминаний", ""),
        "score": df.get("Балл (стадия+тренд)", ""),
        "is_weak_signal": 1,
    })
    out.to_csv(a.out, index=False, encoding="utf-8-sig")

    n_src = sum(len(parse_sources(v)) for v in df.get("Источники", []))
    print(f"Строк: {len(out)} → {a.out}")
    print("По стадиям:", out["stage"].value_counts().to_dict())
    print(f"Распознано ссылок источников в эталоне: {n_src}")


if __name__ == "__main__":
    main()