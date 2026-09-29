"""Признаки классификатора слабых сигналов (этап 1, ТЗ 7.1/8.3).

Обоснование отбора: каждый признак выведен из определения слабого сигнала в ТЗ
и лексики разметки методологов ГПБ (колонки датасета «Почему это слабый сигнал»,
«Стадия развития», «Тренд упоминаний», «Компании») — не произвольный feature engineering.
Интерпретируемость: линейная модель → вклад признака = коэффициент × значение
(для линейных моделей это в точности SHAP-значения).
"""
import re

# --- Стадия развития: ML-шкала (0–5) ---
STAGE_SCORES = [("концепция", 0), ("исследование", 0), ("прототип", 1),
                ("пилот", 2), ("раннее внедрение", 3), ("массовое внедрение", 5)]

# --- Шкала «Балл (стадия+тренд)» из образца экспертов (1–5 + 0–3) для export.xlsx ---
BALL_STAGE = [("концепция", 1), ("исследование", 1), ("прототип", 2),
              ("пилот", 3), ("раннее", 4), ("массовое", 5)]
BALL_TREND = [("быстро", 3), ("раст", 2), ("стабильн", 1), ("падает", 0)]

# Лексика слабых сигналов: стелс-выходы, ранние раунды, нишевость, дефицит обсуждения
WEAK_RE = re.compile(
    r"stealth|стелс|seed|пре-?посевн|посевн|пилот|почти не|единиц|ниша|раунд|микро-?раунд|"
    r"нет категории|не обсуждается|почти нет|ранние раунды|вышли из|впервые|первый",
    re.I)
# Лексика зрелых трендов и шума: критерии исключения ТЗ
MATURE_RE = re.compile(
    r"мейнстрим|отраслевой стандарт|массово|лидер|сформированный рынок|широко используется|"
    r"gartner|гатнер|массовое внедрение|устойчивое конкурентное|десятки вендоров",
    re.I)

NUMERIC_NAMES = ["stage", "trend", "n_companies", "kw_weak", "kw_mature"]


def stage_score(stage: str) -> int:
    s = (stage or "").lower()
    score = 2
    for key, val in STAGE_SCORES:
        if key in s:
            score = val
    return score


def trend_score(trend: str) -> int:
    t = (trend or "").lower()
    if "быстро" in t:
        return 3
    if "раст" in t:
        return 2
    if "стабильн" in t:
        return 1
    if "падает" in t:
        return 0
    return 1


def numeric_features(name: str, reason: str, companies_n: int,
                     stage: str, trend: str) -> dict:
    """Пять прозрачных числовых признаков наблюдения."""
    return {
        "stage": stage_score(stage),
        "trend": trend_score(trend),
        "n_companies": int(companies_n or 0),
        "kw_weak": len(WEAK_RE.findall(reason or "")),
        "kw_mature": len(MATURE_RE.findall(reason or "")),
    }


def ball(stage: str, trend: str) -> int:
    """«Балл (стадия+тренд)» как в образце экспертов: используется в export.xlsx."""
    s = (stage or "").lower()
    t = (trend or "").lower()
    b_s = next((v for k, v in BALL_STAGE if k in s), 2)
    b_t = next((v for k, v in BALL_TREND if k in t), 1)
    return b_s + b_t