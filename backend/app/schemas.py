"""Pydantic-схемы ответов API (Swagger /docs генерируется из них)."""
from typing import List, Optional

from pydantic import BaseModel


class SearchRequest(BaseModel):
    query: str


class SourceOut(BaseModel):
    url: str
    title: str
    published_date: Optional[str] = None
    source_type: str
    language: str
    trust_level: str


class CandidateOut(BaseModel):
    id: Optional[int] = None                 # ← нужен UI для кнопок 👍/👎 (feedback)
    name: str
    name_ru: str = ""
    description: str = ""
    companies: List[str] = []
    area: str = ""
    stage: str = ""
    trend: str = ""
    verdict: str = ""
    final_score: float = 0.0
    clf_score: Optional[float] = None
    llm_score: Optional[float] = None
    judge_score: Optional[float] = None
    judge_verdict: Optional[str] = None
    experts_flag: bool = False
    trust_score: float = 0.0
    analog: str = ""
    why_weak_signal: str = ""
    why_not_mature: str = ""
    advantage: str = ""
    use_case: str = ""
    key_predictors: List[str] = []
    sources: List[SourceOut] = []


class BorderlineOut(BaseModel):
    id: Optional[int] = None
    name: str
    name_ru: str = ""
    final_score: float


class StatsOut(BaseModel):
    processed_sources: int
    candidates: int
    weak_signals_gt75: int
    excluded: int
    borderline: int = 0


class SearchResponse(BaseModel):
    query_id: int
    query: str
    stats: StatsOut
    top: List[CandidateOut]                  # всегда 15 (гарантия выдачи)
    borderline: List[BorderlineOut]          # 5–7 сомнительных, без детализации
    excluded: List[CandidateOut]
    model_disclosure: dict                   # раскрытие «задача → модель» (ТЗ 3.1)


# ---------- Сводный отчёт о прогоне ----------
class SourceFullOut(SourceOut):
    id: int
    fetched: bool = False
    content_chars: int = 0


class ModelUsageOut(BaseModel):
    model: str
    provider: str
    calls: int
    errors: int
    avg_latency_ms: float
    total_latency_ms: float
    tasks: List[str]


class TimingOut(BaseModel):
    created_at: str
    finished_at: Optional[str] = None
    duration_sec: Optional[float] = None


class QueryReportOut(BaseModel):
    query_id: int
    query: str
    status: str
    timing: TimingOut
    stats: StatsOut
    methodology: dict
    top: List[CandidateOut]
    excluded: List[CandidateOut]
    sources: List[SourceFullOut]
    model_usage: List[ModelUsageOut]
    model_disclosure: dict
    generated_at: str


class ABRequest(BaseModel):
    query: str
    config_a: Optional[dict] = None          # оверрайд рантайм-конфига
    config_b: Optional[dict] = None