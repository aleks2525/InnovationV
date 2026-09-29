"""Тип источника, язык оригинала и уровень доверенности (ТЗ, раздел 2).

Источник истины — таблица source_trust_registry в PostgreSQL (редактируется экспертом);
кэш обновляется при старте сервиса и по TTL. Если таблицы нет (свежая БД до schema.sql)
или БД недоступна — работают встроенные словари-фолбэки с той же логикой.
"""
import time
from urllib.parse import urlparse

# --- Фолбэк-словари (полный дубль seed-данных schema.sql) ---
HIGH_TRUST = {
    "europa.eu", "europol.europa.eu", "cbr.ru", "government.ru", "nist.gov", "cftc.gov",
    "bis.org", "imf.org", "rospatent.gov.ru", "wipo.int",
    "arxiv.org", "nature.com", "science.org", "sciencedirect.com", "ieee.org", "acm.org",
    "frontiersin.org", "mdpi.com", "reuters.com", "ft.com", "techcrunch.com",
}
LOW_TRUST = {
    "medium.com", "habr.com", "vc.ru", "linkedin.com", "x.com", "twitter.com",
    "facebook.com", "t.me", "reddit.com", "zen.yandex.ru", "pikabu.ru",
    "awesomeagents.ai", "cryptodaily.co.uk", "stlpartners.com",
}
GOV_SUFFIXES = (".gov", ".gov.ru", ".int")
EDU_SUFFIXES = (".edu", ".ac.uk")

INDUSTRY_MEDIA = {
    "techcrunch.com", "siliconangle.com", "reuters.com", "theblock.co", "techfundingnews.com",
    "securityweek.com", "datacenterdynamics.com", "eetimes.com", "therobotreport.com",
    "geekwire.com", "biometricupdate.com", "tomshardware.com", "theregister.com",
}
VC_TRACKERS = {
    "pulse2.com", "startupticker.ch", "startupresearcher.com", "fundz.net",
    "tamradar.com", "thesaasnews.com", "crowdfundinsider.com", "techstartups.com",
}
SCIENCE_KEYS = ("arxiv", "nature", "science", "ieee", "acm.", "frontiersin", "mdpi",
                "pubs.rsc", "doi.org")
PATENT_KEYS = ("patent", "wipo", "rospatent", "patsnap")
PR_KEYS = ("press-release", "pressreleases", "prnewswire", "businesswire",
           "globenewswire", "newsroom", "press.pdf")

_REGISTRY: dict[str, tuple[str, str]] = {}     # domain -> (trust_level, source_class)
_REGISTRY_TS = 0.0
_TTL = 300.0


def domain_of(url: str) -> str:
    return urlparse(url).netloc.removeprefix("www.").lower()


async def refresh_registry(force: bool = False) -> None:
    """Кэш реестра доверенности из БД. Вызывать в lifespan main.py при старте."""
    global _REGISTRY, _REGISTRY_TS
    if not force and _REGISTRY and time.time() - _REGISTRY_TS < _TTL:
        return
    try:
        from sqlalchemy import text
        from app.db import SessionLocal
        async with SessionLocal() as s:
            rows = (await s.execute(
                text("SELECT domain, trust_level, source_class FROM source_trust_registry"))).all()
        _REGISTRY = {r[0]: (r[1], r[2]) for r in rows}
        _REGISTRY_TS = time.time()
    except Exception:
        pass                                    # таблицы нет / БД недоступна → фолбэки


def _registry_lookup(d: str) -> tuple[str, str] | None:
    """Точное совпадение или субдомен; побеждает самый длинный домен (как fn_source_trust)."""
    if d in _REGISTRY:
        return _REGISTRY[d]
    hits = [dom for dom in _REGISTRY if d.endswith("." + dom)]
    return _REGISTRY[max(hits, key=len)] if hits else None


def _builtin_level(d: str) -> str:
    if d in HIGH_TRUST or d.endswith(GOV_SUFFIXES) or d.endswith(EDU_SUFFIXES):
        return "высокая"
    if d in LOW_TRUST:
        return "пониженная"
    return "средняя"


def trust_level(url: str) -> str:
    """Уровень доверенности источника строкой для UI/отчётов (ТЗ)."""
    d = domain_of(url)
    reg = _registry_lookup(d)
    level = reg[0] if reg else _builtin_level(d)
    if level == "высокая":
        return "высокая"
    if level == "пониженная":
        return "пониженная — требуется подтверждение независимым источником"
    return "средняя"


def classify_type(url: str, title: str = "") -> str:
    """Тип источника: госорган | университет | наука | патентная база | отраслевое медиа |
    венчурный трекер | пресс-релиз | блог | агрегатор | иное."""
    d, t = domain_of(url), (url + " " + title).lower()
    reg = _registry_lookup(d)
    if reg and reg[1]:
        return reg[1]
    if d.endswith(GOV_SUFFIXES) or d in ("europa.eu", "europol.europa.eu"):
        return "государственный орган / регулятор"
    if d.endswith(EDU_SUFFIXES):
        return "университет"
    if any(k in d for k in SCIENCE_KEYS):
        return "научная публикация"
    if any(k in d for k in PATENT_KEYS):
        return "патентная база"
    if any(k in t for k in PR_KEYS):
        return "пресс-релиз (первичный индикатор)"
    if "blog" in t or d in LOW_TRUST:
        return "блог / соцсеть (первичный индикатор)"
    if d in INDUSTRY_MEDIA:
        return "отраслевое медиа"
    if d in VC_TRACKERS:
        return "венчурный трекер"
    return "иное — требуется проверка первоисточника"


def detect_language(text: str) -> str:
    """Язык оригинала: доля кириллицы в первых 2000 символов."""
    sample = (text or "")[:2000]
    cyr = sum(1 for ch in sample if "\u0400" <= ch <= "\u04FF")
    alpha = sum(1 for ch in sample if ch.isalpha())
    return "ru" if alpha and cyr / alpha > 0.3 else "en"