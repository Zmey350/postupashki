# Схема базы данных v2

Исполняемые источники: `postupashki_data/schema.sql` (v1) и `postupashki_data/marketing_schema.sql` (миграция v2). Применять через `initialize` / `init`. Межтабличные проверки — `db.validate` и `marketing_validation.validate_marketing`.

Даты — UTC ISO 8601. Суммы *_minor — копейки RUB. Смысл полей и ограничения статистики: `features.md` и `marketing.md`.

## topics

| Поле | Тип | Обязательное | По умолчанию | Ключ |
|---|---|---|---|---|
| `topic_id` | TEXT | Да | — | PK |
| `title` | TEXT | Да | — | — |

## channels

| Поле | Тип | Обязательное | По умолчанию | Ключ |
|---|---|---|---|---|
| `channel_id` | TEXT | Да | — | PK |
| `title` | TEXT | Да | — | — |
| `kind` | TEXT | Да | — | — |
| `topic_id` | TEXT | Нет | — | — |
| `created_at` | TEXT | Да | — | — |
| `recorded_at` | TEXT | Да | — | — |

Внешние ключи: `topic_id` → `topics.topic_id`.

## users

| Поле | Тип | Обязательное | По умолчанию | Ключ |
|---|---|---|---|---|
| `user_id` | INTEGER | Да | — | PK |
| `first_seen_at` | TEXT | Да | — | — |
| `recorded_at` | TEXT | Да | — | — |

## courses

| Поле | Тип | Обязательное | По умолчанию | Ключ |
|---|---|---|---|---|
| `course_id` | TEXT | Да | — | PK |
| `topic_id` | TEXT | Да | — | — |
| `title` | TEXT | Да | — | — |
| `created_at` | TEXT | Да | — | — |
| `recorded_at` | TEXT | Да | — | — |

Внешние ключи: `topic_id` → `topics.topic_id`.

## course_versions

| Поле | Тип | Обязательное | По умолчанию | Ключ |
|---|---|---|---|---|
| `version_id` | TEXT | Да | — | PK |
| `course_id` | TEXT | Да | — | — |
| `effective_at` | TEXT | Да | — | — |
| `recorded_at` | TEXT | Да | — | — |
| `sales_open_at` | TEXT | Да | — | — |
| `sales_close_at` | TEXT | Да | — | — |
| `starts_at` | TEXT | Да | — | — |
| `regular_price_minor` | INTEGER | Да | — | — |
| `variable_cost_minor` | INTEGER | Нет | — | — |
| `currency` | TEXT | Да | 'RUB' | — |
| `is_cancelled` | INTEGER | Да | 0 | — |

Внешние ключи: `course_id` → `courses.course_id`.

## placements

| Поле | Тип | Обязательное | По умолчанию | Ключ |
|---|---|---|---|---|
| `placement_id` | TEXT | Да | — | PK |
| `channel_id` | TEXT | Да | — | — |
| `campaign_id` | TEXT | Нет | — | — |
| `creative_id` | TEXT | Нет | — | — |
| `published_at` | TEXT | Да | — | — |
| `recorded_at` | TEXT | Да | — | — |
| `quoted_cost_minor` | INTEGER | Нет | — | — |
| `currency` | TEXT | Да | 'RUB' | — |
| `advertised_course_id` | TEXT | Нет | — | — |

Внешние ключи: `advertised_course_id` → `courses.course_id`, `channel_id` → `channels.channel_id`.

## placement_cost_events

| Поле | Тип | Обязательное | По умолчанию | Ключ |
|---|---|---|---|---|
| `cost_event_id` | TEXT | Да | — | PK |
| `placement_id` | TEXT | Да | — | — |
| `kind` | TEXT | Да | — | — |
| `amount_minor` | INTEGER | Да | — | — |
| `currency` | TEXT | Да | 'RUB' | — |
| `occurred_at` | TEXT | Да | — | — |
| `recorded_at` | TEXT | Да | — | — |

Внешние ключи: `placement_id` → `placements.placement_id`.

## profile_events

| Поле | Тип | Обязательное | По умолчанию | Ключ |
|---|---|---|---|---|
| `event_id` | TEXT | Да | — | PK |
| `user_id` | INTEGER | Да | — | — |
| `field` | TEXT | Да | — | — |
| `value` | TEXT | Да | — | — |
| `source` | TEXT | Да | — | — |
| `occurred_at` | TEXT | Да | — | — |
| `recorded_at` | TEXT | Да | — | — |

