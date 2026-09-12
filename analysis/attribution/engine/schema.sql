PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS datasets (
  id TEXT PRIMARY KEY,
  label TEXT NOT NULL,
  provenance TEXT NOT NULL CHECK (provenance IN ('real','synthetic')),
  clock TEXT NOT NULL CHECK (clock IN ('UTC','local_unspecified')),
  sales_policy TEXT NOT NULL CHECK (sales_policy IN ('recorded_payments','legacy_sales_proxy'))
);
CREATE TABLE IF NOT EXISTS users (
  dataset_id TEXT NOT NULL REFERENCES datasets(id),
  user_key TEXT NOT NULL,
  PRIMARY KEY (dataset_id,user_key)
);
CREATE TABLE IF NOT EXISTS placements (
  dataset_id TEXT NOT NULL REFERENCES datasets(id),
  id TEXT NOT NULL,
  campaign_id TEXT NOT NULL,
  creative_id TEXT NOT NULL,
  channel TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('external','owned')),
  publication_time TEXT NOT NULL,
  publication_us INTEGER NOT NULL,
  cost_cents INTEGER CHECK (cost_cents IS NULL OR cost_cents >= 0),
  token TEXT NOT NULL,
  PRIMARY KEY (dataset_id,id),
  UNIQUE (dataset_id,token)
);
CREATE TABLE IF NOT EXISTS touches (
  dataset_id TEXT NOT NULL,
  id TEXT NOT NULL,
  user_key TEXT NOT NULL,
  placement_id TEXT NOT NULL,
  event_type TEXT NOT NULL CHECK (event_type IN ('bot_start','tracked_click','post_click','manual_source')),
  occurred_at TEXT NOT NULL,
  occurred_us INTEGER NOT NULL,
  PRIMARY KEY (dataset_id,id),
  FOREIGN KEY (dataset_id,user_key) REFERENCES users(dataset_id,user_key),
  FOREIGN KEY (dataset_id,placement_id) REFERENCES placements(dataset_id,id)
);
CREATE INDEX IF NOT EXISTS touches_user_time ON touches(dataset_id,user_key,occurred_us);
CREATE TABLE IF NOT EXISTS leads (
  dataset_id TEXT NOT NULL,
  id TEXT NOT NULL,
  user_key TEXT NOT NULL,
  interest TEXT NOT NULL,
  created_at TEXT NOT NULL,
  created_us INTEGER NOT NULL,
  PRIMARY KEY (dataset_id,id),
  FOREIGN KEY (dataset_id,user_key) REFERENCES users(dataset_id,user_key)
);
CREATE TABLE IF NOT EXISTS orders (
  dataset_id TEXT NOT NULL,
  id TEXT NOT NULL,
  user_key TEXT NOT NULL,
  lead_id TEXT,
  created_at TEXT NOT NULL,
  created_us INTEGER NOT NULL,
  amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
  PRIMARY KEY (dataset_id,id),
  FOREIGN KEY (dataset_id,user_key) REFERENCES users(dataset_id,user_key),
  FOREIGN KEY (dataset_id,lead_id) REFERENCES leads(dataset_id,id)
);
CREATE TABLE IF NOT EXISTS order_items (
  dataset_id TEXT NOT NULL,
  order_id TEXT NOT NULL,
  line_no INTEGER NOT NULL CHECK (line_no > 0),
  course TEXT NOT NULL,
  amount_cents INTEGER NOT NULL CHECK (amount_cents >= 0),
  PRIMARY KEY (dataset_id,order_id,line_no),
  FOREIGN KEY (dataset_id,order_id) REFERENCES orders(dataset_id,id)
);
CREATE TABLE IF NOT EXISTS payments (
  dataset_id TEXT NOT NULL,
  id TEXT NOT NULL,
  order_id TEXT NOT NULL,
  amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
  status TEXT NOT NULL CHECK (status IN ('succeeded','pending','failed')),
  occurred_at TEXT NOT NULL,
  occurred_us INTEGER NOT NULL,
  evidence TEXT NOT NULL CHECK (evidence IN ('recorded_payment','sales_proxy')),
  PRIMARY KEY (dataset_id,id),
  FOREIGN KEY (dataset_id,order_id) REFERENCES orders(dataset_id,id)
);
CREATE INDEX IF NOT EXISTS payments_time ON payments(dataset_id,status,occurred_us);
