"""Оркестрация этапа 2: открытый поиск → ТОП-15 слабых сигналов.

Гарантии выдачи (уточнения экспертов):
- ТОП-15 всегда 15 позиций (до 3 раундов углубления поиска);
- позиция без ссылки на источник в выдачу не попадает;
- источники с единственным «пониженным» уровнем не входят в ТОП (eligible);
- 5–7 сомнительных кандидатов ниже порога — отдельно, без детализации;
- скоринг: веса из рантайм-конфига + доверие источников + expert_prior (human-in-the-loop).

Все LLM-вызовы идут через app.llm.registry.run_llm(), который логирует каждый запрос
в таблицу llm_calls и использует детерминированную таблицу «задача → модель» (ТЗ 3.1).
"""
import asyncio
import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from urllib.parse import quote

import httpx
from sqlalchemy import select

from app.classifier.exemplars import bank
from app.classifier.predict import predict as clf_predict
from app.config import settings
from app.core.runtime_config import (component_cfg, component_enabled,
                                      get_runtime_config)
from app.db import (Candidate, ExpertFeedback, SearchQuery, SessionLocal, Source)
from app.llm.registry import parse_json, run_llm
from app.search.fetch import fetch_text, url_hash
from app.search.patents import patent_counts_by_year, patent_predictor
from app.search.rerank import get_reranker
from app.search.trust import classify_type, detect_language, trust_level
from app.search.web_search import web_search

MAX_ROUNDS = 3
MIN_TOP = 15

SYSTEM_JSON = "Ты возвращаешь ТОЛЬКО валидный JSON без пояснений и без ```-обёрток."

TRUST_NUM = lambda level: (1.0 if level.startswith("высокая")
                           else (0.3 if level.startswith("пониженная") else 0.6))


def translit_name(name: str) -> str:
    """Простая транслитерация: если имя уже на кириллице — возвращаем как есть,
    иначе сохраняем оригинал. Полноценный перевод в name_ru делает LLM в промпте анализа."""
    if not name:
        return ""
    if any("\u0400" <= ch <= "\u04FF" for ch in name):
        return name
    return name


EXPANSION_PROMPT = """Сгенерируй 5 поисковых подзапросов (2 на русском, 3 на английском)
для поиска ЗАРОЖДАЮЩИХСЯ технологий (слабых сигналов) по теме: «{query}».
Верни строго JSON-массив строк."""

EXTRACT_PROMPT = """Из текста найди ЗАРОЖДАЮЩИЕСЯ технологии (слабые сигналы): ранние стадии,
мало игроков, недавние раунды финансирования, новые категории без устоявшихся лидеров.
НЕ включай массово внедрённые технологии, отраслевые стандарты и зрелые рынки.
Верни JSON-массив (до 5 объектов):
[{{"name": "...", "description": "...", "companies": ["..."],
   "stage": "Концепция/Исследование | Прототип/PoC | Пилот | Раннее внедрение",
   "trend": "растёт быстро | растёт | стабильный",
   "evidence": "короткая цитата-подтверждение"}}]
Если слабых сигналов нет — верни [].
Текст: {text}"""

ANALYSIS_PROMPT = """Ты — старший технологический аналитик Газпромбанка. Оцени кандидата.
КРИТЕРИИ СЛАБОГО СИГНАЛА: ранняя стадия (исследование/прототип/пилот/раннее внедрение),
мало игроков, скачкообразный рост раундов/упоминаний, отсутствие аналитической категории
и бюджетных строк у заказчиков, независимые подтверждения в источниках.
ИСКЛЮЧИ (verdict mature/noise): массовое внедрение, сформированный рынок, выраженные лидеры,
устойчивое конкурентное разделение, отраслевой стандарт, хайп без независимых источников.
ЭТАЛОННЫЕ ОБОСНОВАНИЯ методологов Газпромбанка (обосновывай в том же стиле и критериях):
{exemplars}
ПАТЕНТНАЯ АКТИВНОСТЬ ПО ГОДАМ: {patents}
Кандидат: {candidate}
Источники: {sources}
Верни строго JSON:
{{"verdict": "weak_signal|mature|noise", "confidence": 0-100, "stage": "...", "trend": "...",
  "name_ru": "название ПО-РУССКИ (перевод или транслитерация оригинала)",
  "area": "Индустриальный ИИ|Роботы|Инфраструктура ИИ|Финтех|Защита ИИ|Edge|другое",
  "why_weak_signal": "...", "why_not_mature": "...", "advantage": "...", "use_case": "...",
  "key_predictors": ["предиктор 1", "предиктор 2", "предиктор 3"]}}"""

