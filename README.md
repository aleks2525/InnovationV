# 📡 Слабые сигналы

# Аналитическая платформа зарождающихся трендов


Сервис автоматизированного сбора и анализа слабых сигналов в научно-технологических отраслях:
обученная интерпретируемая модель (этап 1) + поиск + аналитический модуль открытого запроса
в реальном времени (этап 2). Находит слабый сигнал до того, как о нём заговорят все.

- Демо-стенд: `https://factrank.ru/signals/`
- Методология и соответствие ТЗ: `https://factrank.ru/signals/about.html`
- Пульт управления конфигурацией: `https://factrank.ru/signals/admin.html` (по админ-токену)
- Интерактивная документация API: `https://factrank.ru/signals/docs` (Swagger UI)

**Возможности**

- **Этап 1**: обученная модель (LogReg + TF-IDF, F1 = 0.88+ на эталоне) с интерпретируемыми признаками
- **Этап 2**: открытый поиск → ТОП-15 слабых сигналов с объяснениями
- **Human-in-the-loop**: 👍/👎 в UI → датасет дообучения → новая версия модели
- **Live-стриминг**: SSE-события показывают работу сервиса в реальном времени
- **Экспорт в Excel**: формат образца экспертов (9 колонок)
- **Пульт управления**: `admin.html` — горячая настройка моделей, компонентов, весов
- **Аудит изменений**: журнал всех изменений конфигурации

---

## 1. Состав решения

**Этап 1 обученная модель.** На датасете организаторов (100 размеченных слабых сигналов,
6 областей) обучена интерпретируемая модель (Logistic Regression + TF-IDF, прозрачные признаки),
отделяющая ранние индикаторы от зрелых технологий и шума. Точность — выше порога ТЗ 75–80%
(отчёт: `backend/models/metrics.json`). Система показывает **топ-5 вкладов признаков** при каждом предсказании.

**Этап 2  открытый поиск.** Запрос в свободной форме → поиск по открытым источникам
(патенты, научные публикации, аналитика, отраслевые медиа) → **ТОП-15 зарождающихся трендов**
(всегда 15 позиций, у каждой — обязательные ссылки на источники) + 5–7 «сомнительных» кандидатов
ниже порога + исключённые зрелые тренды/стандарты/хайп с обоснованием. Для каждого сигнала:
описание, потенциальное преимущество, кейс-пример, источники со всеми атрибутами,
скоринг и объяснение отнесения к зарождающемуся тренду. Просмотр инсайта открывает
страницу-документ (отчёт). Выдача и интерфейс — на русском языке.

## Доступные URL

- **Главная**: `https://factrank.ru/signals/`
- **Пульт управления**: `https://factrank.ru/signals/admin.html`
- **Методология**: `https://factrank.ru/signals/about.html`
- **API документация**: `https://factrank.ru/signals/docs`
- **Лог моделей**: `https://factrank.ru/signals/api/llm-calls`

---



## Используемые модели 

Все модели из разрешённого перечня:



| Стадия                | Модель          | Провайдер |
| --------------------- | --------------- | --------- |
| Расширение запроса    | Qwen3.6 35B-A3B | Qwen      |
| Извлечение кандидатов | Qwen3.6 35B-A3B | Qwen      |
| Глубокий анализ       | gpt-4.1         | OpenAI    |
| Второй аудитор        | GigaChat 2 Max  | GigaChat  |
| Генерация отчётов     | GigaChat 2 Max  | GigaChat  |

Конфигурация: `backend/models_config.yaml` (поддерживает префиксы провайдеров).



## Поисковые провайдеры

Каскад фолбэков (в порядке приоритета):

1. **Tavily** (если ключ задан) — 1000 кредитов/мес бесплатно
2. **Brave** (если ключ задан) — 2000 запросов/мес бесплатно
3. **DuckDuckGo** (всегда работает) — без ключей, без лимитов
4. **Yandex XML** (опционально) — платный

Настройка через пульт `admin.html` или `.env`.



## 2. Архитектура

Схема: `architecture.plantuml`. Разделение логики парсинга, инференса и интерфейса (ТЗ 3.2):

~~~
Запрос (RU/EN) → FastAPI pipeline → PostgreSQL
   ├─ 1. Расширение запроса (LLM)
   ├─ 2. Поиск: Tavily → Brave → DuckDuckGo
   ├─ 3. Загрузка текстов (trafilatura, ретраи)
   ├─ 4. Реранкер (опц., BAAI/bge-reranker-v2-m3)
   ├─ 5. Извлечение кандидатов (Qwen3.6 35B-A3B)
   ├─ 6. Скоринг: классификатор + LLM + судья + доверие
   ├─ 7. Фильтрация зрелых/шума
   └─ 8. Выдача: ТОП-15 + экспорт + инсайт-отчёты
