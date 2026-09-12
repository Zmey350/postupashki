PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_meta (
    version INTEGER PRIMARY KEY CHECK(version = 1),
    description TEXT NOT NULL
);
INSERT OR IGNORE INTO schema_meta VALUES(1, 'postupashki-data-v1; money=RUB kopecks; time=UTC');

CREATE TABLE IF NOT EXISTS topics (
    topic_id TEXT PRIMARY KEY NOT NULL,
    title TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS channels (
    channel_id TEXT PRIMARY KEY NOT NULL,
    title TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('own_main','own_course','external')),
    topic_id TEXT REFERENCES topics(topic_id),
    created_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL CHECK(recorded_at >= created_at)
);

CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY CHECK(user_id > 0),
    first_seen_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL CHECK(recorded_at >= first_seen_at)
);

-- Обновление одного ответа не стирает остальные; храним историю ответов.
CREATE TABLE IF NOT EXISTS profile_events (
    event_id TEXT PRIMARY KEY NOT NULL,
    user_id INTEGER NOT NULL REFERENCES users(user_id),
    field TEXT NOT NULL CHECK(field IN ('education_stage','job_search_status')),
    value TEXT NOT NULL,
    source TEXT NOT NULL CHECK(source IN ('bot_form','event_registration','manual_confirmed')),
    occurred_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL CHECK(recorded_at >= occurred_at),
    CHECK((field = 'education_stage' AND value IN ('school','university','graduate','other','unknown'))
       OR (field = 'job_search_status' AND value IN ('not_searching','exploring','internship','job','interviewing','unknown')))
);

CREATE TABLE IF NOT EXISTS courses (
    course_id TEXT PRIMARY KEY NOT NULL,
    topic_id TEXT NOT NULL REFERENCES topics(topic_id),
    title TEXT NOT NULL,
    created_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL CHECK(recorded_at >= created_at)
);

-- Новая цена или перенос старта = новая версия, не UPDATE предыдущей.
CREATE TABLE IF NOT EXISTS course_versions (
    version_id TEXT PRIMARY KEY NOT NULL,
    course_id TEXT NOT NULL REFERENCES courses(course_id),
    effective_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    sales_open_at TEXT NOT NULL,
    sales_close_at TEXT NOT NULL,
    starts_at TEXT NOT NULL,
    regular_price_minor INTEGER NOT NULL CHECK(regular_price_minor >= 0),
    variable_cost_minor INTEGER CHECK(variable_cost_minor >= 0),
    currency TEXT NOT NULL DEFAULT 'RUB' CHECK(currency = 'RUB'),
    is_cancelled INTEGER NOT NULL DEFAULT 0 CHECK(is_cancelled IN (0,1)),
    CHECK(sales_close_at >= sales_open_at)
);

CREATE TABLE IF NOT EXISTS placements (
    placement_id TEXT PRIMARY KEY NOT NULL,
    channel_id TEXT NOT NULL REFERENCES channels(channel_id),
    campaign_id TEXT,
    creative_id TEXT,
    published_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    quoted_cost_minor INTEGER CHECK(quoted_cost_minor >= 0),
    currency TEXT NOT NULL DEFAULT 'RUB' CHECK(currency = 'RUB'),
    advertised_course_id TEXT REFERENCES courses(course_id),
    UNIQUE(placement_id, channel_id)
);

-- Фактические расходы и возвраты не заменяют первоначальную котировку.
CREATE TABLE IF NOT EXISTS placement_cost_events (
    cost_event_id TEXT PRIMARY KEY NOT NULL,
    placement_id TEXT NOT NULL REFERENCES placements(placement_id),
    kind TEXT NOT NULL CHECK(kind IN ('expense','refund')),
    amount_minor INTEGER NOT NULL CHECK(amount_minor > 0),
    currency TEXT NOT NULL DEFAULT 'RUB' CHECK(currency='RUB'),
    occurred_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL CHECK(recorded_at >= occurred_at)
);

CREATE TABLE IF NOT EXISTS acquisition_events (
    event_id TEXT PRIMARY KEY NOT NULL,
    user_id INTEGER NOT NULL REFERENCES users(user_id),
    source_channel_id TEXT REFERENCES channels(channel_id),
    placement_id TEXT,
    mechanism TEXT NOT NULL CHECK(mechanism IN ('invite_link','bot_start','unknown')),
    source_token TEXT,
    occurred_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL CHECK(recorded_at >= occurred_at),
    CHECK(placement_id IS NULL OR source_channel_id IS NOT NULL),
    CHECK(mechanism != 'unknown' OR (source_channel_id IS NULL AND placement_id IS NULL)),
    FOREIGN KEY(placement_id, source_channel_id) REFERENCES placements(placement_id, channel_id)
);

