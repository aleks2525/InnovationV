"""Патентная активность по годам — опциональный количественный предиктор слабого сигнала.

Методология: рост числа патентных публикаций по теме опережает медийный шум и подтверждает
техническую осуществимость. Без ключа PatentsView или при любой ошибке модуль возвращает {}
и НЕ влияет на выдачу (graceful degradation).
"""
from collections import Counter

import httpx

API_URL = "https://search.patentsview.org/api/v1/patent/"


async def patent_counts_by_year(query: str, api_key: str, years: int = 6) -> dict:
    """{год: число патентов} за последние `years` лет по словам в названии патента."""
    if not api_key:
        return {}
    body = {"q": {"_text_any": {"patent_title": query}},
            "f": ["patent_date"],
            "o": {"per_page": 500}}
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(API_URL, params={"api_key": api_key}, json=body)
            r.raise_for_status()
            dates = [p.get("patent_date", "")[:4] for p in r.json().get("patents", [])]
        curve = dict(sorted(Counter(d for d in dates if d).items()))
        return dict(list(curve.items())[-years:])
    except Exception:
        return {}


def patent_predictor(curve: dict) -> str | None:
    """Формулировка предиктора для key_predictors и отчёта; None если роста нет."""
    vals = list(curve.values())
    if len(vals) < 4:
        return None
    past, recent = sum(vals[: len(vals) // 2]), sum(vals[len(vals) // 2:])
    if recent >= 5 and recent > past * 1.5:
        first, last = next(iter(curve)), next(reversed(curve))
        return f"рост патентной активности: {first}: {curve[first]} → {last}: {curve[last]}"
    return None