~~~

---

## 3. Технологический стек

| Слой | Технология | Примечание |
|---|---|---|
| Язык / backend | Python 3.11, FastAPI (async) | ТЗ 3.2 |
| СУБД | PostgreSQL 16, SQLAlchemy 2 (asyncpg) | сырые данные + результаты (ТЗ 3.2) |
| ML (этап 1) | scikit-learn (LogReg + TF-IDF) | интерпретируемость; опционально ruBERT (`train_nn.py`) |
| Поиск | DuckDuckGo - основной (опционально Tavily или Brave Search API, arXiv API) | реальный поиск, не «знания» модели |
| Парсинг | httpx, trafilatura, tenacity | отказоустойчивость (ТЗ 8.2) |
| Облачные LLM | **только разрешённый перечень ТЗ 3.1**: YandexGPT Lite 5 / Pro 5.1, Qwen3.6 35B-A3B / Qwen3 235B, GigaChat 2 Lite/Pro/Max, gpt-4.1, gpt-5.6-luna | выбор детерминирован, лог в `llm_calls` |

---

## 4. Структура репозитория

~~~
weak-signals/
├── docker-compose.yml          # Docker-поставка
├── backend/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── schema.sql              # Схема PostgreSQL + реестр доверенности
│   ├── models_config.yaml      # Конфигурация моделей
│   ├── .env.example
│   └── app/
│       ├── main.py
│       ├── config.py
│       ├── db.py
│       ├── schemas.py
│       ├── pipeline.py         # Оркестрация этапа 2
│       ├── core/runtime_config.py
│       ├── llm/                # registry, openai_compat (единый клиент)
│       ├── search/             # web_search, fetch, trust, patents, rerank, vectors
│       ├── classifier/         # features, train, predict, exemplars, retrain
│       └── routers/            # search, admin, feedback
├── frontend/
│   ├── index.html              # Главная + поиск
│   ├── admin.html              # Пульт управления
│   ├── about.html              # Методология
~~~

---

## 5. Быстрый старт

### 5.1 Docker 

~~~bash
git clone <REPO> weak-signals && cd weak-signals
cp backend/.env.example backend/.env && nano backend/.env   # ключи API
docker compose build
# обучение модели (этап 1) — сначала подготовьте data/train_full.csv (см. §7)
docker compose run --rm backend python -m app.classifier.train \
    --data data/train_full.csv --synthesize-negatives
docker compose up -d
# проверка
curl -s http://localhost:8000/api/health
~~~



### 5.2 Без Docker (сервер, домен с префиксом `/signals`)

Ключевые шаги:

~~~bash
sudo apt install -y postgresql python3-venv python3-dev gcc libpq-dev nginx certbot python3-certbot-nginx ufw
sudo -u postgres psql -c "CREATE USER weak_signals WITH PASSWORD '<PASS>';"
sudo -u postgres psql -c "CREATE DATABASE weak_signals OWNER weak_signals;"
PGPASSWORD='<PASS>' psql -h 127.0.0.1 -U weak_signals -d weak_signals -f backend/schema.sql
python3 -m venv venv && ./venv/bin/pip install -r backend/requirements.txt
# backend/.env: DATABASE_URL=...@localhost:5432/weak_signals (без Docker хост = localhost!)
cd backend && ../venv/bin/python -m app.classifier.train --data data/train_full.csv --synthesize-negatives
# systemd: deploy/weak-signals.service; nginx: deploy/nginx-weak-signals.conf (location /trends/)
# префикс в UI: sed -i "s|'/api/|'/trends/api/|g; s|\"/api/|\"/trends/api/|g; s|\"/docs\"|\"docs\"|g" frontend/*.html
sudo certbot --nginx -d factrank.ru
~~~

---

## 6. Конфигурация

Приоритет слоёв: **`.env` → `models_config.yaml` → `app_settings` в PostgreSQL (UI `admin.html`)**.
Изменения из UI применяются горячо (TTL-кэш 5 c) и пишутся в аудит-лог (`GET /api/admin/audit`).

