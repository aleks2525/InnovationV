"""Точка входа FastAPI.

Боевой стенд: nginx проксирует https://factrank.ru/signals/* БЕЗ снятия префикса,
поэтому роутеры, docs и статика монтируются под PREFIX="/signals".
Путь к статике нормализуется в абсолютный — запуск не зависит от CWD.
"""
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.db import Base, engine
from app.routers.admin import router as admin_router
from app.routers.feedback import router as feedback_router
from app.routers.search import router as search_router

PREFIX = "/signals"   # если nginx начнёт снимать префикс (proxy_pass ...8000/;) — ставьте ""

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC_DIR = (settings.static_dir if os.path.isabs(settings.static_dir)
              else os.path.normpath(os.path.join(_BACKEND_DIR, settings.static_dir)))


@asynccontextmanager
async def lifespan(app: FastAPI):
    # create_all идемпотентен: схема накатана через schema.sql — операторы пропускаются
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield


app = FastAPI(
    title="Слабые сигналы — Газпромбанк.Тех",
    description="Поисково-аналитический модуль зарождающихся трендов (слабых сигналов). "
                "Этап 1: обученная интерпретируемая модель. Этап 2: открытый поиск, ТОП-15.",
    version="1.0",
    lifespan=lifespan,
    docs_url=f"{PREFIX}/docs",            # Swagger UI на /signals/docs
    redoc_url=f"{PREFIX}/redoc",
    openapi_url=f"{PREFIX}/openapi.json",
)

app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])

app.include_router(search_router, prefix=PREFIX)     # /signals/api/search, stream, report...
app.include_router(feedback_router, prefix=PREFIX)   # /signals/api/candidates/{id}/feedback...
app.include_router(admin_router, prefix=PREFIX)      # /signals/api/admin/*


@app.get(f"{PREFIX}/api/health", tags=["служебное"])
@app.get("/api/health", include_in_schema=False, tags=["служебное"])
async def health():
    return {"status": "ok"}


# Статика LAST: роутеры (включая docs) имеют приоритет; index/admin/about.html + assets/
app.mount(PREFIX, StaticFiles(directory=STATIC_DIR, html=True), name="frontend")
