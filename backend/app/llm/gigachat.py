"""Клиент GigaChat 2 Lite/Pro/Max (разрешены ТЗ 3.1) через официальную библиотеку gigachat.

Библиотека синхронная — вызов выносится в отдельный поток (asyncio.to_thread),
чтобы не блокировать event loop FastAPI. Импорт отложен: сервис стартует без ключей.
"""
import asyncio

from app.config import settings

MODEL_NAMES = {"gigachat-lite": "GigaChat Lite",
               "gigachat-pro":  "GigaChat Pro",
               "gigachat-max":  "GigaChat Max"}


async def gigachat_completion(model: str, prompt: str, system: str = "",
                              temperature: float = 0.2, max_tokens: int = 3000) -> str:
    from gigachat import GigaChat

    def _call() -> str:
        with GigaChat(credentials=settings.gigachat_client_id,
                      password=settings.gigachat_client_secret,
                      scope=settings.gigachat_scope,
                      verify_ssl=False) as g:
            r = g.chat([{"role": "system", "content": system},
                        {"role": "user", "content": prompt}],
                       model=MODEL_NAMES.get(model, "GigaChat Pro"),
                       temperature=temperature, max_tokens=max_tokens)
        return r.choices[0].message.content

    return await asyncio.to_thread(_call)