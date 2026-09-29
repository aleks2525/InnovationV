"""Загрузка полных текстов страниц: отказоустойчивость парсеров (ТЗ 8.2).

- tenacity: 2 попытки с экспоненциальным бэкоффом;
- таймаут 20 c, редиректы, идентифицирующий User-Agent;
- извлечение основного текста — trafilatura (в отдельном потоке, не блокирует event loop);
- если полный текст недоступен — пайплайн получает фолбэк на сниппет поисковой выдачи.
"""
import asyncio
import hashlib

import httpx
import trafilatura
from tenacity import retry, stop_after_attempt, wait_exponential

USER_AGENT = ("WeakSignalsResearchBot/1.0 (educational hackathon project; "
              "contact: factrank.ru) ")


@retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=1, min=1, max=4))
async def fetch_text(url: str) -> str:
    """Возвращает основной текст страницы (до 12 000 символов) или бросает исключение."""
    async with httpx.AsyncClient(timeout=20, follow_redirects=True,
                                 headers={"User-Agent": USER_AGENT}) as c:
        r = await c.get(url)
        r.raise_for_status()
        html = r.text
    text = await asyncio.to_thread(trafilatura.extract, html, include_comments=False)
    if not text or len(text) < 200:
        raise ValueError("пустой контент")
    return text[:12000]


def url_hash(url: str) -> str:
    """sha256(url) — ключ дедупликации источников между раундами и запросами."""
    return hashlib.sha256(url.encode()).hexdigest()