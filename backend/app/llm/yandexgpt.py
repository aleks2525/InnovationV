"""Клиент Yandex Cloud Foundation Models: YandexGPT Lite 5 / Pro 5.1 (разрешены ТЗ 3.1).

Аутентификация: OAuth-токен (.env YC_OAUTH_TOKEN) → IAM-токен с кэшем на 1 час.
model: 'yandexgpt-lite' (Lite 5) | 'yandexgpt' (Pro 5/5.1).
"""
import time

import httpx

from app.config import settings

IAM_URL = "https://iam.api.cloud.yandex.net/iam/v1/tokens"
LLM_URL = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
_cache = {"token": None, "expires": 0.0}


async def _iam_token() -> str:
    if _cache["token"] and time.time() < _cache["expires"]:
        return _cache["token"]
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(IAM_URL, json={"yandexPassportOauthToken": settings.yc_oauth_token})
        r.raise_for_status()
        _cache.update(token=r.json()["iamToken"], expires=time.time() + 3600)
    return _cache["token"]


async def yandexgpt_completion(model: str, prompt: str, system: str = "",
                               temperature: float = 0.2, max_tokens: int = 3000) -> str:
    token = await _iam_token()
    body = {
        "modelUri": f"gpt://{settings.yc_folder_id}/{model}/latest",
        "completionOptions": {"stream": False, "temperature": temperature,
                              "maxTokens": max_tokens},
        "messages": [{"role": "system", "text": system},
                     {"role": "user", "text": prompt}],
    }
    async with httpx.AsyncClient(timeout=120) as c:
        r = await c.post(LLM_URL, headers={"Authorization": f"Bearer {token}"}, json=body)
        r.raise_for_status()
        return r.json()["result"]["alternatives"][0]["message"]["text"]