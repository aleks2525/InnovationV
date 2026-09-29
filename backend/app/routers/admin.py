"""Пульт управления конфигурацией (admin.html): настройки, аудит, проверка боеготовности.

Эндпоинты:
    GET  /api/admin/settings   — конфиг с маскированными секретами + ALLOWED_MODELS
    PUT  /api/admin/settings    — сохранение патча (модели проходят гард ТЗ 3.1)
    GET  /api/admin/audit       — журнал изменений (ключи 'audit::<unixtime>')
    POST /api/admin/test        — пинг провайдера/ключа с латентностью
"""
import time

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select

from app.config import settings
from app.core.runtime_config import (ALLOWED_MODELS, deep_merge,
                                      get_runtime_config, put_runtime_config)
from app.db import AppSetting, SessionLocal

router = APIRouter(prefix="/api/admin", tags=["admin"])


async def require_admin(x_admin_token: str = Header(default="")):
    """Проверка токена админки из заголовка X-Admin-Token.
    Токен задаётся переменной ADMIN_TOKEN в .env."""
    if not settings.admin_token or x_admin_token != settings.admin_token:
        raise HTTPException(403, "Неверный админ-токен")


def model_allowed(provider: str, model: str) -> bool:
    """Гард ТЗ 3.1 с терпимостью к префиксам провайдера.

    Допускает:
      - точное совпадение (например, "Qwen/Qwen3.6-35B-A3B" в списке allowed),
      - совпадение с префиксом ("vendor/Qwen/Qwen3.6-35B-A3B" ≡ "Qwen/Qwen3.6-35B-A3B"),
      - совпадение базового имени ("qwen3.6-35b-a3b" ≡ "Qwen/Qwen3.6-35B-A3B").
    """
    allowed = ALLOWED_MODELS.get(provider, []) or []
    if not allowed:
        return False
    if model in allowed:
        return True
    base = model.rsplit("/", 1)[-1].lower()
    return any(model.endswith("/" + a) or a.lower() == base for a in allowed)


def mask_secret(v: str) -> dict:
    """Маскирование секрета: полный текст только в БД, в API — только индикатор."""
    if not v:
        return {"set": False, "preview": ""}
    preview = f"…{v[-4:]}" if len(v) > 4 else "***"
    return {"set": True, "preview": preview}


@router.get("/settings", dependencies=[Depends(require_admin)])
async def get_settings():
    """Получить актуальный рантайм-конфиг: секреты маскируются, модели видны."""
    cfg = await get_runtime_config()
    pub = deep_merge(cfg, {
        "secrets": {k: mask_secret(v) for k, v in (cfg.get("secrets") or {}).items()}
    })
    return {"config": pub, "allowed_models": ALLOWED_MODELS}


@router.put("/settings", dependencies=[Depends(require_admin)])
async def update_settings(patch: dict):
    """Сохранить патч конфига. Секреты: пустое значение / '***' / null = «не менять».
    Модели проверяются гардом ALLOWED_MODELS (с терпимостью к префиксам)."""
    cur = await get_runtime_config()

    # 1. Секреты: пропуск пустых/маскированных значений
    secrets_patch = patch.get("secrets") or {}
    for k, v in list(secrets_patch.items()):
        if v in ("", "***", None):
            secrets_patch[k] = cur.get("secrets", {}).get(k, "")

    # 2. Гард ТЗ 3.1: модели только из разрешённого перечня
    for task, st in (patch.get("stages") or {}).items():
        prov = st.get("provider")
        model = st.get("model")
        if prov and model and not model_allowed(prov, model):
            raise HTTPException(
                400,
                f"Модель «{model}» провайдера «{prov}» не входит в разрешённый перечень ТЗ 3.1. "
                f"Допустимы: {ALLOWED_MODELS.get(prov, [])}"
            )

    return await put_runtime_config(patch, user="admin")


@router.get("/audit", dependencies=[Depends(require_admin)])
async def audit(limit: int = 30):
    """Журнал изменений конфигурации (записи 'audit::<unixtime>')."""
    async with SessionLocal() as s:
        rows = (await s.scalars(
            select(AppSetting)
            .where(AppSetting.key.like("audit::%"))
            .order_by(AppSetting.key.desc())
            .limit(limit)
        )).all()
        return [{
            "at": str(r.updated_at),
            "by": (r.value or {}).get("by"),
            "patch": (r.value or {}).get("patch"),
        } for r in rows]


@router.post("/test", dependencies=[Depends(require_admin)])
async def test_target(body: dict):
    """Пинг провайдера/ключа с латентностью — проверка боеготовности перед демо.

    body: {"target": "tavily"|"brave"|"patents"|"yandex"|"qwen"|"openai"|"gigachat",
           "api_key": <опционально, тест ещё не сохранённого ключа>}
    """
    cfg = await get_runtime_config()
    sec = dict(cfg.get("secrets") or {})
    # временный оверрайд ключа (для теста ещё не сохранённого значения)
    if body.get("api_key"):
        target = body.get("target", "")
        for k in list(sec.keys()):
            if target in k:
                sec[k] = body["api_key"]
                break

    t0 = time.time()
    try:
        t = body.get("target")

        if t == "tavily":
            from app.search.web_search import _tavily
            await _tavily("quantum computing", 1)

        elif t == "brave":
            from app.search.web_search import _brave
            await _brave("quantum computing", 1)

        elif t == "patents":
            from app.search.patents import patent_counts_by_year
            await patent_counts_by_year("neural network",
                                         sec.get("patentsview_api_key", ""))

        elif t in ("yandex", "qwen", "openai", "gigachat"):
            # используем первую разрешённую модель провайдера как smoke-test
            allowed = ALLOWED_MODELS.get(t)
            if not allowed:
                raise HTTPException(400, f"Провайдер «{t}» не имеет разрешённых моделей")
            from app.llm.registry import run_llm
            await run_llm(
                "query_expansion",
                "Скажи слово ok",
                stage={
                    "enabled": True,
                    "provider": t,
                    "model": allowed[0],
                    "temperature": 0.0,
                    "api_key": body.get("api_key") or None,
                },
            )
        else:
            raise HTTPException(400,
                                f"Неизвестная цель теста «{t}». Допустимо: "
                                "tavily|brave|patents|yandex|qwen|openai|gigachat")

        return {"ok": True, "latency_ms": round((time.time() - t0) * 1000)}

    except HTTPException:
        raise
    except Exception as e:
        return {
            "ok": False,
            "error": str(e)[:300],
            "latency_ms": round((time.time() - t0) * 1000),
        }