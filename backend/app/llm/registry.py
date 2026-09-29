"""Реестр моделей: детерминированная таблица «задача → модель» + явное логирование (ТЗ 3.1).

- Конфигурация из models_config.yaml; файла нет или он битый → встроенный DEFAULT_CONFIG;
- run_llm() принимает stage-оверрайд из рантайм-конфига (пульт admin.html);
- КАЖДЫЙ вызов пишется в llm_calls: request_id, task, model, provider, latency, status;
- MODEL_BY_TASK — раскрытие формата выбора модели (GET /api/models);
- ВСЕ провайдеры (yandex | gigachat | qwen | openai | …) — через OpenAI-совместимый
  эндпоинт провайдера (app.llm.openai_compat); нативных SDK нет;
- авто-выбора модели НЕТ: выбор фиксирован таблицей (требование ТЗ 3.1).
"""
import copy
import json
import os
import time
import uuid

import yaml

from app.config import settings
from app.db import LLMCall, SessionLocal


class StageDisabled(RuntimeError):
    """Стадия пайплайна выключена в конфигурации — пайплайн деградирует gracefully."""


DEFAULT_CONFIG = {
    "stages": {
                "query_expansion":   {"enabled": True, "provider": "qwen",
                              "model": "Qwen/Qwen3.6-35B-A3B", "temperature": 0.4},
        "candidate_extract": {"enabled": True, "provider": "qwen",
                              "model": "Qwen/Qwen3.6-35B-A3B", "temperature": 0.1},
        "deep_analysis":     {"enabled": True, "provider": "openai",
                              "model": "openai/gpt-4.1",       "temperature": 0.2},
        "judge_analysis":    {"enabled": True, "provider": "gigachat",
                              "model": "GigaChat/GigaChat-2-Max", "temperature": 0.1},
        "report_generation": {"enabled": True, "provider": "gigachat",
                              "model": "GigaChat/GigaChat-2-Max", "temperature": 0.5},
    },
    "components": {
        "reranker":   {"enabled": True, "type": "api", "format": "tei",
                       "model": "BAAI/bge-reranker-v2-m3", "top_k": 8,
                       "base_url": None, "api_key": None},
        "classifier": {"enabled": True},
        "patents":    {"enabled": True},
        "pgvector":   {"enabled": False},
    },
}


def load_yaml_config() -> dict:
    """База конфигурации: yaml поверх встроенных дефолтов (использует runtime_config)."""
    path = settings.models_config_path
    if not os.path.exists(path):
        return copy.deepcopy(DEFAULT_CONFIG)
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except Exception:
        return copy.deepcopy(DEFAULT_CONFIG)
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    for section in ("stages", "components"):
        for key, val in (data.get(section) or {}).items():
            cfg[section].setdefault(key, {}).update(val or {})
    return cfg


_CFG = load_yaml_config()

# Раскрытие выбора моделей (ТЗ 3.1) — отдаётся на GET /api/models
MODEL_BY_TASK = {t: {"provider": c.get("provider"), "model": c.get("model"),
                     "temperature": c.get("temperature")}
                 for t, c in _CFG["stages"].items()}


def stage_cfg(task: str) -> dict:
    st = _CFG["stages"].get(task, {})
    return {"enabled": bool(st.get("enabled", True)),
            "provider": st.get("provider", "yandex"),
            "model": st.get("model", "yandexgpt-lite"),
            "temperature": float(st.get("temperature", 0.2)),
            "base_url": st.get("base_url") or None,     # None → дефолт провайдера из .env
            "api_key": st.get("api_key") or None}       # None → ключ провайдера из .env


def component_cfg(name: str) -> dict:
    return _CFG["components"].get(name, {}) or {}


def component_enabled(name: str) -> bool:
    return bool(component_cfg(name).get("enabled", name != "pgvector"))


async def _call_provider(cfg: dict, prompt: str, system: str) -> str:
    provider = cfg["provider"]
    if provider == "perplexity":
        if not settings.use_sonar:
            raise StageDisabled("Perplexity Sonar выключен: требует согласования (ТЗ 3.1)")
        from app.llm.sonar import sonar_completion      # опциональный модуль
        return await sonar_completion(cfg["model"], prompt, system, cfg["temperature"])
    # Все остальные провайдеры — единый OpenAI-совместимый клиент
    from app.llm.openai_compat import chat_completion
    return await chat_completion(cfg["model"], prompt, system, cfg["temperature"],
                                 provider=provider,
                                 base_url=cfg.get("base_url"), api_key=cfg.get("api_key"))


async def run_llm(task: str, prompt: str, system: str = "",
                  query_id: int | None = None, stage: dict | None = None) -> str:
    """Единственная точка входа в LLM: конфиг стадии + оверрайд + логирование."""
    cfg = {**stage_cfg(task), **(stage or {})}
    if not cfg.get("enabled", True):
        raise StageDisabled(task)
    request_id, t0 = str(uuid.uuid4()), time.time()
    status, text = "ok", ""
    try:
        text = await _call_provider(cfg, prompt, system)
        return text
    except Exception as e:
        status, text = "error", str(e)[:8000]
        raise
    finally:
        try:   # логирование не должно рвать пайплайн
            async with SessionLocal() as s:
                s.add(LLMCall(request_id=request_id, query_id=query_id, task=task,
                              model=cfg.get("model", ""), provider=cfg.get("provider", ""),
                              prompt=prompt[:4000], response=text[:8000],
                              latency_ms=(time.time() - t0) * 1000, status=status))
                await s.commit()
        except Exception:
            pass


def parse_json(text: str):
    """Достаёт JSON из ответа модели: срезает ```-обёртки, ищет первую [ или { … последнюю ] или }."""
    text = (text or "").strip()
    for fence in ("```json", "```"):
        if text.startswith(fence):
            text = text[len(fence):]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()
    start = next((i for i, ch in enumerate(text) if ch in "[{"), None)
    end = next((i + 1 for i in range(len(text) - 1, -1, -1) if text[i] in "]}"), None)
    if start is None or end is None or end <= start:
        raise ValueError("В ответе модели нет JSON")
    return json.loads(text[start:end])