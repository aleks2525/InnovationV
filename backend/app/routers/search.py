"""Эндпоинты поиска и выдачи: прогон, SSE-стриминг, A/B-абляция, отчёты, экспорт, логи."""
import asyncio
import json
from datetime import datetime, timezone

import pandas as pd
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response, StreamingResponse
from io import BytesIO
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.classifier.features import ball
from app.db import Candidate, LLMCall, SearchQuery, SessionLocal, Source
from app.llm.registry import MODEL_BY_TASK
from app.pipeline import run_pipeline
from app.schemas import (ABRequest, CandidateOut, QueryReportOut, SearchRequest,
                         SearchResponse, SourceFullOut, SourceOut, StatsOut, TimingOut)

router = APIRouter(prefix="/api", tags=["weak-signals"])

METHODOLOGY = {
    "weak_signal_criteria": [
        "ранняя стадия развития (концепция/исследование/прототип/пилот/раннее внедрение)",
        "малое число игроков и микро-раунды финансирования",
        "скачкообразный рост упоминаний и раундов при низкой базе",
        "отсутствие аналитической категории (напр. в Gartner) и бюджетных строк у заказчиков",
        "подтверждение несколькими независимыми источниками",
    ],
    "exclusion_criteria": [
        "массовое внедрение", "сформированный рынок с выраженными лидерами",
        "устойчивое конкурентное разделение", "отраслевой стандарт",
        "маркетинговый хайп и информационный шум без независимых подтверждений",
    ],
    "scoring": ("final_score = веса из конфигурации: классификатор + LLM-аудитор + "
                "судья + доверие источников; при отключении компонента веса перенормируются"),
    "source_policy": ("соцсети, блоги, агрегаторы, пресс-релизы — только первичный индикатор: "
                      "независимое подтверждение либо отметка о пониженной доверенности (ТЗ, разд. 2)"),
}


def _src_out(s: Source) -> SourceOut:
    return SourceOut(url=s.url, title=s.title, published_date=s.published_date,
                     source_type=s.source_type, language=s.language or "?",
                     trust_level=s.trust_level)


def _cand_out(c: Candidate) -> CandidateOut:
    return CandidateOut(id=c.id, name=c.name, name_ru=c.name_ru, description=c.description,
                        companies=c.companies or [], area=c.area, stage=c.stage, trend=c.trend,
                        verdict=c.verdict, final_score=c.final_score, clf_score=c.clf_score,
                        llm_score=c.llm_score, judge_score=c.judge_score,
                        judge_verdict=c.judge_verdict, experts_flag=c.experts_flag,
                        trust_score=c.trust_score, analog=c.analog,
                        why_weak_signal=c.why_weak_signal, why_not_mature=c.why_not_mature,
                        advantage=c.advantage, use_case=c.use_case,
                        key_predictors=c.key_predictors or [],
                        sources=[_src_out(s) for s in c.sources_rel])


@router.post("/search", response_model=SearchResponse)
async def api_search(req: SearchRequest):
    if len(req.query.strip()) < 3:
        raise HTTPException(400, "Слишком короткий запрос")
    return await run_pipeline(req.query.strip())


@router.get("/search/stream")
async def search_stream(q: str):
    """SSE: события stage / stats / candidate / done / error — работа сервиса видна вживую."""
    queue: asyncio.Queue = asyncio.Queue()

    async def emit(type_, **payload):
        await queue.put({"type": type_, **payload})

    async def worker():
        try:
            result = await run_pipeline(q.strip(), emit=emit)
            await emit("done", result=result)
        except Exception as e:
            await emit("error", message=str(e)[:300])

    task = asyncio.create_task(worker())

    async def gen():
        try:
            while True:
                ev = await queue.get()
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
                if ev["type"] in ("done", "error"):
                    break
        finally:
            if not task.done():
                task.cancel()

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/search/ab")
async def search_ab(req: ABRequest):
    """Два прогона одной темы разными конфигурациями + diff выдачи (живая абляция)."""
    a = await run_pipeline(req.query, cfg_override=req.config_a)
    b = await run_pipeline(req.query, cfg_override=req.config_b)
    na = {c["name_ru"] or c["name"] for c in a["top"]}
    nb = {c["name_ru"] or c["name"] for c in b["top"]}
    return {"a": a, "b": b,
            "diff": {"only_a": sorted(na - nb), "only_b": sorted(nb - na),
                     "overlap": len(na & nb)}}


@router.get("/queries")
async def list_queries():
    async with SessionLocal() as db:
        rows = (await db.scalars(select(SearchQuery)
                .order_by(SearchQuery.id.desc()).limit(20))).all()
        return [{"id": r.id, "text": r.text, "status": r.status, "stats": r.stats,
                 "created_at": str(r.created_at)} for r in rows]