CREATE TABLE IF NOT EXISTS membership_events (
    event_id TEXT PRIMARY KEY NOT NULL,
    user_id INTEGER NOT NULL REFERENCES users(user_id),
    channel_id TEXT NOT NULL REFERENCES channels(channel_id),
    status TEXT NOT NULL CHECK(status IN ('joined','left','banned')),
    evidence_kind TEXT NOT NULL DEFAULT 'status_update' CHECK(evidence_kind IN ('status_update','status_check')),
    occurred_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL CHECK(recorded_at >= occurred_at)
);

-- Только действия, которые фиксирует наш бот; никаких «прочитал пост».
CREATE TABLE IF NOT EXISTS activity_events (
    event_id TEXT PRIMARY KEY NOT NULL,
    user_id INTEGER NOT NULL REFERENCES users(user_id),
    event_type TEXT NOT NULL CHECK(event_type IN (
        'bot_started','topic_selected','course_program_requested','price_requested',
        'waitlist_joined','trial_started','trial_completed')),
    topic_id TEXT REFERENCES topics(topic_id),
    course_id TEXT REFERENCES courses(course_id),
    occurred_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL CHECK(recorded_at >= occurred_at),
    CHECK(event_type != 'topic_selected' OR topic_id IS NOT NULL),
    CHECK(event_type NOT IN ('course_program_requested','price_requested','waitlist_joined','trial_started','trial_completed')
          OR course_id IS NOT NULL)
);

CREATE TABLE IF NOT EXISTS hackathons (
    hackathon_id TEXT PRIMARY KEY NOT NULL,
    topic_id TEXT NOT NULL REFERENCES topics(topic_id),
    title TEXT NOT NULL,
    starts_at TEXT NOT NULL,
    ends_at TEXT NOT NULL CHECK(ends_at >= starts_at),
    recorded_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS participation_events (
    event_id TEXT PRIMARY KEY NOT NULL,
    user_id INTEGER NOT NULL REFERENCES users(user_id),
    hackathon_id TEXT NOT NULL REFERENCES hackathons(hackathon_id),
    status TEXT NOT NULL CHECK(status IN ('registered','attended','solution_submitted')),
    occurred_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL CHECK(recorded_at >= occurred_at)
);

CREATE TABLE IF NOT EXISTS orders (
    order_id TEXT PRIMARY KEY NOT NULL,
    user_id INTEGER NOT NULL REFERENCES users(user_id),
    ordered_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL CHECK(recorded_at >= ordered_at),
    total_minor INTEGER NOT NULL CHECK(total_minor >= 0),
    currency TEXT NOT NULL DEFAULT 'RUB' CHECK(currency = 'RUB')
);

CREATE TABLE IF NOT EXISTS order_items (
    item_id TEXT PRIMARY KEY NOT NULL,
    order_id TEXT NOT NULL REFERENCES orders(order_id),
    course_id TEXT NOT NULL REFERENCES courses(course_id),
    quantity INTEGER NOT NULL DEFAULT 1 CHECK(quantity > 0),
    line_total_minor INTEGER NOT NULL CHECK(line_total_minor >= 0),
    variable_cost_minor INTEGER CHECK(variable_cost_minor >= 0)
);

CREATE TABLE IF NOT EXISTS payments (
    payment_id TEXT PRIMARY KEY NOT NULL,
    order_id TEXT NOT NULL REFERENCES orders(order_id),
    kind TEXT NOT NULL CHECK(kind IN ('payment','refund')),
    amount_minor INTEGER NOT NULL CHECK(amount_minor > 0),
    fee_minor INTEGER CHECK(fee_minor >= 0),
    occurred_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL CHECK(recorded_at >= occurred_at)
);

CREATE TABLE IF NOT EXISTS offers (
    offer_id TEXT PRIMARY KEY NOT NULL,
    user_id INTEGER NOT NULL REFERENCES users(user_id),
    course_id TEXT NOT NULL REFERENCES courses(course_id),
    regular_price_minor INTEGER NOT NULL CHECK(regular_price_minor > 0),
    offered_price_minor INTEGER NOT NULL CHECK(offered_price_minor >= 0),
    currency TEXT NOT NULL DEFAULT 'RUB' CHECK(currency = 'RUB'),
    status TEXT NOT NULL CHECK(status IN ('sent','failed')),
    sent_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL CHECK(recorded_at >= sent_at),
    valid_until TEXT NOT NULL CHECK(valid_until > sent_at),
    promo_code TEXT,
    experiment_id TEXT,
    experiment_group TEXT
);

-- Коллектор подтверждает только уже успешно собранные интервалы.
-- Интервалы не заполняют простой автоматически и не обещают будущий сбор.
CREATE TABLE IF NOT EXISTS collection_periods (
    period_id TEXT PRIMARY KEY NOT NULL,
    domain TEXT NOT NULL CHECK(domain IN (
        'acquisition','membership','activity','commerce','participation','offers')),
    starts_at TEXT NOT NULL,
    ends_at TEXT NOT NULL CHECK(ends_at > starts_at),
    recorded_at TEXT NOT NULL CHECK(recorded_at >= ends_at)
);

CREATE TABLE IF NOT EXISTS feature_definitions (
    position INTEGER PRIMARY KEY CHECK(position BETWEEN 1 AND 20),
    name TEXT NOT NULL UNIQUE,
    title_ru TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('categorical','numeric')),
    definition TEXT NOT NULL,
    useful_for TEXT NOT NULL,
    missing_rule TEXT NOT NULL
);

