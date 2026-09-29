"""Эталонная методология из датасета организаторов (100 слабых сигналов, сентябрь 2026).

Колонка «Почему это слабый сигнал» работает тремя способами:
1) few-shot-примеры в промпте анализа (LLM обосновывает в стиле методологов ГПБ);
2) kNN-аналог: ближайшее экспертное обоснование (TF-IDF cosine — легко, без GPU);
3) опционально: fine-tune ruBERT на текстах обоснований (train_nn.py).
"""
import re
from functools import lru_cache

import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

COL_NUM = "№"
COL_TECH = "Технология (слабый сигнал)"
COL_WHY = "Почему это слабый сигнал"


def load_signals_xlsx(path: str) -> pd.DataFrame:
    """Устойчивое чтение: пропускает титульные строки, находит шапку по колонке «Технология»."""
    raw = pd.read_excel(path, header=None)
    hdr = next(i for i in range(len(raw))
               if raw.iloc[i].astype(str).str.contains("Технология", na=False).any())
    df = raw.iloc[hdr + 1:].copy()
    df.columns = [str(c).strip() for c in raw.iloc[hdr]]
    df = df.dropna(subset=[COL_TECH])
    df[COL_TECH] = df[COL_TECH].astype(str).str.strip()
    return df[df[COL_TECH] != ""].reset_index(drop=True)


def parse_sources(md) -> list[tuple[str, str]]:
    """Колонка «Источники» в формате [name](url) → пары (наименование, ссылка)."""
    return re.findall(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", str(md))


class ExemplarBank:
    def __init__(self, path: str):
        self.df = load_signals_xlsx(path)
        self.vec = TfidfVectorizer(max_features=3000, ngram_range=(1, 2))
        self.M = self.vec.fit_transform(self.df[COL_WHY].fillna(""))

    def analogs(self, text: str, k: int = 2) -> list[tuple[dict, float]]:
        sim = cosine_similarity(self.vec.transform([text]), self.M)[0]
        idx = sim.argsort()[::-1][:k]
        return [(self.df.iloc[i].to_dict(), round(float(sim[i]), 2))
                for i in idx if sim[i] > 0.15]

    def few_shot(self, text: str, k: int = 2) -> str:
        return "\n".join(f"- {r[COL_TECH]}: {r[COL_WHY]}" for r, _ in self.analogs(text, k)) \
               or "- (прямых аналогов не найдено)"


@lru_cache(maxsize=1)
def bank() -> "ExemplarBank | None":
    """None при отсутствии файла: пайплайн деградирует gracefully (без аналогов/few-shot)."""
    from app.config import settings
    try:
        return ExemplarBank(settings.exemplars_path)
    except FileNotFoundError:
        return None
    except Exception:
        return None