@router.get("/queries/{qid}/report", response_model=QueryReportOut)
async def query_report(qid: int):
    """Сводный протокол прогона: артефакт для жюри и промежуточной/финальной сдачи."""
    async with SessionLocal() as db:
        q = await db.get(SearchQuery, qid)
        if q is None:
            raise HTTPException(404, f"Запрос #{qid} не найден")
        sources = (await db.scalars(select(Source).where(Source.query_id == qid)
                   .order_by(Source.id))).all()
        cands = (await db.scalars(select(Candidate)
                 .options(selectinload(Candidate.sources_rel))
                 .where(Candidate.query_id == qid)
                 .order_by(Candidate.final_score.desc()))).all()
        calls = (await db.scalars(select(LLMCall).where(LLMCall.query_id == qid)
                 .order_by(LLMCall.id))).all()

    stats = q.stats or {}
    duration = (round((q.finished_at - q.created_at).total_seconds(), 1)
                if q.finished_at else None)
    usage: dict = {}
    for c in calls:
        u = usage.setdefault((c.model, c.provider),
                             {"calls": 0, "errors": 0, "lat": 0.0, "tasks": set()})
        u["calls"] += 1
        u["errors"] += (c.status != "ok")
        u["lat"] += c.latency_ms
        u["tasks"].add(c.task)

    weak = [c for c in cands if c.verdict == "weak_signal"]
    return QueryReportOut(
        query_id=q.id, query=q.text, status=q.status,
        timing=TimingOut(created_at=str(q.created_at),
                         finished_at=str(q.finished_at) if q.finished_at else None,
                         duration_sec=duration),
        stats=StatsOut(processed_sources=stats.get("processed_sources", len(sources)),
                       candidates=stats.get("candidates", len(cands)),
                       weak_signals_gt75=stats.get("weak_signals_gt75", 0),
                       excluded=stats.get("excluded", 0),
                       borderline=stats.get("borderline", 0)),
        methodology=METHODOLOGY,
        top=[_cand_out(c) for c in weak[:15]],
        excluded=[_cand_out(c) for c in cands if c.verdict != "weak_signal"],
        sources=[SourceFullOut(id=s.id, url=s.url, title=s.title,
                               published_date=s.published_date, source_type=s.source_type,
                               language=s.language or "?", trust_level=s.trust_level,
                               fetched=s.fetched, content_chars=len(s.content or ""))
                 for s in sources],
        model_usage=[{"model": m, "provider": p, "calls": u["calls"], "errors": u["errors"],
                      "avg_latency_ms": round(u["lat"] / u["calls"], 1),
                      "total_latency_ms": round(u["lat"], 1), "tasks": sorted(u["tasks"])}
                     for (m, p), u in usage.items()],
        model_disclosure=MODEL_BY_TASK,
        generated_at=datetime.now(timezone.utc).isoformat())


@router.get("/queries/{qid}/export.xlsx")
async def export_xlsx(qid: int):
    """Экспорт в формате образца экспертов: 9 колонок + лист «Сомнительные (5-7)»."""
    async with SessionLocal() as db:
        top = (await db.scalars(select(Candidate).options(selectinload(Candidate.sources_rel))
               .where(Candidate.query_id == qid, Candidate.verdict == "weak_signal")
               .order_by(Candidate.final_score.desc()).limit(15))).all()
        bord = (await db.scalars(select(Candidate)
                .where(Candidate.query_id == qid, Candidate.verdict == "weak_signal")
                .order_by(Candidate.final_score.desc()).offset(15).limit(7))).all()

    rows = []
    for i, c in enumerate(top, 1):
        rows.append([i, f"{c.name_ru} ({c.name})", c.area, ", ".join(c.companies or []),
                     c.why_weak_signal, c.stage, c.trend, ball(c.stage, c.trend),
                     "\n".join(f"{s.title} — {s.url} ({s.published_date or 'б/д'}; "
                               f"{s.source_type}; доверенность: {s.trust_level})"
                               for s in c.sources_rel)])
    df = pd.DataFrame(rows, columns=["№", "Технология (слабый сигнал)", "Область", "Компании",
                                     "Почему это слабый сигнал", "Стадия развития",
                                     "Тренд упоминаний", "Балл (стадия+тренд)", "Источники"])
    dfb = pd.DataFrame([[i, b.name_ru or b.name, b.final_score]
                        for i, b in enumerate(bord, 1)],
                       columns=["№", "Технология (сомнительный кандидат)", "Скоринг"])
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        df.to_excel(w, index=False, sheet_name="Слабые сигналы ТОП-15")
        dfb.to_excel(w, index=False, sheet_name="Сомнительные (5-7)")
    return Response(buf.getvalue(),
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition":
                             f'attachment; filename="weak_signals_q{qid}.xlsx"'})


@router.get("/llm-calls")
async def llm_calls(limit: int = 50):
    """Лог вызовов моделей — обязательное требование ТЗ 3.1 (явное логирование)."""
    async with SessionLocal() as db:
        rows = (await db.scalars(select(LLMCall).order_by(LLMCall.id.desc())
                .limit(limit))).all()
        return [{"request_id": r.request_id, "query_id": r.query_id, "task": r.task,
                 "model": r.model, "provider": r.provider,
                 "latency_ms": round(r.latency_ms), "status": r.status,
                 "created_at": str(r.created_at)} for r in rows]


@router.get("/models")
async def models():
    """Раскрытие формата выбора модели для конкретного ответа (ТЗ 3.1)."""
    return MODEL_BY_TASK