-- Ключи и quality_json -- метаданные, не дополнительные ML-признаки.
CREATE TABLE IF NOT EXISTS user_features (
    user_id INTEGER NOT NULL REFERENCES users(user_id),
    as_of TEXT NOT NULL,
    feature_version TEXT NOT NULL DEFAULT 'v1',
    first_source_channel_id TEXT,
    education_stage TEXT,
    job_search_status TEXT,
    primary_topic_id TEXT,
    days_since_first_seen REAL,
    is_main_channel_member INTEGER CHECK(is_main_channel_member IN (0,1)),
    days_since_last_activity REAL,
    active_days_30d INTEGER CHECK(active_days_30d >= 0),
    orders_count_90d INTEGER CHECK(orders_count_90d >= 0),
    net_spend_90d_minor INTEGER,
    mean_order_value_90d_minor REAL,
    days_since_last_purchase REAL,
    mean_purchase_gap_days REAL,
    paid_courses_count INTEGER CHECK(paid_courses_count >= 0),
    hackathons_attended_180d INTEGER CHECK(hackathons_attended_180d >= 0),
    course_intent_actions_30d INTEGER CHECK(course_intent_actions_30d >= 0),
    offers_sent_30d INTEGER CHECK(offers_sent_30d >= 0),
    active_offer_discount_pct REAL,
    calendar_month INTEGER NOT NULL CHECK(calendar_month BETWEEN 1 AND 12),
    days_to_next_relevant_course_start REAL,
    quality_json TEXT NOT NULL,
    built_at TEXT NOT NULL,
    PRIMARY KEY(user_id, as_of, feature_version)
);

CREATE INDEX IF NOT EXISTS profile_user_time ON profile_events(user_id,occurred_at,recorded_at);
CREATE INDEX IF NOT EXISTS acquisition_user_time ON acquisition_events(user_id,occurred_at,recorded_at);
CREATE INDEX IF NOT EXISTS membership_user_time ON membership_events(user_id,channel_id,occurred_at);
CREATE INDEX IF NOT EXISTS activity_user_time ON activity_events(user_id,occurred_at,recorded_at);
CREATE INDEX IF NOT EXISTS participation_user_time ON participation_events(user_id,occurred_at);
CREATE INDEX IF NOT EXISTS order_user_time ON orders(user_id,ordered_at,recorded_at);
CREATE INDEX IF NOT EXISTS payments_order_time ON payments(order_id,occurred_at,recorded_at);
CREATE INDEX IF NOT EXISTS items_order ON order_items(order_id);
CREATE INDEX IF NOT EXISTS offers_user_time ON offers(user_id,sent_at,recorded_at);
CREATE INDEX IF NOT EXISTS course_version_time ON course_versions(course_id,effective_at,recorded_at);
CREATE INDEX IF NOT EXISTS feature_date ON user_features(as_of,feature_version);
