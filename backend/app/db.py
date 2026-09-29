"""Модели SQLAlchemy (PostgreSQL). Схема-источник: backend/schema.sql.
create_all в main.py идемпотентен: существующие таблицы не трогаются.
"""
from datetime import datetime
from typing import List, Optional

from sqlalchemy import (Boolean, Column, DateTime, Float, ForeignKey, String,
                        Table, Text)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.config import settings

engine = create_async_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


candidate_sources = Table(
    "candidate_sources",
    Base.metadata,
    Column("candidate_id", ForeignKey("candidates.id", ondelete="CASCADE"), primary_key=True),
    Column("source_id", ForeignKey("sources.id", ondelete="CASCADE"), primary_key=True),
)


class SearchQuery(Base):
    """Открытый запрос пользователя и итоги прогона."""
    __tablename__ = "queries"

    id: Mapped[int] = mapped_column(primary_key=True)
    text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="running")   # running|done|error
    stats: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    sources: Mapped[List["Source"]] = relationship(back_populates="query",
                                                   cascade="all, delete-orphan")
    candidates: Mapped[List["Candidate"]] = relationship(back_populates="query",
                                                         cascade="all, delete-orphan")


class Source(Base):
    """Найденный источник: все обязательные атрибуты ТЗ (наименование, ссылка,
    дата, тип, язык, доверенность)."""
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    query_id: Mapped[int] = mapped_column(ForeignKey("queries.id", ondelete="CASCADE"), index=True)
    url: Mapped[str] = mapped_column(String(1024))
    url_hash: Mapped[str] = mapped_column(String(64), index=True)        # sha256(url)
    title: Mapped[str] = mapped_column(Text, default="")
    snippet: Mapped[str] = mapped_column(Text, default="")
    content: Mapped[str] = mapped_column(Text, default="")
    published_date: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    language: Mapped[str] = mapped_column(String(8), default="")
    source_type: Mapped[str] = mapped_column(String(128), default="")
    trust_level: Mapped[str] = mapped_column(String(160), default="средняя")
    fetched: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    query: Mapped["SearchQuery"] = relationship(back_populates="sources")


class Candidate(Base):
    """Кандидат в слабые сигналы: вердикт, скоринг, интерпретация, источники."""
    __tablename__ = "candidates"

    id: Mapped[int] = mapped_column(primary_key=True)
    query_id: Mapped[int] = mapped_column(ForeignKey("queries.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(Text)                              # оригинал
    name_ru: Mapped[str] = mapped_column(Text, default="")               # перевод/транслитерация
    description: Mapped[str] = mapped_column(Text, default="")
    companies: Mapped[list] = mapped_column(JSONB, default=list)
    area: Mapped[str] = mapped_column(String(128), default="")
    stage: Mapped[str] = mapped_column(String(128), default="")
    trend: Mapped[str] = mapped_column(String(160), default="")
    verdict: Mapped[str] = mapped_column(String(20), default="")         # weak_signal|mature|noise
    final_score: Mapped[float] = mapped_column(Float, default=0.0)
    clf_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    llm_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    judge_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    judge_verdict: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    experts_flag: Mapped[bool] = mapped_column(Boolean, default=False)   # конфликт оценок
    trust_score: Mapped[float] = mapped_column(Float, default=0.0)       # надёжность источников 0–100
    patent_curve: Mapped[dict] = mapped_column(JSONB, default=dict)      # опция: патенты по годам
    analog: Mapped[str] = mapped_column(Text, default="")                # эталонный аналог (kNN)
    why_weak_signal: Mapped[str] = mapped_column(Text, default="")
    why_not_mature: Mapped[str] = mapped_column(Text, default="")
    advantage: Mapped[str] = mapped_column(Text, default="")
    use_case: Mapped[str] = mapped_column(Text, default="")
    key_predictors: Mapped[list] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    query: Mapped["SearchQuery"] = relationship(back_populates="candidates")
    sources_rel: Mapped[List["Source"]] = relationship(secondary=candidate_sources)


class LLMCall(Base):
    """Явное логирование каждого вызова модели (ТЗ 3.1)."""
    __tablename__ = "llm_calls"

    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[str] = mapped_column(String(64), index=True)
    query_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("queries.id", ondelete="SET NULL"), nullable=True)
    task: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(64))
    provider: Mapped[str] = mapped_column(String(32))
    prompt: Mapped[str] = mapped_column(Text, default="")
    response: Mapped[str] = mapped_column(Text, default="")
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(16), default="ok")        # ok|error
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AppSetting(Base):
    """Рантайм-конфигурация пульта (admin.html) + аудит-записи 'audit::<ts>'."""
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[dict] = mapped_column(JSONB, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_by: Mapped[str] = mapped_column(String(64), default="admin")


class ExpertFeedback(Base):
    """Human-in-the-loop: метка эксперта по вердикту системы → дообучение."""
    __tablename__ = "expert_feedback"

    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int] = mapped_column(
        ForeignKey("candidates.id", ondelete="CASCADE"), index=True)
    agree: Mapped[bool] = mapped_column(Boolean, default=True)
    comment: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)