- `models_config.yaml` — отдельный API-доступ на каждую стадию пайплайна
  (`query_expansion`, `candidate_extract`, `deep_analysis`, `judge_analysis`, `report_generation`:
  provider / model / temperature / base_url / api_key / enabled) и флаги компонентов
  (`reranker`, `classifier`, `patents`, `pgvector`).
  
- Профили: `tz` (дефолт, строго разрешённый перечень ТЗ) и `max` (расширенные, для тестов). Переключение: `MODEL_PROFILE` в `.env`.

  

---

## 7. Этап 1: подготовка данных и обучение

Датасет организаторов (Excel) содержит 100 положительных примеров. Конвертация в CSV
с колонкой разметки и обучение:

~~~bash
cd backend
../venv/bin/python - <<'PY'
import pandas as pd
raw = pd.read_excel("data/100_слабых_технологических_сигналов_сентябрь_2026.xlsx", header=None)
hdr = next(i for i in range(len(raw))
           if raw.iloc[i].astype(str).str.contains("Технология", na=False).any())
df = raw.iloc[hdr+1:].copy()
df.columns = [str(c).strip() for c in raw.iloc[hdr]]
df = df.dropna(subset=["Технология (слабый сигнал)"])
df = df.rename(columns={"Технология (слабый сигнал)":"name","Область":"area",
     "Компании":"companies","Почему это слабый сигнал":"reason",
     "Стадия развития":"stage","Тренд упоминаний":"trend","Балл (стадия+тренд)":"score"})
df["is_weak_signal"] = 1
df[["name","area","companies","reason","stage","trend","score","is_weak_signal"]] \
  .to_csv("data/train_full.csv", index=False)
print("строк:", len(df))
PY
../venv/bin/python -m app.classifier.train --data data/train_full.csv --synthesize-negatives
~~~

- `--synthesize-negatives` добавляет демо-зрелые технологии как отрицательный класс
- `class_weight="balanced"` компенсирует дисбаланс 100 vs негативные;
- метрики 5-fold CV → `models/metrics.json` (Precision / Recall / F1, порог ТЗ 75–80%);
- опционально: `python -m app.classifier.train_nn` — fine-tune ruBERT на текстах обоснований.

---

## 8. Методология: как система «понимает», что это слабый сигнал

**Определение:** слабый сигнал = ранняя стадия (концепция → раннее внедрение) + малое число
игроков + скачкообразный рост упоминаний/раундов при низкой базе + отсутствие аналитической
категории и бюджетных строк + подтверждение независимыми источниками.

**Признаки этапа 1** (обоснование отбора — определение ТЗ + лексика разметки методологов ГПБ):

| Признак | Тип | Пример вклада |
|---|---|---|
| `stage` (0–5) | числовой | −0.62 при «массовом внедрении» |
| `trend` (0–3) | числовой | +0.87 («растёт быстро») |
| `n_companies` | числовой | +0.31 (игроков единицы) |
| `kw_weak` | счётчик лексики | +1.18 («стелс», «раунд», «ниша», «почти не обсуждается») |
| `kw_mature` | счётчик лексики | −2.31 («мейнстрим», «лидер», «стандарт») |
| TF-IDF 1–2 граммы | 1500 текстовых | word:раунд +1.42 |

**Эталонная методология в контуре анализа:** обоснования из колонки «Почему это слабый сигнал» эталонного Excel используются тремя способами: примеры в промпте анализа;
kNN-аналог («ближайшее экспертное обоснование, сходство 0.83» в карточке и отчёте);
опциональный fine-tune ruBERT на этих текстах.

**Скоринг этапа 2:**
`final = 0.35·классификатор + 0.30·LLM-аудитор + 0.15·судья + 0.20·доверие источников`
(веса настраиваются в `admin.html`; при отключении компонента перенормируются;
конфликт аудиторов > 30 п.к. → флаг «требует проверки экспертом»).

**Источники:** иерархия доверенности хранится в таблице `source_trust_registry`
(редактируется экспертом): госорганы/регуляторы/университеты/наука/патентные базы — высокая;
соцсети, блоги, агрегаторы, пресс-релизы — **только первичный индикатор** (подтверждение
независимым источником либо отметка о пониженной доверенности; единственный такой источник
не допускает позицию в ТОП-15). Для каждого источника: наименование, ссылка, дата, тип, язык,
доверенность; для зарубежных — русскоязычное резюме с отметкой «сгенерировано автоматически».

**Количественный предиктор (опция):** патентная активность по годам (PatentsView) —
«рост патентной активности: 2024: 6 → 2026: 41».

