-- Additive extension over postupashki_data v2. No business table is replaced.
CREATE TABLE IF NOT EXISTS app_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
INSERT OR IGNORE INTO app_meta VALUES ('schema','1');
INSERT OR IGNORE INTO app_meta VALUES ('provenance','unknown');
CREATE TABLE IF NOT EXISTS telegram_channels (
 channel_id TEXT PRIMARY KEY REFERENCES channels(channel_id), chat_id INTEGER NOT NULL UNIQUE CHECK(chat_id<0),
 recorded_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS placement_details (
 placement_id TEXT PRIMARY KEY REFERENCES placements(placement_id),
 activity_type TEXT NOT NULL CHECK(activity_type IN ('ad','promotion','warmup','hackathon','open_week')),
 ends_at TEXT, hackathon_id TEXT REFERENCES hackathons(hackathon_id), recorded_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS acquisition_notes (
 event_id TEXT PRIMARY KEY REFERENCES acquisition_events(event_id), reason TEXT NOT NULL,
 time_source TEXT NOT NULL, recorded_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS manual_sources (
 event_id TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(user_id),
 source_channel_id TEXT REFERENCES channels(channel_id), placement_id TEXT REFERENCES placements(placement_id),
 answer TEXT NOT NULL, occurred_at TEXT NOT NULL, recorded_at TEXT NOT NULL CHECK(recorded_at>=occurred_at)
);
CREATE TABLE IF NOT EXISTS tracked_clicks (
 event_id TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(user_id),
 source_channel_id TEXT NOT NULL REFERENCES channels(channel_id), placement_id TEXT REFERENCES placements(placement_id),
 occurred_at TEXT NOT NULL, recorded_at TEXT NOT NULL CHECK(recorded_at>=occurred_at)
);
CREATE TABLE IF NOT EXISTS leads (
 lead_id TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(user_id), interest TEXT NOT NULL,
 created_at TEXT NOT NULL, recorded_at TEXT NOT NULL CHECK(recorded_at>=created_at)
);
CREATE TABLE IF NOT EXISTS order_leads (
 order_id TEXT PRIMARY KEY REFERENCES orders(order_id), lead_id TEXT NOT NULL REFERENCES leads(lead_id), recorded_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS submission_links (
 event_id TEXT PRIMARY KEY REFERENCES participation_events(event_id), url TEXT NOT NULL, recorded_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS completion_events (
 event_id TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(user_id),
 hackathon_id TEXT NOT NULL REFERENCES hackathons(hackathon_id), confirmed_by INTEGER NOT NULL CHECK(confirmed_by>0),
 occurred_at TEXT NOT NULL, recorded_at TEXT NOT NULL CHECK(recorded_at>=occurred_at), UNIQUE(user_id,hackathon_id)
);
CREATE TABLE IF NOT EXISTS join_requests (
 event_id TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(user_id), channel_id TEXT NOT NULL REFERENCES channels(channel_id),
 occurred_at TEXT NOT NULL, recorded_at TEXT NOT NULL CHECK(recorded_at>=occurred_at)
);
-- Advertising is already in placement_cost_events. Discounts are derived from known prices.
CREATE TABLE IF NOT EXISTS cost_components (
 cost_id TEXT PRIMARY KEY, placement_id TEXT REFERENCES placements(placement_id),
 promotion_id TEXT REFERENCES promotions(promotion_id), hackathon_id TEXT REFERENCES hackathons(hackathon_id),
 component TEXT NOT NULL CHECK(component IN ('prize','staff','other')), amount_minor INTEGER NOT NULL CHECK(amount_minor>=0),
 occurred_at TEXT NOT NULL, recorded_at TEXT NOT NULL CHECK(recorded_at>=occurred_at),
 CHECK((placement_id IS NOT NULL)+(promotion_id IS NOT NULL)+(hackathon_id IS NOT NULL)=1)
);
CREATE TABLE IF NOT EXISTS incrementality_evidence (
 evidence_id TEXT PRIMARY KEY, placement_id TEXT REFERENCES placements(placement_id), channel_id TEXT REFERENCES channels(channel_id),
 method TEXT NOT NULL CHECK(method IN ('experiment','calibration')), romi_inc REAL, k REAL CHECK(k IS NULL OR (k>=0 AND k<=1)),
 description TEXT NOT NULL, starts_at TEXT NOT NULL, ends_at TEXT NOT NULL CHECK(ends_at>starts_at), recorded_at TEXT NOT NULL,
 CHECK((method='experiment' AND placement_id IS NOT NULL AND romi_inc IS NOT NULL AND k IS NULL)
 OR (method='calibration' AND channel_id IS NOT NULL AND k IS NOT NULL AND romi_inc IS NULL))
);
CREATE TABLE IF NOT EXISTS forecast_runs (
 run_id TEXT PRIMARY KEY, model TEXT NOT NULL, origin_at TEXT NOT NULL, created_at TEXT NOT NULL CHECK(created_at>=origin_at),
 training_end_at TEXT NOT NULL CHECK(training_end_at<=origin_at), horizon_days INTEGER NOT NULL CHECK(horizon_days>0),
 backtest_mae_minor REAL, backtest_wape REAL, backtest_n INTEGER NOT NULL CHECK(backtest_n>=0),
 interval_level REAL, known_campaigns_json TEXT NOT NULL, notes TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS forecast_points (
 point_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES forecast_runs(run_id), day TEXT NOT NULL,
 value_minor REAL NOT NULL, lower_minor REAL, upper_minor REAL,
 CHECK(lower_minor IS NULL OR upper_minor>=lower_minor), UNIQUE(run_id,day)
);
CREATE TABLE IF NOT EXISTS bot_updates (
 bot_id INTEGER NOT NULL, update_id INTEGER NOT NULL, dataset_id TEXT NOT NULL,
 received_at TEXT NOT NULL, payload_json TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','processed','failed')),
 error TEXT, PRIMARY KEY(bot_id,update_id)
);
CREATE TABLE IF NOT EXISTS bot_runtime (bot_id INTEGER PRIMARY KEY,dataset_id TEXT NOT NULL,next_offset INTEGER,last_update_at TEXT);
CREATE TABLE IF NOT EXISTS bot_outbox (
 bot_id INTEGER NOT NULL,update_id INTEGER NOT NULL,seq INTEGER NOT NULL,method TEXT NOT NULL,payload_json TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','sent','failed')),attempts INTEGER NOT NULL DEFAULT 0,
 retry_at REAL NOT NULL DEFAULT 0,error TEXT,PRIMARY KEY(bot_id,update_id,seq),
 FOREIGN KEY(bot_id,update_id) REFERENCES bot_updates(bot_id,update_id)
);
CREATE TABLE IF NOT EXISTS import_runs (
 dataset_id TEXT NOT NULL,fingerprint TEXT NOT NULL,filename TEXT NOT NULL,received_at TEXT NOT NULL,status TEXT NOT NULL,error TEXT,
 PRIMARY KEY(dataset_id,fingerprint)
);
CREATE INDEX IF NOT EXISTS leads_user_time ON leads(user_id,created_at);
CREATE INDEX IF NOT EXISTS manual_user_time ON manual_sources(user_id,occurred_at);
