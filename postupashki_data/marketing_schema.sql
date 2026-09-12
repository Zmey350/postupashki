-- v2: отдельные сущности в общей БД; v1 таблицы и признаки сохраняются.
-- Новый независимый поток подтверждения фактических затрат на рекламу.
ALTER TABLE collection_periods RENAME TO collection_periods_v1;
CREATE TABLE collection_periods (
    period_id TEXT PRIMARY KEY NOT NULL,
    domain TEXT NOT NULL CHECK(domain IN (
        'acquisition','membership','activity','commerce','participation','offers','ad_costs',
        'promotion_offers','promotion_responses','order_promotions')),
    starts_at TEXT NOT NULL,
    ends_at TEXT NOT NULL CHECK(ends_at > starts_at),
    recorded_at TEXT NOT NULL CHECK(recorded_at >= ends_at)
);
INSERT INTO collection_periods SELECT * FROM collection_periods_v1;
DROP TABLE collection_periods_v1;

CREATE TABLE IF NOT EXISTS channel_snapshots (
    snapshot_id TEXT PRIMARY KEY NOT NULL,
    channel_id TEXT NOT NULL REFERENCES channels(channel_id),
    observed_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL CHECK(recorded_at >= observed_at),
    subscribers INTEGER CHECK(subscribers >= 0),
    typical_post_views_24h INTEGER CHECK(typical_post_views_24h >= 0),
    audience_stage TEXT CHECK(audience_stage IN ('school','university','professional','mixed','unknown')),
    source TEXT NOT NULL CHECK(source IN ('owner_report','manual','public_counter'))
);

CREATE TABLE IF NOT EXISTS placement_metrics (
    metric_id TEXT PRIMARY KEY NOT NULL,
    placement_id TEXT NOT NULL REFERENCES placements(placement_id),
    observed_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL CHECK(recorded_at >= observed_at),
    views INTEGER NOT NULL CHECK(views >= 0),
    source TEXT NOT NULL CHECK(source IN ('owner_report','manual','public_counter'))
);

-- Одна метка на размещение/вход. Ссылка могла быть переслана другим человеком.
CREATE TABLE IF NOT EXISTS tracking_links (
    link_id TEXT PRIMARY KEY NOT NULL,
    source_token TEXT NOT NULL UNIQUE,
    source_channel_id TEXT NOT NULL REFERENCES channels(channel_id),
    placement_id TEXT,
    destination_channel_id TEXT REFERENCES channels(channel_id),
    mechanism TEXT NOT NULL CHECK(mechanism IN ('invite_link','bot_start')),
    created_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL CHECK(recorded_at >= created_at),
    FOREIGN KEY(placement_id,source_channel_id) REFERENCES placements(placement_id,channel_id)
);

CREATE TABLE IF NOT EXISTS promotions (
    promotion_id TEXT PRIMARY KEY NOT NULL,
    title TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('discount','bundle','bonus','warmup','launch')),
    created_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL CHECK(recorded_at >= created_at)
);

-- Версия содержит полные условия; изменение сроков/условий = новая версия.
CREATE TABLE IF NOT EXISTS promotion_versions (
    version_id TEXT PRIMARY KEY NOT NULL,
    promotion_id TEXT NOT NULL REFERENCES promotions(promotion_id),
    effective_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    starts_at TEXT NOT NULL,
    ends_at TEXT NOT NULL CHECK(ends_at > starts_at),
    discount_type TEXT NOT NULL CHECK(discount_type IN ('none','percent','fixed')),
    discount_value INTEGER NOT NULL DEFAULT 0 CHECK(discount_value >= 0),
    promo_code TEXT,
    target_education_stage TEXT CHECK(target_education_stage IN ('school','university','graduate','other','unknown')),
    target_job_search_status TEXT CHECK(target_job_search_status IN ('not_searching','exploring','internship','job','interviewing','unknown')),
    terms TEXT NOT NULL,
    is_cancelled INTEGER NOT NULL DEFAULT 0 CHECK(is_cancelled IN (0,1)),
    CHECK(discount_type != 'none' OR discount_value = 0),
    CHECK(discount_type != 'percent' OR discount_value <= 100)
);

CREATE TABLE IF NOT EXISTS promotion_courses (
    link_id TEXT PRIMARY KEY NOT NULL,
    version_id TEXT NOT NULL REFERENCES promotion_versions(version_id),
    course_id TEXT NOT NULL REFERENCES courses(course_id),
    UNIQUE(version_id,course_id)
);

