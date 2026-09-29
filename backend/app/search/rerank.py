"""Реранкер «запрос ↔ текст источника» через API провайдера (локального torch на стенде нет).

Доступные модели: BAAI/bge-m3, BAAI/bge-reranker-v2-m3,
Qwen/Qwen3-Reranker-0.6B, Qwen/Qwen3-VL-Reranker-2B, Qwen/Qwen3-VL-Reranker-8B.
Wire-форматы:
  tei    — POST {base}/rerank {"query", "texts"}                      -> [{"index","score"},...]
  cohere — POST {base}/rerank {"model","query","documents","top_n"}  -> {"results":[{"index"}]}
Отключается components.reranker.enabled; без base_url пайплайн работает БЕЗ реранкера.
"""
import httpx

from app.config import settings


class ApiReranker:
    def __init__(self, cfg: dict):
        self.cfg = cfg

    def _endpoint(self) -> tuple[str, str]:
        base = (self.cfg.get("base_url") or settings.reranker_base_url or "").rstrip("/")
        key = self.cfg.get("api_key") or settings.reranker_api_key
        return base, key

    def rank(self, query: str, pairs: list[tuple[int, str]], top_k: int) -> list[int]:
        base, key = self._endpoint()
        if not base or not key:
            raise RuntimeError("Reranker: не заданы base_url/api_key")
        docs = [t for _, t in pairs]
        fmt = self.cfg.get("format") or settings.reranker_format
        model = self.cfg.get("model") or settings.reranker_model
        headers = {"Authorization": f"Bearer {key}"}
        with httpx.Client(timeout=60) as c:
            if fmt == "cohere":
                r = c.post(f"{base}/rerank", headers=headers,
                           json={"model": model, "query": query,
                                 "documents": docs, "top_n": min(top_k, len(docs))})
                r.raise_for_status()
                order = [x["index"] for x in r.json()["results"]]
            else:  # tei
                r = c.post(f"{base}/rerank", headers=headers,
                           json={"query": query, "texts": docs})
                r.raise_for_status()
                items = sorted(r.json(), key=lambda x: -x["score"])[:top_k]
                order = [x["index"] for x in items]
        return [pairs[i][0] for i in order]


def get_reranker():
    from app.llm.registry import component_cfg
    cfg = component_cfg("reranker")
    if not cfg.get("enabled", True):
        return None
    base = (cfg.get("base_url") or settings.reranker_base_url or "").strip()
    if not base:
        return None                      # graceful: пайплайн без реранкера
    return ApiReranker(cfg)