"""Векторный индекс: дедупликация кандидатов и семантический поиск по источникам.

НУЖЕН ЛИ pgvector?
- По умолчанию НЕТ: InMemoryIndex (numpy-cosine) покрывает масштабы кейса
  (сотни–тысячи векторов); векторы пересчитываются при старте, персист не нужен.
- ДА, когда корпус эмбеддингов вырастет до сотен тысяч строк, нужен семантический
  поиск по всей истории в SQL или общий индекс для нескольких воркеров:
  USE_PGVECTOR=true + CREATE EXTENSION vector (schema.sql, блок 11).

Эмбеддинги: через API провайдера (BAAI/bge-m3 и др.; OpenAI-форма /embeddings
или TEI /embed). Не настроены — TF-IDF-фолбэк без дополнительных зависимостей
(локального torch/transformers на стенде нет).
"""
from __future__ import annotations

import httpx
import numpy as np

from app.config import settings


def _norm(v) -> np.ndarray:
    v = np.asarray(v, dtype="float32")
    return v / (np.linalg.norm(v) + 1e-9)


class InMemoryIndex:
    """Дефолт: векторы в RAM, косинус через numpy."""

    def __init__(self):
        self._V: list[np.ndarray] = []
        self._meta: list[dict] = []

    def add(self, meta: dict, vec):
        self._V.append(_norm(vec))
        self._meta.append(meta)

    def query(self, vec, k: int = 5) -> list[tuple[dict, float]]:
        if not self._V:
            return []
        sim = np.vstack(self._V) @ _norm(vec)
        idx = np.argsort(sim)[::-1][:k]
        return [(self._meta[i], float(sim[i])) for i in idx]

    def near_dup(self, vec, threshold: float = 0.92):
        r = self.query(vec, 1)
        return r[0][0] if r and r[0][1] >= threshold else None


class PgVectorIndex:
    """Опция: векторы в PostgreSQL (pgvector, HNSW-индекс, оператор <=>)."""

    def __init__(self, session_factory, dim: int, table: str = "source_embeddings"):
        self.sf, self.dim, self.table = session_factory, dim, table

    @staticmethod
    def _lit(vec) -> str:
        return "[" + ",".join(f"{x:.6f}" for x in np.asarray(vec, dtype="float32")) + "]"

    async def ensure(self):
        from sqlalchemy import text
        async with self.sf() as s:
            await s.execute(text(f"""
                CREATE TABLE IF NOT EXISTS {self.table} (
                    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                    source_id BIGINT REFERENCES sources(id) ON DELETE CASCADE,
                    embedding vector({self.dim}) NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now())"""))
            await s.execute(text(f"CREATE INDEX IF NOT EXISTS ix_{self.table}_hnsw "
                                 f"ON {self.table} USING hnsw (embedding vector_cosine_ops)"))
            await s.commit()

    async def add(self, source_id: int, vec):
        from sqlalchemy import text
        async with self.sf() as s:
            await s.execute(
                text(f"INSERT INTO {self.table} (source_id, embedding) VALUES (:i, :v)"),
                {"i": source_id, "v": self._lit(vec)})
            await s.commit()

    async def query(self, vec, k: int = 5) -> list[tuple[int, float]]:
        from sqlalchemy import text
        async with self.sf() as s:
            r = await s.execute(text(f"""
                SELECT source_id, 1 - (embedding <=> :v::vector) AS sim
                FROM {self.table}
                ORDER BY embedding <=> :v::vector LIMIT :k"""),
                {"v": self._lit(vec), "k": k})
            return [(row.source_id, row.sim) for row in r]


class ApiEmbedder:
    """Эмбеддинги через API провайдера: OpenAI-форма /embeddings, фолбэк TEI /embed."""

    def __init__(self, base: str, key: str, model: str):
        self.base, self.key, self.model = base.rstrip("/"), key, model

    def __call__(self, texts) -> np.ndarray:
        texts = list(texts)
        headers = {"Authorization": f"Bearer {self.key}"}
        with httpx.Client(timeout=120) as c:
            r = c.post(f"{self.base}/embeddings", headers=headers,
                       json={"model": self.model, "input": texts})
            if r.status_code == 404:
                r = c.post(f"{self.base}/embed", headers=headers, json={"inputs": texts})
            r.raise_for_status()
            data = r.json()
            if isinstance(data, dict):
                data = sorted(data["data"], key=lambda x: x.get("index", 0))
                return np.asarray([d["embedding"] for d in data], dtype="float32")
            return np.asarray(data, dtype="float32")


def make_embedder(corpus: list[str]):
    """API-эмбеддер (bge-m3 и т.п.), если настроен; иначе TF-IDF-фолбэк без зависимостей."""
    if settings.embeddings_base_url and settings.embeddings_api_key:
        return ApiEmbedder(settings.embeddings_base_url,
                           settings.embeddings_api_key, settings.embeddings_model)
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.preprocessing import normalize
    v = TfidfVectorizer(max_features=2000, ngram_range=(1, 2)).fit(corpus or ["слабый сигнал"])
    return lambda texts: normalize(v.transform(texts)).toarray()


def get_index(session_factory=None, use_pgvector: bool | None = None, dim: int = 384):
    flag = settings.use_pgvector if use_pgvector is None else use_pgvector
    return PgVectorIndex(session_factory, dim) if flag else InMemoryIndex()