Внешние ключи: `user_id` → `users.user_id`.

## acquisition_events

| Поле | Тип | Обязательное | По умолчанию | Ключ |
|---|---|---|---|---|
| `event_id` | TEXT | Да | — | PK |
| `user_id` | INTEGER | Да | — | — |
| `source_channel_id` | TEXT | Нет | — | — |
| `placement_id` | TEXT | Нет | — | — |
| `mechanism` | TEXT | Да | — | — |
| `source_token` | TEXT | Нет | — | — |
| `occurred_at` | TEXT | Да | — | — |
| `recorded_at` | TEXT | Да | — | — |

Внешние ключи: `placement_id` → `placements.placement_id`, `source_channel_id` → `placements.channel_id`, `source_channel_id` → `channels.channel_id`, `user_id` → `users.user_id`.

## membership_events

| Поле | Тип | Обязательное | По умолчанию | Ключ |
|---|---|---|---|---|
| `event_id` | TEXT | Да | — | PK |
| `user_id` | INTEGER | Да | — | — |
| `channel_id` | TEXT | Да | — | — |
| `status` | TEXT | Да | — | — |
| `evidence_kind` | TEXT | Да | 'status_update' | — |
| `occurred_at` | TEXT | Да | — | — |
| `recorded_at` | TEXT | Да | — | — |

Внешние ключи: `channel_id` → `channels.channel_id`, `user_id` → `users.user_id`.

## activity_events

| Поле | Тип | Обязательное | По умолчанию | Ключ |
|---|---|---|---|---|
| `event_id` | TEXT | Да | — | PK |
| `user_id` | INTEGER | Да | — | — |
| `event_type` | TEXT | Да | — | — |
| `topic_id` | TEXT | Нет | — | — |
| `course_id` | TEXT | Нет | — | — |
| `occurred_at` | TEXT | Да | — | — |
| `recorded_at` | TEXT | Да | — | — |

Внешние ключи: `course_id` → `courses.course_id`, `topic_id` → `topics.topic_id`, `user_id` → `users.user_id`.

## hackathons

| Поле | Тип | Обязательное | По умолчанию | Ключ |
|---|---|---|---|---|
| `hackathon_id` | TEXT | Да | — | PK |
| `topic_id` | TEXT | Да | — | — |
| `title` | TEXT | Да | — | — |
| `starts_at` | TEXT | Да | — | — |
| `ends_at` | TEXT | Да | — | — |
| `recorded_at` | TEXT | Да | — | — |

Внешние ключи: `topic_id` → `topics.topic_id`.

## participation_events

| Поле | Тип | Обязательное | По умолчанию | Ключ |
|---|---|---|---|---|
| `event_id` | TEXT | Да | — | PK |
| `user_id` | INTEGER | Да | — | — |
| `hackathon_id` | TEXT | Да | — | — |
| `status` | TEXT | Да | — | — |
| `occurred_at` | TEXT | Да | — | — |
| `recorded_at` | TEXT | Да | — | — |

Внешние ключи: `hackathon_id` → `hackathons.hackathon_id`, `user_id` → `users.user_id`.

## orders

| Поле | Тип | Обязательное | По умолчанию | Ключ |
|---|---|---|---|---|
| `order_id` | TEXT | Да | — | PK |
| `user_id` | INTEGER | Да | — | — |
| `ordered_at` | TEXT | Да | — | — |
| `recorded_at` | TEXT | Да | — | — |
| `total_minor` | INTEGER | Да | — | — |
| `currency` | TEXT | Да | 'RUB' | — |

Внешние ключи: `user_id` → `users.user_id`.

## order_items

| Поле | Тип | Обязательное | По умолчанию | Ключ |
|---|---|---|---|---|
| `item_id` | TEXT | Да | — | PK |
| `order_id` | TEXT | Да | — | — |
| `course_id` | TEXT | Да | — | — |
| `quantity` | INTEGER | Да | 1 | — |
| `line_total_minor` | INTEGER | Да | — | — |
| `variable_cost_minor` | INTEGER | Нет | — | — |

Внешние ключи: `course_id` → `courses.course_id`, `order_id` → `orders.order_id`.

## payments

| Поле | Тип | Обязательное | По умолчанию | Ключ |
|---|---|---|---|---|
| `payment_id` | TEXT | Да | — | PK |
| `order_id` | TEXT | Да | — | — |
| `kind` | TEXT | Да | — | — |
| `amount_minor` | INTEGER | Да | — | — |
| `fee_minor` | INTEGER | Нет | — | — |
| `occurred_at` | TEXT | Да | — | — |
| `recorded_at` | TEXT | Да | — | — |

