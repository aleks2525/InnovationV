"""Конфигурация приложения: окружение + .env (pydantic-settings).

Все LLM-провайдеры (YandexGPT, GigaChat, Qwen, OpenAI, …) доступны через
OpenAI-совместимые эндпоинты провайдеров — нативные SDK и обмены IAM/OAuth не используются.
Локальных torch/transformers на стенде нет: реранкер и эмбеддинги — через API.
"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Инфраструктура ---
    database_url: str = "postgresql+asyncpg://weak_signals:weak_signals@localhost:5432/weak_signals"
    static_dir: str = "../frontend"            # index.html, admin.html, about.html
    model_dir: str = "./models"                # артефакты классификатора + registry.json
    models_config_path: str = "./models_config.yaml"
    exemplars_path: str = "data/100_слабых_технологических_сигналов_сентябрь_2026.xlsx"
    admin_token: str = "change-me"             # доступ к /api/admin и admin.html

    # --- Поиск по открытым источникам ---
    search_provider: str = "tavily"            # tavily | brave (второй с ключом = автофолбэк)
    tavily_api_key: str = ""
    brave_api_key: str = ""
    patentsview_api_key: str = ""              # опция: патентная кривая по годам
    results_per_subquery: int = 6
    max_fetch_sources: int = 12

    # --- LLM-провайдеры: ВСЕ через OpenAI-совместимые эндпоинты (перечень ТЗ 3.1) ---
    model_profile: str = "tz"                  # tz | max (max — после согласования с ГПБ.Тех)
    yandex_base_url: str = ""                  # YandexGPT Lite 5 / Pro 5.1
    yandex_api_key: str = ""
    gigachat_base_url: str = ""                # GigaChat 2 Lite/Pro/Max
    gigachat_api_key: str = ""
    qwen_base_url: str = ""                    # Qwen3.6 35B-A3B / Qwen3 235B
    qwen_api_key: str = ""
    qwen_model: str = "qwen3-235b"
    qwen_small_model: str = "qwen3.6-35b-a3b"
    openai_base_url: str = ""                  # gpt-4.1 / gpt-5.6-luna
    openai_api_key: str = ""
    openai_model: str = "gpt-5.6-luna"
    anthropic_base_url: str = ""               # только профиль max после согласования
    anthropic_api_key: str = ""
    cohere_base_url: str = ""                  # API-реранкер (опция)
    cohere_api_key: str = ""

    # --- Реранкер и эмбеддинги через API провайдера ---
    reranker_base_url: str = ""
    reranker_api_key: str = ""
    reranker_model: str = "BAAI/bge-reranker-v2-m3"   # или Qwen/Qwen3-Reranker-0.6B и др.
    reranker_format: str = "tei"               # tei | cohere
    embeddings_base_url: str = ""
    embeddings_api_key: str = ""
    embeddings_model: str = "BAAI/bge-m3"

    # --- Опциональные модули ---
    use_pgvector: bool = False                 # векторы в PostgreSQL (по умолчанию не нужны)
    use_sonar: bool = False                    # Perplexity Sonar — только после согласования
    perplexity_api_key: str = ""


settings = Settings()