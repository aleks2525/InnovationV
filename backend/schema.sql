-- =====================================================================
-- «Слабые сигналы» — итоговая схема PostgreSQL
-- Применение:  psql -h 127.0.0.1 -U weak_signals -d weak_signals -f schema.sql
-- Совместима с SQLAlchemy-моделями приложения (create_all пропускает существующие таблицы)
-- =====================================================================
BEGIN;

-- ---------------------------------------------------------------------
-- 1. Запросы пользователей (открытый поиск)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS queries (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    text         TEXT         NOT NULL,
    status       VARCHAR(20)  NOT NULL DEFAULT 'running',   -- running | done | error
    stats        JSONB        NOT NULL DEFAULT '{}',        -- processed_sources, candidates,
                                                            -- weak_signals_gt75, excluded, borderline
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
    finished_at  TIMESTAMPTZ
);
COMMENT ON TABLE  queries       IS 'Открытые запросы пользователей и итоги выполнения';
COMMENT ON COLUMN queries.stats IS 'Агрегированная статистика прогона (бонус-метрики ТЗ)';

-- ---------------------------------------------------------------------
-- 2. Найденные источники (все обязательные атрибуты ТЗ)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sources (
    id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    query_id       BIGINT         NOT NULL REFERENCES queries(id) ON DELETE CASCADE,
    url            VARCHAR(1024)  NOT NULL,
    url_hash       CHAR(64)       NOT NULL,                -- sha256(url): дедупликация
    title          TEXT           NOT NULL DEFAULT '',     -- наименование
    snippet        TEXT           NOT NULL DEFAULT '',
    content        TEXT           NOT NULL DEFAULT '',     -- полный текст (кэш воспроизводимости)
    published_date VARCHAR(64),                             -- дата публикации (при наличии)
    language       VARCHAR(8)     NOT NULL DEFAULT '',     -- язык оригинала: ru | en | ...
    source_type    VARCHAR(128)   NOT NULL DEFAULT '',      -- госорган | университет | наука |
                                                            -- патентная база | медиа | пресс-релиз | блог
    trust_level    VARCHAR(160)   NOT NULL DEFAULT 'средняя',
    fetched        BOOLEAN        NOT NULL DEFAULT FALSE,
    created_at     TIMESTAMPTZ    NOT NULL DEFAULT now()
);
COMMENT ON TABLE sources IS 'Источники: наименование, ссылка, дата, тип, язык, доверенность (ТЗ, разд. 2)';

-- ---------------------------------------------------------------------
-- 3. Реестр доверенности источников (ядро методологии «надёжные источники»)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS source_trust_registry (
    domain       VARCHAR(255) PRIMARY KEY,
    trust_level  VARCHAR(20)  NOT NULL,              -- высокая | средняя | пониженная
    source_class VARCHAR(64)  NOT NULL,              -- регулятор | госорган | университет | наука |
                                                     -- патентная база | отраслевое медиа | соцсеть | агрегатор
    comment      TEXT
);
COMMENT ON TABLE source_trust_registry IS
    'Белые/чёрные списки доменов по ТЗ: официальные и научные — высокая доверенность; '
    'соцсети/блоги/агрегаторы/пресс-релизы — только первичный индикатор';