-- Указываем точную версию, действовавшую при отправке/оформлении заказа.
CREATE TABLE IF NOT EXISTS promotion_offers (
    offer_id TEXT PRIMARY KEY NOT NULL REFERENCES offers(offer_id),
    version_id TEXT NOT NULL REFERENCES promotion_versions(version_id),
    assignment_reason TEXT NOT NULL CHECK(assignment_reason IN ('broadcast','segment','randomized','manual','model')),
    recorded_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS promotion_responses (
    response_id TEXT PRIMARY KEY NOT NULL,
    offer_id TEXT NOT NULL REFERENCES promotion_offers(offer_id),
    kind TEXT NOT NULL CHECK(kind IN ('clicked','declined')),
    occurred_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL CHECK(recorded_at >= occurred_at)
);

-- v2: ровно одна основная акция заказа, без дробной атрибуции и удвоения денег.
CREATE TABLE IF NOT EXISTS order_promotions (
    order_id TEXT PRIMARY KEY NOT NULL REFERENCES orders(order_id),
    version_id TEXT NOT NULL REFERENCES promotion_versions(version_id),
    offer_id TEXT REFERENCES promotion_offers(offer_id),
    evidence TEXT NOT NULL CHECK(evidence IN ('promo_code','offer_checkout','manual_confirmed')),
    recorded_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS placement_promotions (
    placement_id TEXT PRIMARY KEY NOT NULL REFERENCES placements(placement_id),
    promotion_id TEXT NOT NULL REFERENCES promotions(promotion_id),
    recorded_at TEXT NOT NULL
);

-- Численные витрины + метаданные полноты. Никаких обученных моделей.
CREATE TABLE IF NOT EXISTS channel_stats (
    channel_id TEXT NOT NULL,
    as_of TEXT NOT NULL,
    horizon_days INTEGER NOT NULL,
    main_channel_id TEXT NOT NULL,
    topic_id TEXT,
    audience_stage TEXT,
    subscribers INTEGER,
    typical_post_views_24h INTEGER,
    snapshot_age_days REAL,
    placements_count INTEGER NOT NULL,
    days_since_last_placement REAL,
    touch_users INTEGER NOT NULL,
    first_source_users INTEGER NOT NULL,
    returning_touch_users INTEGER NOT NULL,
    mature_users INTEGER NOT NULL,
    commerce_observed_users INTEGER NOT NULL,
    buyers_horizon INTEGER NOT NULL,
    conversion_horizon REAL,
    net_cash_horizon_minor INTEGER,
    cash_per_user_horizon_minor REAL,
    member_observed_users INTEGER,
    left_users INTEGER,
    banned_users INTEGER,
    rejoined_users INTEGER,
    current_members INTEGER,
    current_membership_unknown_users INTEGER,
    retention_eligible_users INTEGER,
    retention_horizon REAL,
    net_cash_to_date_minor INTEGER,
    contribution_to_date_minor INTEGER,
    ad_cost_minor INTEGER,
    cost_per_first_source_user_minor REAL,
    attributed_romi REAL,
    quality_json TEXT NOT NULL,
    PRIMARY KEY(channel_id,as_of,horizon_days,main_channel_id)
);

CREATE TABLE IF NOT EXISTS promotion_stats (
    promotion_id TEXT NOT NULL,
    as_of TEXT NOT NULL,
    horizon_days INTEGER NOT NULL,
    version_id TEXT,
    kind TEXT NOT NULL,
    starts_at TEXT,
    ends_at TEXT,
    is_active INTEGER,
    duration_days REAL,
    days_to_end REAL,
    discount_type TEXT,
    discount_value INTEGER,
    courses_count INTEGER,
    sent_offers INTEGER NOT NULL,
    sent_users INTEGER NOT NULL,
    failed_offers INTEGER NOT NULL,
    clicked_users INTEGER NOT NULL,
    click_rate REAL,
    mature_sent_users INTEGER NOT NULL,
    commerce_observed_users INTEGER NOT NULL,
    buyers_horizon INTEGER NOT NULL,
    conversion_horizon REAL,
    linked_paid_orders INTEGER NOT NULL,
    linked_buyers INTEGER NOT NULL,
    net_cash_minor INTEGER NOT NULL,
    mean_discount_sent_pct REAL,
    repeat_sent_users INTEGER NOT NULL,
    quality_json TEXT NOT NULL,
    PRIMARY KEY(promotion_id,as_of,horizon_days)
);

CREATE INDEX IF NOT EXISTS channel_snapshot_time ON channel_snapshots(channel_id,observed_at,recorded_at);
CREATE INDEX IF NOT EXISTS promotion_version_time ON promotion_versions(promotion_id,effective_at,recorded_at);
CREATE INDEX IF NOT EXISTS promo_response_time ON promotion_responses(offer_id,occurred_at,recorded_at);