JUDGE_PROMPT = """Ты — независимый аудитор технологических вердиктов. Оцени кандидата
САМОСТОЯТЕЛЬНО, не доверяя первичному анализу. Критерии: ранняя стадия, мало игроков,
рост раундов/упоминаний, отсутствие аналитической категории, независимые подтверждения.
ИСКЛЮЧИ зрелые рынки, стандарты, хайп.
Кандидат: {candidate}
Первичный анализ: {first}
Источники: {sources}
Верни строго JSON: {{"verdict": "weak_signal|mature|noise", "confidence": 0-100,
"critique": "в чём первичный анализ прав/ошибается"}}"""


def _norm(s: str) -> str:
    return " ".join((s or "").lower().split())


async def _arxiv_search(query: str, max_results: int = 5) -> list[dict]:
    """arXiv — доверенный научный источник по ТЗ (не модель, согласования не требует)."""
    url = "http://export.arxiv.org/api/query?search_query=all:" + quote(query) + \
          f"&max_results={max_results}"
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(url)
        r.raise_for_status()
    ns = {"a": "http://www.w3.org/2005/Atom"}
    root = ET.fromstring(r.text)
    return [{"title": (e.findtext("a:title", "", ns) or "").strip(),
             "url": (e.findtext("a:id", "", ns) or "").strip(),
             "snippet": (e.findtext("a:summary", "", ns) or "")[:1500].strip(),
             "published_date": (e.findtext("a:published", "", ns) or "")[:10]}
            for e in root.findall("a:entry", ns)]


async def run_pipeline(query_text: str, emit=None, cfg_override: dict | None = None) -> dict:
    """Основная точка входа в пайплайн. Поддерживает SSE-эмиссию и оверрайд конфига (A/B)."""
    async def _emit(t, **kw):
        if emit:
            await emit(t, **kw)

    rc = cfg_override or await get_runtime_config()
    async with SessionLocal() as db:
        q = SearchQuery(text=query_text, status="running")
        db.add(q)
        await db.commit()
        await db.refresh(q)
        query_id = q.id

    try:
        results, sources_all = [], []
        for rnd in range(1, MAX_ROUNDS + 1):
            await _emit("stage", message=f"Раунд поиска {rnd}: сбор источников…")
            subs = await _expand(query_text, query_id, rnd, rc)
            raw = await _collect_sources(subs, rc)
            sources = await _persist_sources(query_id, raw, rc)
            sources_all += sources
            cands = await _extract_candidates(query_id, sources, query_text, rc)
            await _emit("stage", message=f"Раунд {rnd}: анализ {len(cands)} кандидатов…")
            results += await _analyze(query_id, cands, {s.id: s for s in sources}, rc, _emit)
            weak_n = sum(1 for r in results
                         if r["verdict"] == "weak_signal" and r["source_ids"])
            await _emit("stats", data={"processed_sources": len(sources_all),
                                       "candidates": len(results),
                                       "weak_signals_gt75": sum(
                                           1 for r in results if r["final_score"] > 75),
                                       "excluded": sum(
                                           1 for r in results if r["verdict"] != "weak_signal"),
                                       "borderline": 0})
            if weak_n >= MIN_TOP:
                break
        return await _finalize(query_id, query_text, sources_all, results, rc)
    except Exception:
        async with SessionLocal() as db:
            q = await db.get(SearchQuery, query_id)
            q.status = "error"
            await db.commit()
        raise