INSERT INTO source_trust_registry (domain, trust_level, source_class, comment) VALUES
    ('europa.eu',         'высокая', 'регулятор',        'Международная организация (ЕС)'),
    ('europol.europa.eu', 'высокая', 'регулятор',        'Europol EC3'),
    ('cbr.ru',            'высокая', 'регулятор',        'Банк России'),
    ('government.ru',     'высокая', 'госорган',         'Правительство РФ'),
    ('nist.gov',          'высокая', 'госорган',         'NIST'),
    ('cftc.gov',          'высокая', 'регулятор',        'CFTC'),
    ('bis.org',           'высокая', 'регулятор',        'Bank for International Settlements'),
    ('imf.org',           'высокая', 'регулятор',        'МВФ'),
    ('rospatent.gov.ru',  'высокая', 'патентная база',   'Госреестр патентов РФ'),
    ('wipo.int',          'высокая', 'патентная база',   'WIPO Patentscope'),
    ('arxiv.org',         'высокая', 'наука',            'Научные препринты'),
    ('nature.com',        'высокая', 'наука',            'Научный журнал'),
    ('science.org',       'высокая', 'наука',            'Научный журнал'),
    ('ieee.org',          'высокая', 'наука',            'Публикации/конференции'),
    ('acm.org',           'высокая', 'наука',            'Публикации/конференции'),
    ('frontiersin.org',   'высокая', 'наука',            'Научный журнал'),
    ('mdpi.com',          'высокая', 'наука',            'Научный журнал'),
    ('reuters.com',       'высокая', 'отраслевое медиа', 'Профессиональное медиа'),
    ('ft.com',            'высокая', 'отраслевое медиа', 'Профессиональное медиа'),
    ('techcrunch.com',    'высокая', 'отраслевое медиа', 'Профессиональное медиа'),
    ('siliconangle.com',  'средняя', 'отраслевое медиа', 'Отраслевое медиа'),
    ('securityweek.com',  'средняя', 'отраслевое медиа', 'Отраслевое медиа ИБ'),
    ('datacenterdynamics.com','средняя','отраслевое медиа','Отраслевое медиа'),
    ('medium.com',        'пониженная', 'соцсеть',       'Личный блог — требуется подтверждение'),
    ('habr.com',          'пониженная', 'соцсеть',       'Блог — требуется подтверждение'),
    ('vc.ru',             'пониженная', 'соцсеть',       'Блог — требуется подтверждение'),
    ('linkedin.com',      'пониженная', 'соцсеть',       'Соцсеть — требуется подтверждение'),
    ('x.com',             'пониженная', 'соцсеть',       'Соцсеть — требуется подтверждение'),
    ('reddit.com',        'пониженная', 'соцсеть',       'Соцсеть — требуется подтверждение'),
    ('t.me',              'пониженная', 'соцсеть',       'Соцсеть — требуется подтверждение'),
    ('awesomeagents.ai',  'пониженная', 'агрегатор',     'Агрегатор — требуется подтверждение'),
    ('cryptodaily.co.uk', 'пониженная', 'агрегатор',     'Агрегатор — требуется подтверждение')
ON CONFLICT (domain) DO NOTHING;

CREATE OR REPLACE FUNCTION fn_source_trust(p_domain TEXT)
RETURNS VARCHAR(20) LANGUAGE sql STABLE AS $$
    SELECT COALESCE((
        SELECT trust_level FROM source_trust_registry
         WHERE lower(p_domain) = domain OR lower(p_domain) LIKE '%.' || domain
         ORDER BY length(domain) DESC LIMIT 1),
    'средняя');
$$;

-- ---------------------------------------------------------------------
-- 4. Кандидаты в слабые сигналы (выдача + интерпретация)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS candidates (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    query_id        BIGINT           NOT NULL REFERENCES queries(id) ON DELETE CASCADE,
    name            TEXT             NOT NULL,               -- оригинальное название
    name_ru         TEXT             NOT NULL DEFAULT '',    -- название по-русски (перевод/транслит)
    description     TEXT             NOT NULL DEFAULT '',
    companies       JSONB            NOT NULL DEFAULT '[]',
    area            TEXT             NOT NULL DEFAULT '',    -- область (ИИ, Роботы, Финтех, ...)
    stage           VARCHAR(128)     NOT NULL DEFAULT '',    -- стадия развития
    trend           VARCHAR(160)     NOT NULL DEFAULT '',    -- тренд упоминаний
    verdict         VARCHAR(20)      NOT NULL DEFAULT '',    -- weak_signal | mature | noise
    final_score     DOUBLE PRECISION NOT NULL DEFAULT 0,     -- итоговая уверенность 0–100
    clf_score       DOUBLE PRECISION,                        -- обученная модель (этап 1)
    llm_score       DOUBLE PRECISION,                        -- основной аудитор (Qwen3 235B)
    judge_score     DOUBLE PRECISION,                        -- второй аудитор (gpt-5.6-luna)
    judge_verdict   VARCHAR(20),
    experts_flag    BOOLEAN          NOT NULL DEFAULT FALSE, -- «требуется проверка экспертом»
    trust_score     DOUBLE PRECISION NOT NULL DEFAULT 0,     -- вес надёжности источников 0–100
    patent_curve    JSONB            NOT NULL DEFAULT '{}',  -- опция: патенты по годам
    analog          TEXT             NOT NULL DEFAULT '',    -- эталонный аналог из датасета (kNN)
    why_weak_signal TEXT             NOT NULL DEFAULT '',    -- объяснение отнесения к сигналу
    why_not_mature  TEXT             NOT NULL DEFAULT '',    -- почему НЕ зрелый тренд / не шум
    advantage       TEXT             NOT NULL DEFAULT '',    -- потенциальное преимущество
    use_case        TEXT             NOT NULL DEFAULT '',    -- кейс-пример
    key_predictors  JSONB            NOT NULL DEFAULT '[]',  -- признаки, по которым определён сигнал
    created_at      TIMESTAMPTZ      NOT NULL DEFAULT now()
);
COMMENT ON COLUMN candidates.key_predictors IS 'Ключевые предикторы: объяснение отнесения к зарождающемуся тренду (ТЗ)';
COMMENT ON COLUMN candidates.patent_curve   IS 'Опциональный количественный предиктор; {} если патентный модуль выключен';