Внешние ключи: `order_id` → `orders.order_id`.

## offers

| Поле | Тип | Обязательное | По умолчанию | Ключ |
|---|---|---|---|---|
| `offer_id` | TEXT | Да | — | PK |
| `user_id` | INTEGER | Да | — | — |
| `course_id` | TEXT | Да | — | — |
| `regular_price_minor` | INTEGER | Да | — | — |
| `offered_price_minor` | INTEGER | Да | — | — |
| `currency` | TEXT | Да | 'RUB' | — |
| `status` | TEXT | Да | — | — |
| `sent_at` | TEXT | Да | — | — |
| `recorded_at` | TEXT | Да | — | — |
| `valid_until` | TEXT | Да | — | — |
| `promo_code` | TEXT | Нет | — | — |
| `experiment_id` | TEXT | Нет | — | — |
| `experiment_group` | TEXT | Нет | — | — |

Внешние ключи: `course_id` → `courses.course_id`, `user_id` → `users.user_id`.

## collection_periods

| Поле | Тип | Обязательное | По умолчанию | Ключ |
|---|---|---|---|---|
| `period_id` | TEXT | Да | — | PK |
| `domain` | TEXT | Да | — | — |
| `starts_at` | TEXT | Да | — | — |
| `ends_at` | TEXT | Да | — | — |
| `recorded_at` | TEXT | Да | — | — |

## channel_snapshots

| Поле | Тип | Обязательное | По умолчанию | Ключ |
|---|---|---|---|---|
| `snapshot_id` | TEXT | Да | — | PK |
| `channel_id` | TEXT | Да | — | — |
| `observed_at` | TEXT | Да | — | — |
| `recorded_at` | TEXT | Да | — | — |
| `subscribers` | INTEGER | Нет | — | — |
| `typical_post_views_24h` | INTEGER | Нет | — | — |
| `audience_stage` | TEXT | Нет | — | — |
| `source` | TEXT | Да | — | — |

Внешние ключи: `channel_id` → `channels.channel_id`.

## placement_metrics

| Поле | Тип | Обязательное | По умолчанию | Ключ |
|---|---|---|---|---|
| `metric_id` | TEXT | Да | — | PK |
| `placement_id` | TEXT | Да | — | — |
| `observed_at` | TEXT | Да | — | — |
| `recorded_at` | TEXT | Да | — | — |
| `views` | INTEGER | Да | — | — |
| `source` | TEXT | Да | — | — |

Внешние ключи: `placement_id` → `placements.placement_id`.

## tracking_links

| Поле | Тип | Обязательное | По умолчанию | Ключ |
|---|---|---|---|---|
| `link_id` | TEXT | Да | — | PK |
| `source_token` | TEXT | Да | — | — |
| `source_channel_id` | TEXT | Да | — | — |
| `placement_id` | TEXT | Нет | — | — |
| `destination_channel_id` | TEXT | Нет | — | — |
| `mechanism` | TEXT | Да | — | — |
| `created_at` | TEXT | Да | — | — |
| `recorded_at` | TEXT | Да | — | — |

Внешние ключи: `placement_id` → `placements.placement_id`, `source_channel_id` → `placements.channel_id`, `destination_channel_id` → `channels.channel_id`, `source_channel_id` → `channels.channel_id`.

## promotions

| Поле | Тип | Обязательное | По умолчанию | Ключ |
|---|---|---|---|---|
| `promotion_id` | TEXT | Да | — | PK |
| `title` | TEXT | Да | — | — |
| `kind` | TEXT | Да | — | — |
| `created_at` | TEXT | Да | — | — |
| `recorded_at` | TEXT | Да | — | — |

## promotion_versions

| Поле | Тип | Обязательное | По умолчанию | Ключ |
|---|---|---|---|---|
| `version_id` | TEXT | Да | — | PK |
| `promotion_id` | TEXT | Да | — | — |
| `effective_at` | TEXT | Да | — | — |
| `recorded_at` | TEXT | Да | — | — |
| `starts_at` | TEXT | Да | — | — |
| `ends_at` | TEXT | Да | — | — |
| `discount_type` | TEXT | Да | — | — |
| `discount_value` | INTEGER | Да | 0 | — |
| `promo_code` | TEXT | Нет | — | — |
| `target_education_stage` | TEXT | Нет | — | — |
| `target_job_search_status` | TEXT | Нет | — | — |
| `terms` | TEXT | Да | — | — |
| `is_cancelled` | INTEGER | Да | 0 | — |

