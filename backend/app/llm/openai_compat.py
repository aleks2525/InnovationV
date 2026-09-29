"""Универсальный клиент ВСЕХ LLM-провайдеров проекта.

YandexGPT Lite 5 / Pro 5.1, GigaChat 2 Lite/Pro/Max, Qwen3.6 35B-A3B / Qwen3 235B,
gpt-4.1 / gpt-5.6-luna — все доступны через OpenAI-совместимый эндпоинт провайдера
(единый wire-формат /chat/completions). Нативные SDK и обмены IAM/OAuth не используются.
Пер-стадийные base_url/api_key (models_config.yaml / пульт) перекрывают ключи из .env.
"""
import httpx

from app.config import settings

PROVIDER_DEFAULTS = {
    "yandex":    lambda: (settings.yandex_base_url, settings.yandex_api_key),
    "gigachat":  lambda: (settings.gigachat_base_url, settings.gigachat_api_key),
    "qwen":      lambda: (settings.qwen_base_url, settings.qwen_api_key),
    "openai":    lambda: (settings.openai_base_url, settings.openai_api_key),
    "anthropic": lambda: (settings.anthropic_base_url, settings.anthropic_api_key),
    "cohere":    lambda: (settings.cohere_base_url, settings.cohere_api_key),
}


async def chat_completion(model: str, prompt: str, system: str = "",
                          temperature: float = 0.2, max_tokens: int = 3000,
                          provider: str = "qwen",
                          base_url: str | None = None,
                          api_key: str | None = None) -> str:
    get = PROVIDER_DEFAULTS.get(provider)
    if get is None:
        raise RuntimeError(f"Неизвестный провайдер: {provider}")
    d_base, d_key = get()
    base = (base_url or d_base or "").rstrip("/")
    key = api_key or d_key
    if not base or not key:
        raise RuntimeError(f"Провайдер {provider}: не заданы base_url/api_key "
                           f"(models_config.yaml или .env)")
    async with httpx.AsyncClient(timeout=180) as c:
        r = await c.post(f"{base}/chat/completions",
                         headers={"Authorization": f"Bearer {key}"},
                         json={"model": model, "temperature": temperature,
                               "max_tokens": max_tokens,
                               "messages": [{"role": "system", "content": system},
                                            {"role": "user", "content": prompt}]})
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]