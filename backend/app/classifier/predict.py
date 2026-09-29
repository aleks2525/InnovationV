"""Инференс обученной модели + интерпретируемость (топ-5 вкладов признаков).

Hot-reload: артефакт перечитывается при изменении mtime файла — дообучение
(retrain.py) подхватывается боевым сервисом БЕЗ рестарта.
"""
import os

import joblib
import numpy as np
import pandas as pd

from app.classifier.features import NUMERIC_NAMES, numeric_features
from app.config import settings

_artifact = None
_mtime = 0.0


def _load():
    global _artifact, _mtime
    path = f"{settings.model_dir}/weak_signal_clf.joblib"
    if not os.path.exists(path):
        return None
    m = os.path.getmtime(path)
    if _artifact is None or m != _mtime:
        _artifact, _mtime = joblib.load(path), m
    return _artifact


def predict(candidate: dict) -> tuple[float | None, list[str]]:
    """Возвращает (скоринг 0..100, объяснения) или (None, []), если модель не обучена."""
    art = _load()
    if art is None:
        return None, []
    pipe = art["pipeline"]
    pre, clf = pipe.named_steps["pre"], pipe.named_steps["clf"]

    reason = f"{candidate.get('why_weak_signal', '')} {candidate.get('description', '')}"
    nf = numeric_features(candidate.get("name", ""), reason,
                          len(candidate.get("companies") or []),
                          candidate.get("stage", ""), candidate.get("trend", ""))
    X = pd.DataFrame([{**{k: nf[k] for k in NUMERIC_NAMES},
                       "text": f"{candidate.get('name', '')}. {reason}"}])
    vec = pre.transform(X)
    vec = vec.toarray()[0] if hasattr(vec, "toarray") else np.asarray(vec)
    proba = float(clf.predict_proba(vec.reshape(1, -1))[0, 1])

    # Интерпретируемость: топ-5 признаков по |коэффициент × значение|
    coefs = clf.coef_[0]
    contrib = np.abs(coefs * vec)
    vocab = pre.named_transformers_["tfidf"].get_feature_names_out()
    names = list(NUMERIC_NAMES) + [f"слово:{w}" for w in vocab]
    top = np.argsort(contrib)[::-1][:5]
    expl = [f"{names[i]} (вклад {coefs[i] * vec[i]:+.2f})" for i in top if contrib[i] > 0]
    return proba * 100, expl