Внешние ключи: `promotion_id` → `promotions.promotion_id`.

## promotion_courses

| Поле | Тип | Обязательное | По умолчанию | Ключ |
|---|---|---|---|---|
| `link_id` | TEXT | Да | — | PK |
| `version_id` | TEXT | Да | — | — |
| `course_id` | TEXT | Да | — | — |

Внешние ключи: `course_id` → `courses.course_id`, `version_id` → `promotion_versions.version_id`.

## promotion_offers

| Поле | Тип | Обязательное | По умолчанию | Ключ |
|---|---|---|---|---|
| `offer_id` | TEXT | Да | — | PK |
| `version_id` | TEXT | Да | — | — |
| `assignment_reason` | TEXT | Да | — | — |
| `recorded_at` | TEXT | Да | — | — |

Внешние ключи: `version_id` → `promotion_versions.version_id`, `offer_id` → `offers.offer_id`.

## promotion_responses

| Поле | Тип | Обязательное | По умолчанию | Ключ |
|---|---|---|---|---|
| `response_id` | TEXT | Да | — | PK |
| `offer_id` | TEXT | Да | — | — |
| `kind` | TEXT | Да | — | — |
| `occurred_at` | TEXT | Да | — | — |
| `recorded_at` | TEXT | Да | — | — |

Внешние ключи: `offer_id` → `promotion_offers.offer_id`.

## order_promotions

| Поле | Тип | Обязательное | По умолчанию | Ключ |
|---|---|---|---|---|
| `order_id` | TEXT | Да | — | PK |
| `version_id` | TEXT | Да | — | — |
| `offer_id` | TEXT | Нет | — | — |
| `evidence` | TEXT | Да | — | — |
| `recorded_at` | TEXT | Да | — | — |

Внешние ключи: `offer_id` → `promotion_offers.offer_id`, `version_id` → `promotion_versions.version_id`, `order_id` → `orders.order_id`.

## placement_promotions

| Поле | Тип | Обязательное | По умолчанию | Ключ |
|---|---|---|---|---|
| `placement_id` | TEXT | Да | — | PK |
| `promotion_id` | TEXT | Да | — | — |
| `recorded_at` | TEXT | Да | — | — |

Внешние ключи: `promotion_id` → `promotions.promotion_id`, `placement_id` → `placements.placement_id`.

## feature_definitions

| Поле | Тип | Обязательное | По умолчанию | Ключ |
|---|---|---|---|---|
| `position` | INTEGER | Да | — | PK |
| `name` | TEXT | Да | — | — |
| `title_ru` | TEXT | Да | — | — |
| `kind` | TEXT | Да | — | — |
| `definition` | TEXT | Да | — | — |
| `useful_for` | TEXT | Да | — | — |
| `missing_rule` | TEXT | Да | — | — |

## user_features

| Поле | Тип | Обязательное | По умолчанию | Ключ |
|---|---|---|---|---|
| `user_id` | INTEGER | Да | — | PK |
| `as_of` | TEXT | Да | — | PK |
| `feature_version` | TEXT | Да | 'v1' | PK |
| `first_source_channel_id` | TEXT | Нет | — | — |
| `education_stage` | TEXT | Нет | — | — |
| `job_search_status` | TEXT | Нет | — | — |
| `primary_topic_id` | TEXT | Нет | — | — |
| `days_since_first_seen` | REAL | Нет | — | — |
| `is_main_channel_member` | INTEGER | Нет | — | — |
| `days_since_last_activity` | REAL | Нет | — | — |
| `active_days_30d` | INTEGER | Нет | — | — |
| `orders_count_90d` | INTEGER | Нет | — | — |
| `net_spend_90d_minor` | INTEGER | Нет | — | — |
| `mean_order_value_90d_minor` | REAL | Нет | — | — |
| `days_since_last_purchase` | REAL | Нет | — | — |
| `mean_purchase_gap_days` | REAL | Нет | — | — |
| `paid_courses_count` | INTEGER | Нет | — | — |
| `hackathons_attended_180d` | INTEGER | Нет | — | — |
| `course_intent_actions_30d` | INTEGER | Нет | — | — |
| `offers_sent_30d` | INTEGER | Нет | — | — |
| `active_offer_discount_pct` | REAL | Нет | — | — |
| `calendar_month` | INTEGER | Да | — | — |
| `days_to_next_relevant_course_start` | REAL | Нет | — | — |
| `quality_json` | TEXT | Да | — | — |
| `built_at` | TEXT | Да | — | — |