async def _expand(query_text: str, query_id: int, rnd: int, rc: dict) -> list[str]:
    """Расширение запроса: 5–6 подзапросов RU/EN через LLM."""
    st = rc["stages"].get("query_expansion", {})
    if not st.get("enabled", True):
        return [query_text]
    prompt = EXPANSION_PROMPT.format(query=query_text)
    if rnd > 1:
        prompt += "\nРаунд уточнения: сгенерируй более узкие и специфичные подзапросы."
    try:
        subs = parse_json(await run_llm("query_expansion", prompt, SYSTEM_JSON,
                                        query_id=query_id, stage=st))
        subs = [s for s in subs if isinstance(s, str)][:6]
    except Exception:
        subs = []
    return subs + [query_text]


async def _collect_sources(sub_queries: list[str], rc: dict) -> list[dict]:
    """Сбор источников: Tavily/Brave → arXiv, дедупликация по url."""
    seen, out, sem = set(), [], asyncio.Semaphore(4)
    n = int(rc["search"].get("results_per_subquery", settings.results_per_subquery))

    async def one(q: str) -> list[dict]:
        async with sem:
            try:
                return await web_search(q, n)
            except Exception:
                return []

    for res in await asyncio.gather(*(one(q) for q in sub_queries)):
        for r in res:
            if r.get("url") and r["url"] not in seen:
                seen.add(r["url"])
                out.append(r)
    try:
        for r in await _arxiv_search(sub_queries[0], 5):
            if r["url"] not in seen:
                seen.add(r["url"])
                out.append(r)
    except Exception:
        pass
    return out


async def _persist_sources(query_id: int, raw: list[dict], rc: dict) -> list[Source]:
    """Сохранение источников + загрузка полных текстов с отказоустойчивостью (ТЗ 8.2)."""
    limit = int(rc["search"].get("max_fetch_sources", settings.max_fetch_sources))
    async with SessionLocal() as db:
        sem = asyncio.Semaphore(5)
        sources = []
        for r in raw[:limit]:
            s = Source(query_id=query_id, url=r["url"], url_hash=url_hash(r["url"]),
                       title=r.get("title", ""), snippet=r.get("snippet", ""),
                       published_date=r.get("published_date"),
                       source_type=classify_type(r["url"], r.get("title", "")),
                       trust_level=trust_level(r["url"]))
            db.add(s)
            sources.append(s)
        await db.commit()

        async def fetch_one(s: Source):
            async with sem:
                try:
                    s.content = await fetch_text(s.url)
                    s.fetched = True
                except Exception:
                    s.content = s.snippet   # фолбэк на сниппет (ТЗ 8.2)
                s.language = detect_language(s.content or s.snippet)

        await asyncio.gather(*(fetch_one(s) for s in sources))
        await db.commit()
        return sources


async def _extract_candidates(query_id: int, sources: list[Source],
                              query_text: str, rc: dict) -> list[dict]:
    """Извлечение кандидатов из текстов с группировкой по нормализованному имени."""
    rr_cfg = component_cfg("reranker")
    reranker = get_reranker() if rr_cfg.get("enabled", True) else None
    if reranker:
        pairs = [(s.id, (s.content or s.snippet)[:2000])
                 for s in sources if (s.content or s.snippet)]
        try:
            keep = set(reranker.rank(query_text, pairs,
                                     top_k=int(rr_cfg.get("top_k", 8))))
            sources = [s for s in sources if s.id in keep]
        except Exception:
            pass   # при ошибке реранкера работаем без него

    groups: dict[str, dict] = {}
    sem = asyncio.Semaphore(3)
    st = rc["stages"].get("candidate_extract", {})

    async def one(s: Source) -> list[dict]:
        if not s.content:
            return []
        async with sem:
            try:
                found = parse_json(await run_llm(
                    "candidate_extract", EXTRACT_PROMPT.format(text=s.content[:9000]),
                    SYSTEM_JSON, query_id=query_id, stage=st))
                return [f for f in found if isinstance(f, dict) and f.get("name")][:5]
            except Exception:
                return []

    for s, found in zip(sources, await asyncio.gather(*(one(s) for s in sources))):
        for f in found:
            g = groups.setdefault(_norm(f["name"]), {
                "name": f["name"], "description": f.get("description", ""),
                "companies": [], "stage": f.get("stage", ""), "trend": f.get("trend", ""),
                "evidence": [], "source_ids": []})
            g["companies"] += [c for c in (f.get("companies") or []) if c not in g["companies"]]
            if f.get("evidence"):
                g["evidence"].append(str(f["evidence"])[:300])
            if s.id not in g["source_ids"]:
                g["source_ids"].append(s.id)
    return list(groups.values())[:30]


