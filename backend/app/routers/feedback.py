"""Human-in-the-loop: экспертная обратная связь по вердиктам системы → дообучение."""
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, Response
from sqlalchemy import func, select

from app.db import Candidate, ExpertFeedback, SessionLocal

router = APIRouter(prefix="/api", tags=["human-in-the-loop"])


@router.post("/candidates/{cid}/feedback")
async def add_feedback(cid: int, body: dict):
    if body.get("agree") is None:
        raise HTTPException(400, "Поле agree обязательно (true/false)")
    async with SessionLocal() as s:
        c = await s.get(Candidate, cid)
        if c is None:
            raise HTTPException(404, "Кандидат не найден")
        s.add(ExpertFeedback(candidate_id=cid, agree=bool(body["agree"]),
                             comment=str(body.get("comment", ""))[:2000]))
        await s.commit()
    label = int((c.verdict == "weak_signal") if body["agree"]
                else (c.verdict != "weak_signal"))
    return {"ok": True, "candidate_id": cid,
            "system_verdict": c.verdict, "expert_label": label}


@router.get("/candidates/{cid}/feedback")
async def get_feedback(cid: int):
    async with SessionLocal() as s:
        rows = (await s.scalars(select(ExpertFeedback)
                .where(ExpertFeedback.candidate_id == cid)
                .order_by(ExpertFeedback.id.desc()))).all()
        return [{"agree": r.agree, "comment": r.comment,
                 "created_at": str(r.created_at)} for r in rows]


@router.get("/feedback/stats")
async def feedback_stats():
    """Сводка для демо: накоплено меток и доля согласия эксперта с системой."""
    async with SessionLocal() as s:
        total = (await s.scalar(select(func.count(ExpertFeedback.id)))) or 0
        agree = (await s.scalar(select(func.count(ExpertFeedback.id))
                 .where(ExpertFeedback.agree.is_(True)))) or 0
        month = (await s.scalar(select(func.count(ExpertFeedback.id))
                 .where(ExpertFeedback.created_at >
                        datetime.utcnow() - timedelta(days=30)))) or 0
    return {"total": total, "agree": agree,
            "agreement_rate": round(agree / total, 3) if total else None,
            "last_30_days": month}


@router.get("/feedback/export.csv")
async def export_feedback():
    """Строки дообучения в формате train.csv: retrain.py читает напрямую."""
    async with SessionLocal() as s:
        rows = (await s.execute(
            select(Candidate, ExpertFeedback)
            .join(ExpertFeedback, ExpertFeedback.candidate_id == Candidate.id)
            .order_by(ExpertFeedback.id))).all()
    esc = lambda v: '"' + str(v or "").replace('"', '""') + '"'
    lines = ["name,area,companies,reason,stage,trend,score,is_weak_signal,expert_comment"]
    for c, f in rows:
        label = int((c.verdict == "weak_signal") if f.agree
                    else (c.verdict != "weak_signal"))
        lines.append(",".join([esc(c.name_ru or c.name), esc(c.area),
                               esc(", ".join(c.companies or [])),
                               esc(c.why_weak_signal or c.why_not_mature or c.description),
                               esc(c.stage), esc(c.trend), "", str(label), esc(f.comment)]))
    return Response("\n".join(lines).encode("utf-8-sig"), media_type="text/csv",
                    headers={"Content-Disposition":
                             'attachment; filename="expert_feedback.csv"'})