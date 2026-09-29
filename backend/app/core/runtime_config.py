"""Рантайм-конфигурация: приоритет слоёв .env → models_config.yaml → app_settings (UI).

- get_runtime_config() — конфиг с оверрайдом из БД (кэш TTL 5 c);
- put_runtime_config() — сохранение патча + аудит-запись 'audit::<ts>';
- ALLOWED_MODELS — гард ТЗ 3.1: UI/API не могут выбрать модель вне перечня.
"""
import copy
import time
from datetime import datetime

from sqlalchemy import select

from app.db import AppSetting, SessionLocal
from app.llm.registry import load_yaml_config

TTL = 5.0
_CACHE = {"ts": 0.0, "cfg": None}

# Гард ТЗ 3.1: интерфейс предлагает ТОЛЬКО разрешённые модели
ALLOWED_MODELS = {
    "qwen":     ["Qwen/Qwen3.6-35B-A3B", "Qwen/Qwen3-30B-A3B", "Qwen/Qwen3-32B",
                 "Qwen/Qwen3-235B"],                      # последние два — если появятся в каталоге
    "gigachat": ["GigaChat/GigaChat-2-Max"],
    "openai":   ["openai/gpt-4.1", "openai/gpt-5.6-luna"], # gpt-5.6-luna — если появится
    "yandex":   ["yandexgpt-lite", "yandexgpt"],
}


def deep_merge(base: dict, patch: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (patch or {}).items():
        if isinstance(out.get(k), dict) and isinstance(v, dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def base_config() -> dict:
    """База из models_config.yaml (или встроенных дефолтов, если файла нет)."""
    y = load_yaml_config()
    return {
        "search": {"provider": "tavily", "results_per_subquery": 6,
                   "max_fetch_sources": 12, "patents_enabled": True},
        "stages": {t: {"enabled": bool(c.get("enabled", True)),
                       "provider": c.get("provider", "yandex"),
                       "model": c.get("model", "yandexgpt-lite"),
                       "temperature": float(c.get("temperature", 0.2)),
                       "base_url": c.get("base_url") or None,
                       "api_key": c.get("api_key") or None}
                   for t, c in (y.get("stages") or {}).items()},
        "components": {k: {"enabled": bool(v.get("enabled", k != "pgvector")),
                           **{kk: vv for kk, vv in v.items() if kk != "enabled"}}
                       for k, v in (y.get("components") or {}).items()},
        "scoring": {"weights": {"clf": .35, "llm": .30, "judge": .15, "trust": .20},
                    "borderline_min": 5, "borderline_max": 7},
        "secrets": {k: "" for k in ("tavily_api_key", "brave_api_key", "patentsview_api_key",
                                    "qwen_api_key", "openai_api_key", "yc_oauth_token")},
    }


async def get_runtime_config(force: bool = False) -> dict:
    now = time.time()
    if not force and _CACHE["cfg"] is not None and now - _CACHE["ts"] < TTL:
        return _CACHE["cfg"]
    cfg = base_config()
    async with SessionLocal() as s:
        row = (await s.scalars(select(AppSetting).where(AppSetting.key == "runtime"))).first()
        if row and row.value:
            cfg = deep_merge(cfg, row.value)
    _CACHE.update(ts=now, cfg=cfg)
    return cfg


async def put_runtime_config(patch: dict, user: str = "admin") -> dict:
    async with SessionLocal() as s:
        row = (await s.scalars(select(AppSetting).where(AppSetting.key == "runtime"))).first()
        merged = deep_merge(row.value if row else {}, patch)
        if row:
            row.value, row.updated_at, row.updated_by = merged, datetime.utcnow(), user
        else:
            s.add(AppSetting(key="runtime", value=merged, updated_by=user))
        s.add(AppSetting(key=f"audit::{int(time.time())}",
                         value={"by": user, "patch": patch}))
        await s.commit()
    return await get_runtime_config(force=True)