async def _judge(query_id: int, cand: dict, first: dict, src_meta: list, rc: dict) -> dict:
    """Второй независимый аудитор: модель другой семьи, чем deep_analysis."""
    st = rc["stages"].get("judge_analysis", {})
    try:
        return parse_json(await run_llm("judge_analysis", JUDGE_PROMPT.format(
            candidate=json.dumps({k: cand.get(k) for k in
                                  ("name", "description", "companies", "stage", "trend")},
                                 ensure_ascii=False),
            first=json.dumps({k: first.get(k) for k in
                              ("verdict", "confidence", "why_weak_signal", "why_not_mature")},
                             ensure_ascii=False),
            sources=json.dumps(src_meta, ensure_ascii=False)),
            query_id=query_id, stage=st))
    except Exception:
        return {"verdict": first.get("verdict", "noise"), "confidence": 0,
                "critique": "аудит недоступен"}


async def _analyze(query_id: int, candidates: list[dict], sources_by_id: dict,
                   rc: dict, emit) -> list[dict]:
    """Глубокий анализ кандидатов: вердикт + скоринг + экспертный приор."""
    sem = asyncio.Semaphore(3)
    w = rc["scoring"]["weights"]
    st_analysis = rc["stages"].get("deep_analysis", {})
    patents_on = bool(rc["search"].get("patents_enabled", True)) and component_enabled("patents")
    pat_key = ((rc.get("secrets") or {}).get("patentsview_api_key")
               or settings.patentsview_api_key)
    b = bank()

    async def one(c: dict) -> dict:
        async with sem:
            src_meta = [{"title": sources_by_id[i].title, "url": sources_by_id[i].url,
                         "trust": sources_by_id[i].trust_level}
                        for i in c["source_ids"] if i in sources_by_id]
            trust = max((TRUST_NUM(sources_by_id[i].trust_level)
                         for i in c["source_ids"] if i in sources_by_id), default=0.0)
            curve = await patent_counts_by_year(c["name"], pat_key) if patents_on else {}
            pat = patent_predictor(curve)
            ex_text = f"{c['name']} {c.get('description', '')}"
            analogs = b.analogs(ex_text) if b else []
            analog = (f"№{analogs[0][0].get('№', '?')} (сходство {analogs[0][1]})"
                      if analogs else "")
            try:
                a = parse_json(await run_llm("deep_analysis", ANALYSIS_PROMPT.format(
                    candidate=json.dumps({k: c.get(k) for k in
                                          ("name", "description", "companies",
                                           "stage", "trend", "evidence")},
                                         ensure_ascii=False),
                    sources=json.dumps(src_meta, ensure_ascii=False),
                    exemplars=(b.few_shot(ex_text) if b else "") or "- (прямых аналогов нет)",
                    patents=curve or "нет данных"),
                    query_id=query_id, stage=st_analysis))
            except Exception:
                a = {"verdict": "noise", "confidence": 0, "why_weak_signal":
                     "Автоматический анализ не выполнен (ошибка модели).",
                     "why_not_mature": "", "advantage": "", "use_case": "",
                     "key_predictors": []}

            clf_score, clf_expl = (
                clf_predict({**c, "why_weak_signal": a.get("why_weak_signal", "")})
                if component_enabled("classifier") else (None, []))
            llm_score = float(a.get("confidence", 0) or 0)

            judge = None
            if (a.get("verdict") == "weak_signal"
                    and rc["stages"].get("judge_analysis", {}).get("enabled", True)):
                judge = await _judge(query_id, c, a, src_meta, rc)
            j_score = float(judge.get("confidence", 0)) if judge else None

            # Нормируемый ансамбль: отключённые компоненты выкидываются из суммы
            parts = [(clf_score, w.get("clf", .35)), (llm_score, w.get("llm", .30)),
                     (j_score, w.get("judge", .15)), (trust * 100, w.get("trust", .20))]
            avail = [(s_, w_) for s_, w_ in parts if s_ is not None]
            tot = sum(w_ for _, w_ in avail) or 1.0
            final = sum(s_ * w_ for s_, w_ in avail) / tot

            # Конфликт аудиторов > 30 п.к. → флаг «требует проверки экспертом»
            disagree = bool(judge) and judge.get("verdict") != a.get("verdict")
            out = {**c,
                   "verdict": a.get("verdict", "noise"),
                   "stage": a.get("stage") or c.get("stage", ""),
                   "trend": a.get("trend") or c.get("trend", ""),
                   "name_ru": a.get("name_ru") or translit_name(c["name"]),
                   "area": a.get("area", ""),
                   "why_weak_signal": a.get("why_weak_signal", ""),
                   "why_not_mature": a.get("why_not_mature", ""),
                   "advantage": a.get("advantage", ""),
                   "use_case": a.get("use_case", ""),
                   "confidence": llm_score,
                   "judge_score": j_score,
                   "judge_verdict": judge.get("verdict") if judge else None,
                   "clf_score": clf_score,
                   "trust_score": round(trust * 100),
                   "patent_curve": curve, "analog": analog,
                   "experts_flag": bool(disagree or (j_score is not None
                                                     and abs(j_score - llm_score) > 30)),
                   "eligible": trust >= 0.6,
                   "final_score": round(final, 1),
                   "key_predictors": (list(a.get("key_predictors", [])) + clf_expl
                                      + ([pat] if pat else []))}
            await emit("candidate", data={
                "name": out["name"], "name_ru": out["name_ru"], "verdict": out["verdict"],
                "final_score": out["final_score"], "area": out["area"],
                "stage": out["stage"], "trend": out["trend"], "analog": out["analog"],
                "experts_flag": out["experts_flag"], "trust_score": out["trust_score"],
                "key_predictors": out["key_predictors"], "sources": src_meta})
            return out

    return list(await asyncio.gather(*(one(c) for c in candidates)))