-- ---------------------------------------------------------------------
-- 5. Связь «кандидат ↔ источники» (ссылка на источник обязательна)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS candidate_sources (
    candidate_id BIGINT NOT NULL REFERENCES candidates(id) ON DELETE CASCADE,
    source_id    BIGINT NOT NULL REFERENCES sources(id)    ON DELETE CASCADE,
    PRIMARY KEY (candidate_id, source_id)
);

-- ---------------------------------------------------------------------
-- 6. Лог вызовов моделей (ТЗ 3.1: раскрытие выбора + явное логирование)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS llm_calls (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    request_id  VARCHAR(64)      NOT NULL,
    query_id    BIGINT           REFERENCES queries(id) ON DELETE SET NULL,
    task        VARCHAR(64)      NOT NULL,   -- query_expansion | candidate_extract |
                                             -- deep_analysis | judge_analysis | report_generation
    model       VARCHAR(64)      NOT NULL,   -- только разрешённый ТЗ перечень
    provider    VARCHAR(32)      NOT NULL,   -- yandex | qwen | openai | gigachat
    prompt      TEXT             NOT NULL DEFAULT '',
    response    TEXT             NOT NULL DEFAULT '',
    latency_ms  DOUBLE PRECISION NOT NULL DEFAULT 0,
    status      VARCHAR(16)      NOT NULL DEFAULT 'ok',   -- ok | error
    created_at  TIMESTAMPTZ      NOT NULL DEFAULT now()
);
COMMENT ON TABLE llm_calls IS 'Явное логирование каждого вызова модели; таблица «задача → модель» детерминирована';

-- ---------------------------------------------------------------------
-- 7. Рантайм-конфигурация пульта управления (admin.html) + аудит
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS app_settings (
    key        VARCHAR(128) PRIMARY KEY,      -- 'runtime' | 'audit::<unixtime>'
    value      JSONB        NOT NULL DEFAULT '{}',
    updated_at TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_by VARCHAR(64)  NOT NULL DEFAULT 'admin'
);
COMMENT ON TABLE app_settings IS 'Оверрайд конфигурации из UI (приоритет выше yaml и .env) + журнал изменений';