Внешние ключи: `user_id` → `users.user_id`.

## channel_stats

| Поле | Тип | Обязательное | По умолчанию | Ключ |
|---|---|---|---|---|
| `channel_id` | TEXT | Да | — | PK |
| `as_of` | TEXT | Да | — | PK |
| `horizon_days` | INTEGER | Да | — | PK |
| `main_channel_id` | TEXT | Да | — | PK |
| `topic_id` | TEXT | Нет | — | — |
| `audience_stage` | TEXT | Нет | — | — |
| `subscribers` | INTEGER | Нет | — | — |
| `typical_post_views_24h` | INTEGER | Нет | — | — |
| `snapshot_age_days` | REAL | Нет | — | — |
| `placements_count` | INTEGER | Да | — | — |
| `days_since_last_placement` | REAL | Нет | — | — |
| `touch_users` | INTEGER | Да | — | — |
| `first_source_users` | INTEGER | Да | — | — |
| `returning_touch_users` | INTEGER | Да | — | — |
| `mature_users` | INTEGER | Да | — | — |
| `commerce_observed_users` | INTEGER | Да | — | — |
| `buyers_horizon` | INTEGER | Да | — | — |
| `conversion_horizon` | REAL | Нет | — | — |
| `net_cash_horizon_minor` | INTEGER | Нет | — | — |
| `cash_per_user_horizon_minor` | REAL | Нет | — | — |
| `member_observed_users` | INTEGER | Нет | — | — |
| `left_users` | INTEGER | Нет | — | — |
| `banned_users` | INTEGER | Нет | — | — |
| `rejoined_users` | INTEGER | Нет | — | — |
| `current_members` | INTEGER | Нет | — | — |
| `current_membership_unknown_users` | INTEGER | Нет | — | — |
| `retention_eligible_users` | INTEGER | Нет | — | — |
| `retention_horizon` | REAL | Нет | — | — |
| `net_cash_to_date_minor` | INTEGER | Нет | — | — |
| `contribution_to_date_minor` | INTEGER | Нет | — | — |
| `ad_cost_minor` | INTEGER | Нет | — | — |
| `cost_per_first_source_user_minor` | REAL | Нет | — | — |
| `attributed_romi` | REAL | Нет | — | — |
| `quality_json` | TEXT | Да | — | — |

## promotion_stats

| Поле | Тип | Обязательное | По умолчанию | Ключ |
|---|---|---|---|---|
| `promotion_id` | TEXT | Да | — | PK |
| `as_of` | TEXT | Да | — | PK |
| `horizon_days` | INTEGER | Да | — | PK |
| `version_id` | TEXT | Нет | — | — |
| `kind` | TEXT | Да | — | — |
| `starts_at` | TEXT | Нет | — | — |
| `ends_at` | TEXT | Нет | — | — |
| `is_active` | INTEGER | Нет | — | — |
| `duration_days` | REAL | Нет | — | — |
| `days_to_end` | REAL | Нет | — | — |
| `discount_type` | TEXT | Нет | — | — |
| `discount_value` | INTEGER | Нет | — | — |
| `courses_count` | INTEGER | Нет | — | — |
| `sent_offers` | INTEGER | Да | — | — |
| `sent_users` | INTEGER | Да | — | — |
| `failed_offers` | INTEGER | Да | — | — |
| `clicked_users` | INTEGER | Да | — | — |
| `click_rate` | REAL | Нет | — | — |
| `mature_sent_users` | INTEGER | Да | — | — |
| `commerce_observed_users` | INTEGER | Да | — | — |
| `buyers_horizon` | INTEGER | Да | — | — |
| `conversion_horizon` | REAL | Нет | — | — |
| `linked_paid_orders` | INTEGER | Да | — | — |
| `linked_buyers` | INTEGER | Да | — | — |
| `net_cash_minor` | INTEGER | Да | — | — |
| `mean_discount_sent_pct` | REAL | Нет | — | — |
| `repeat_sent_users` | INTEGER | Да | — | — |
| `quality_json` | TEXT | Да | — | — |

## schema_meta

| Поле | Тип | Обязательное | По умолчанию | Ключ |
|---|---|---|---|---|
| `version` | INTEGER | Да | — | PK |
| `description` | TEXT | Да | — | — |