async def expert_prior() -> dict:
    """Мнение эксперта влияет на ранжирование сразу (до дообучения).
    Штраф за «система: weak, эксперт против», бонус за обратное."""
    async with SessionLocal() as s:
        rows = (await s.execute(
            select(Candidate.name_ru, Candidate.name, Candidate.verdict, ExpertFeedback.agree)
            .join(ExpertFeedback, ExpertFeedback.candidate_id == Candidate.id)
            .where(ExpertFeedback.created_at > datetime.utcnow() - timedelta(days=90)))).all()
    pri: dict[str, float] = {}
    for nr, n, verdict, agree in rows:
        d = (-15.0 if (verdict == "weak_signal" and not agree)
             else (+10.0 if (verdict != "weak_signal" and not agree) else 0.0))
        for key in (_norm(nr), _norm(n)):
            if key:
                pri[key] = pri.get(key, 0.0) + d
    return pri


async def _finalize(query_id: int, query_text: str, sources: list[Source],
                    results: list[dict], rc: dict) -> dict:
    """Финализация: применение expert_prior, сортировка, сохранение в БД с id."""
    # expert_prior применяется ДО сортировки, чтобы прошлые метки сразу влияли на выдачу
    pri = await expert_prior()
    for r in results:
        delta = pri.get(_norm(r.get("name_ru")), 0.0) + pri.get(_norm(r.get("name")), 0.0)
        if delta:
            r["final_score"] = round(min(100.0, max(0.0, r["final_score"] + delta)), 1)
            r["key_predictors"] = list(r["key_predictors"]) + \
                ["учтена экспертная оценка (human-in-the-loop)"]

    weak = [r for r in results if r["verdict"] == "weak_signal" and r["source_ids"]]
    # Надёжные источники поднимаем выше; остальные — по скорингу
    weak.sort(key=lambda r: (not r["eligible"], -r["final_score"]))
    top, rest = weak[:MIN_TOP], weak[MIN_TOP:]
    doubtful = [r for r in results if r not in weak and r["final_score"] >= 40]
    borderline = sorted(rest + doubtful, key=lambda r: -r["final_score"])
    bmin = int(rc["scoring"].get("borderline_min", 5))
    bmax = int(rc["scoring"].get("borderline_max", 7))
    borderline = borderline[:bmax] if len(borderline) >= bmin else borderline
    excluded = [r for r in results if r["verdict"] != "weak_signal"]

    stats = {"processed_sources": len(sources), "candidates": len(results),
             "weak_signals_gt75": sum(1 for x in weak if x["final_score"] > 75),
             "excluded": len(excluded), "borderline": len(borderline)}
    by_id = {s.id: s for s in sources}

    async with SessionLocal() as db:
        orm = {s.id: s for s in
               (await db.scalars(select(Source).where(Source.query_id == query_id))).all()}
        pairs = []
        for r in results:
            c = Candidate(
                query_id=query_id, name=r["name"], name_ru=r.get("name_ru", ""),
                description=r.get("description", ""), companies=r.get("companies", []),
                area=r.get("area", ""), stage=r.get("stage", ""), trend=r.get("trend", ""),
                verdict=r["verdict"], final_score=r["final_score"],
                clf_score=r.get("clf_score"), llm_score=r.get("confidence"),
                judge_score=r.get("judge_score"), judge_verdict=r.get("judge_verdict"),
                experts_flag=r.get("experts_flag", False), trust_score=r.get("trust_score", 0),
                patent_curve=r.get("patent_curve", {}), analog=r.get("analog", ""),
                why_weak_signal=r.get("why_weak_signal", ""),
                why_not_mature=r.get("why_not_mature", ""),
                advantage=r.get("advantage", ""), use_case=r.get("use_case", ""),
                key_predictors=r.get("key_predictors", []))
            c.sources_rel = [orm[i] for i in r["source_ids"] if i in orm]
            db.add(c)
            pairs.append((r, c))
        q = await db.get(SearchQuery, query_id)
        q.status, q.stats, q.finished_at = "done", stats, datetime.utcnow()
        await db.commit()
        # expire_on_commit=False → id доступен сразу
        for r, c in pairs:
            r["id"] = c.id

    def src_out(sid: int) -> dict:
        s = by_id[sid]
        return {"url": s.url, "title": s.title, "published_date": s.published_date,
                "source_type": s.source_type, "language": s.language or "?",
                "trust_level": s.trust_level}

    def cand_out(r: dict) -> dict:
        return {"id": r.get("id"), "name": r["name"], "name_ru": r.get("name_ru", ""),
                "description": r.get("description", ""), "companies": r.get("companies", []),
                "area": r.get("area", ""), "stage": r.get("stage", ""),
                "trend": r.get("trend", ""), "verdict": r["verdict"],
                "final_score": r["final_score"], "clf_score": r.get("clf_score"),
                "llm_score": r.get("confidence"), "judge_score": r.get("judge_score"),
                "judge_verdict": r.get("judge_verdict"),
                "experts_flag": r.get("experts_flag", False),
                "trust_score": r.get("trust_score", 0), "analog": r.get("analog", ""),
                "why_weak_signal": r.get("why_weak_signal", ""),
                "why_not_mature": r.get("why_not_mature", ""),
                "advantage": r.get("advantage", ""), "use_case": r.get("use_case", ""),
                "key_predictors": r.get("key_predictors", []),
                "sources": [src_out(i) for i in r["source_ids"] if i in by_id]}

    return {"query_id": query_id, "query": query_text, "stats": stats,
            "top": [cand_out(r) for r in top],
            "borderline": [{"id": r.get("id"), "name": r["name"],
                            "name_ru": r.get("name_ru", ""),
                            "final_score": r["final_score"]} for r in borderline],
            "excluded": [cand_out(r) for r in excluded],
            "model_disclosure": {t: {"provider": st.get("provider"), "model": st.get("model"),
                                     "temperature": st.get("temperature")}
                                 for t, st in rc["stages"].items()}}