-- ---------------------------------------------------------------------
-- 8. Экспертная обратная связь (human-in-the-loop → дообучение)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS expert_feedback (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    candidate_id BIGINT      NOT NULL REFERENCES candidates(id) ON DELETE CASCADE,
    agree        BOOLEAN     NOT NULL DEFAULT TRUE,   -- эксперт согласен с вердиктом системы
    comment      TEXT        NOT NULL DEFAULT '',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE expert_feedback IS 'Метки экспертов: agree + вердикт системы → метка дообучения (retrain.py)';

-- ---------------------------------------------------------------------
-- 9. Индексы
-- ---------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS ix_sources_query      ON sources(query_id);
CREATE INDEX IF NOT EXISTS ix_sources_url_hash   ON sources(url_hash);
CREATE INDEX IF NOT EXISTS ix_candidates_query   ON candidates(query_id);
CREATE INDEX IF NOT EXISTS ix_candidates_ranking ON candidates(query_id, verdict, final_score DESC);
CREATE INDEX IF NOT EXISTS ix_llm_calls_query    ON llm_calls(query_id);
CREATE INDEX IF NOT EXISTS ix_llm_calls_created  ON llm_calls(created_at DESC);
CREATE INDEX IF NOT EXISTS ix_queries_created    ON queries(created_at DESC);
CREATE INDEX IF NOT EXISTS ix_feedback_candidate ON expert_feedback(candidate_id);

-- ---------------------------------------------------------------------
-- 10. Представления для отчётов, экспорта и проверки жюри
-- ---------------------------------------------------------------------
-- 10.1 Источники каждого кандидата + флаг «все источники пониженной доверенности»
CREATE OR REPLACE VIEW v_candidate_sources AS
SELECT cs.candidate_id,
       array_agg(s.url   ORDER BY s.id) AS source_urls,
       array_agg(s.title ORDER BY s.id) AS source_titles,
       count(*)                                        AS n_sources,
       bool_or(s.trust_level LIKE 'высокая%')          AS has_high_trust_source,
       (bool_or(s.trust_level LIKE 'пониженная%')
        AND NOT bool_or(s.trust_level NOT LIKE 'пониженная%')) AS only_low_trust
FROM candidate_sources cs
JOIN sources s ON s.id = cs.source_id
GROUP BY cs.candidate_id;

-- 10.2 ТОП-15 по каждому запросу (всегда weak_signal, ранжирование по скорингу)
CREATE OR REPLACE VIEW v_top15 AS
SELECT c.query_id, r.pos, c.id AS candidate_id,
       c.name_ru, c.name, c.area, c.stage, c.trend,
       c.final_score, c.clf_score, c.llm_score, c.judge_score,
       c.trust_score, c.experts_flag, c.analog,
       c.why_weak_signal, c.key_predictors,
       src.source_urls, src.source_titles, src.n_sources,
       src.has_high_trust_source, src.only_low_trust
FROM (
    SELECT c0.id, row_number() OVER (PARTITION BY c0.query_id
                                     ORDER BY c0.final_score DESC) AS pos
    FROM candidates c0
    WHERE c0.verdict = 'weak_signal'
) r
JOIN candidates c ON c.id = r.id
LEFT JOIN v_candidate_sources src ON src.candidate_id = c.id
WHERE r.pos <= 15;

-- 10.3 Использование моделей по запросу (агрегация лога)
CREATE OR REPLACE VIEW v_model_usage AS
SELECT query_id, model, provider,
       count(*)                               AS calls,
       count(*) FILTER (WHERE status <> 'ok') AS errors,
       round(avg(latency_ms))                 AS avg_latency_ms,
       round(sum(latency_ms))                 AS total_latency_ms,
       array_agg(DISTINCT task)               AS tasks
FROM llm_calls
GROUP BY query_id, model, provider;

-- 10.4 Сводка прогона: тайминги, счётчики, качество источников
CREATE OR REPLACE VIEW v_query_summary AS
SELECT q.id, q.text, q.status, q.created_at, q.finished_at,
       round(extract(epoch FROM (q.finished_at - q.created_at))::numeric, 1) AS duration_sec,
       (q.stats->>'processed_sources')::int   AS processed_sources,
       (q.stats->>'candidates')::int          AS candidates,
       (q.stats->>'weak_signals_gt75')::int   AS weak_signals_gt75,
       (q.stats->>'excluded')::int            AS excluded,
       (SELECT count(*) FROM candidates c
         WHERE c.query_id = q.id AND c.verdict = 'weak_signal')     AS weak_total,
       (SELECT count(*) FROM sources s
         WHERE s.query_id = q.id AND s.trust_level LIKE 'высокая%') AS high_trust_sources
FROM queries q;

-- 10.5 Согласие экспертов с системой (human-in-the-loop метрика)
CREATE OR REPLACE VIEW v_feedback_stats AS
SELECT c.query_id,
       count(*)                                              AS feedback_total,
       count(*) FILTER (WHERE f.agree)                       AS agree,
       round(count(*) FILTER (WHERE f.agree)::numeric
             / NULLIF(count(*), 0), 3)                       AS agreement_rate
FROM expert_feedback f
JOIN candidates c ON c.id = f.candidate_id
GROUP BY c.query_id;

-- ---------------------------------------------------------------------
-- 11. ОПЦИОНАЛЬНО: векторное хранилище источников (pgvector).
--     По умолчанию НЕ требуется (InMemoryIndex). Включать только при
--     USE_PGVECTOR=true: сначала `CREATE EXTENSION vector;`
--     (sudo apt install postgresql-16-pgvector), затем раскомментировать.
-- ---------------------------------------------------------------------
-- CREATE EXTENSION IF NOT EXISTS vector;
-- CREATE TABLE IF NOT EXISTS source_embeddings (
--     id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
--     source_id  BIGINT       NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
--     embedding  vector(384)  NOT NULL,
--     created_at TIMESTAMPTZ  NOT NULL DEFAULT now()
-- );
-- CREATE INDEX IF NOT EXISTS ix_source_embeddings_hnsw
--     ON source_embeddings USING hnsw (embedding vector_cosine_ops);

COMMIT;