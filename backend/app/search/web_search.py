"""Поиск по открытым источникам с каскадом фолбэков:
   Tavily (если есть ключ) → Brave (если есть ключ) → DuckDuckGo (всегда работает).
   Yandex XML — опционально, включается через search_provider=yandex_xml в пульте.

DuckDuckGo не требует API-ключей и работает без ограничений (фолбэк по умолчанию).
"""
import xml.etree.ElementTree as ET

import httpx

from app.config import settings


async def _tavily(query: str, max_results: int) -> list[dict]:
    """Tavily API (требует ключ, 1000 кредитов/мес бесплатно)."""
    import asyncio
    try:
        from tavily import TavilyClient
    except ImportError:
        raise RuntimeError("tavily-python не установлен")

    if not settings.tavily_api_key:
        raise RuntimeError("TAVILY_API_KEY не задан")

    def _call():
        client = TavilyClient(api_key=settings.tavily_api_key)
        r = client.search(query=query, max_results=max_results,
                          search_depth="advanced", include_answer=False)
        return [{"title": x.get("title", ""),
                 "url": x.get("url", ""),
                 "snippet": (x.get("content") or "")[:1500],
                 "published_date": x.get("published_date")}
                for x in r.get("results", [])]

    return await asyncio.to_thread(_call)


async def _brave(query: str, max_results: int) -> list[dict]:
    """Brave Search API (требует ключ, 2000 запросов/мес бесплатно)."""
    if not settings.brave_api_key:
        raise RuntimeError("BRAVE_API_KEY не задан")

    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.get("https://api.search.brave.com/res/v1/web/search",
                        headers={"X-Subscription-Token": settings.brave_api_key,
                                 "Accept": "application/json"},
                        params={"q": query, "count": max_results})
        r.raise_for_status()
        return [{"title": x.get("title", ""),
                 "url": x.get("url", ""),
                 "snippet": (x.get("description") or "")[:1500],
                 "published_date": None}
                for x in r.json().get("web", {}).get("results", [])]


async def _duckduckgo(query: str, max_results: int) -> list[dict]:
    """DuckDuckGo — бесплатный, без ключей, без лимитов."""
    import asyncio
    try:
        from ddgs import DDGS
    except ImportError:
        from duckduckgo_search import DDGS

    def _call():
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
            return [{"title": x.get("title", ""),
                     "url": x.get("href", ""),
                     "snippet": (x.get("body") or "")[:1500],
                     "published_date": None}
                    for x in results]

    return await asyncio.to_thread(_call)


async def _yandex_xml(query: str, max_results: int) -> list[dict]:
    """Yandex Search XML (платный, высокое качество RU-источников).
    Требует YANDEX_XML_USER и YANDEX_XML_KEY в .env."""
    if not settings.yandex_xml_user or not settings.yandex_xml_key:
        raise RuntimeError("YANDEX_XML_USER/YANDEX_XML_KEY не заданы в .env")

    xml_body = f'''<?xml version="1.0" encoding="UTF-8"?>
<request>
  <query>{query}</query>
  <sortby order="descending" priority="no">rlv</sortby>
  <maxpassages>1</maxpassages>
  <page>0</page>
  <groupings>
    <groupby attr="d" mode="plain" groups-on-page="{max_results}" docs-in-group="1"/>
  </groupings>
</request>'''

    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post("https://yandex.ru/search/xml",
                         params={"user": settings.yandex_xml_user,
                                 "key": settings.yandex_xml_key},
                         content=xml_body,
                         headers={"Content-Type": "application/xml"})
        r.raise_for_status()

    root = ET.fromstring(r.text)
    results = []
    for group in root.findall('.//group'):
        doc = group.find('.//doc')
        if doc is not None:
            results.append({
                "title": (doc.findtext('title') or '')[:200],
                "url": doc.findtext('url') or '',
                "snippet": (doc.findtext('passages') or '')[:1500],
                "published_date": None,
            })
    return results[:max_results]


_PROVIDERS = {
    "tavily": _tavily,
    "brave": _brave,
    "duckduckgo": _duckduckgo,
    "yandex_xml": _yandex_xml,
}


async def web_search(query: str, max_results: int = 6) -> list[dict]:
    """Каскад: основной из .env SEARCH_PROVIDER → Tavily → Brave → DuckDuckGo."""
    primary = settings.search_provider  # tavily|brave|duckduckgo|yandex_xml
    order = [primary] if primary in _PROVIDERS else []

    # Добавляем резервные (если есть ключи)
    if "tavily" not in order and settings.tavily_api_key:
        order.append("tavily")
    if "brave" not in order and settings.brave_api_key:
        order.append("brave")
    # DuckDuckGo — всегда в конце как фолбэк
    if "duckduckgo" not in order:
        order.append("duckduckgo")

    last_error = None
    for name in order:
        try:
            return await _PROVIDERS[name](query, max_results)
        except Exception as e:
            last_error = f"{name}: {e}"
            continue

    raise RuntimeError(f"Все поисковые провайдеры недоступны: {last_error}")