**Исключение зрелых:** два слоя — обученный классификатор (признаки зрелости) + LLM-критик
по критериям ТЗ (сформированный рынок, выраженные лидеры, устойчивое конкурентное разделение,
отраслевой стандарт, хайп без подтверждений). Обоснование исключения видно в UI и отчёте.

---

## 9. Этап 2: пайплайн открытого поиска

1. Расширение запроса (5–6 подзапросов RU/EN);
2. Поиск (DuckDuckGo, Tavily → Brave-фолбэк → arXiv), дедупликация по `url_hash`;
3. Загрузка полных текстов (ретраи, фолбэк на сниппет), реранкер (опция);
4. Извлечение кандидатов с цитатами доказательств (кандидат без источника в выдачу не попадает);
5. Анализ: вердикт, область, предикторы, аналог методологии;
6. Судья (вторая модель другой семьи) по кандидатам с вердиктом слабого сигнала
7. **Гарантия ТОП-15:** до 3 раундов углубления поиска;
8. Сомнительные 5–7 (ниже порога, без детализации) + исключённые с причинами;
9. Стриминг (SSE): журнал событий и карточки появляются по мере анализа.

Экспорт: `GET /api/queries/{id}/export.xlsx` — лист «ТОП-15» в формате образца экспертов
(№, Технология, Область, Компании, Почему это слабый сигнал, Стадия, Тренд, Балл, Источники)
+ лист «Сомнительные».

---

## 10. API

| Метод | Путь | Назначение |
|---|---|---|
| POST | `/api/search` | полный прогон запроса → ТОП-15, сомнительные, статистика |
| GET | `/api/search/stream?q=` | SSE-стриминг: stage / stats / candidate / done / error |
| POST | `/api/search/ab` | два прогона одной темы разными конфигурациями + diff (абляция) |
| GET | `/api/queries` | история запросов |
| GET | `/api/queries/{id}/report` | протокол прогона: источники, модели, тайминги, методология |
| GET | `/api/queries/{id}/export.xlsx` | выдача в формате образца экспертов |
| GET | `/api/llm-calls` | лог вызовов моделей (ТЗ 3.1) |
| GET | `/api/models` | раскрытие таблицы «задача → модель» |
| POST | `/api/candidates/{id}/feedback` | 👍/ эксперта (human-in-the-loop) |
| GET | `/api/feedback/stats` · `/api/feedback/export.csv` | сводка согласия · датасет дообучения |
| GET/PUT | `/api/admin/settings` · GET `/api/admin/audit` · POST `/api/admin/test` | пульт конфигурации |
| GET | `/api/health`, `/docs`, `/redoc`, `/openapi.json` | здоровье и документация |

---

## 11. Human-in-the-loop и дообучение

Петля: 👍/👎 в UI → таблица `expert_feedback` → `python -m app.classifier.retrain`
(метка эксперта приоритетнее вердикта системы) → версия модели `v<N>` + `registry.json` →
`predict.py` подхватывает горячо (по mtime) → следующие прогоны ранжируют с учётом
`expert_prior` (штраф/бонус за прошлые экспертные вердикты). Откат: `--rollback N`.
Порог накопления: `--min-rows`. Экспертная разметка выгружается: `/api/feedback/export.csv`.

---

## 12. Модели и логирование

Разрешённые облачные API: YandexGPT Lite 5, YandexGPT Pro 5/5.1, Qwen3.6 35B-A3B, Qwen3 235B,
GigaChat 2 Lite/Pro/Max, gpt-4.1, gpt-5.6-luna. 
Выбор модели описан в  детерминированной таблице «задача → модель» (`models_config.yaml`,
`GET /api/models`); каждый вызов логируется в `llm_calls` (request_id, task, model, provider,
prompt, response, latency, status) и виден на `/api/llm-calls`. Итоговая выдача никогда не
формируется только из знаний LLM, каждый сигнал привязан к найденным источникам (`source_ids`).

---

## 13. Артефакты сдачи 

- Промежуточная: репозиторий, `models/metrics.json` (Precision/Recall/F1), базовый сценарий открытого запроса (`POST /api/search`).
- Финальная: web-интерфейс и аналитическая панель,  презентация,
  `GET /api/queries/{id}/report`, `export.xlsx`, лог моделей, страница методологии.



---

## 15. Команда и лицензия

Команда «Innovation V»
Репозиторий открыт для жюри; коммерческое и другое использование только  по с командой и с Газпромбанк.Тех.