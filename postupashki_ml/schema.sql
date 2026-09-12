-- Additive migration. No replacement of users, orders, offers, or raw events.
CREATE TABLE IF NOT EXISTS ml_meta (
    key TEXT PRIMARY KEY, value TEXT NOT NULL
);
INSERT OR IGNORE INTO ml_meta VALUES ('schema_version','1');
INSERT OR IGNORE INTO ml_meta VALUES ('provenance','unknown');

-- A contact is delivered/available, never an invented individual post view.
CREATE TABLE IF NOT EXISTS ml_contacts (
    contact_id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(user_id),
    course_id TEXT REFERENCES courses(course_id),
    kind TEXT NOT NULL CHECK(kind IN ('message','event','ad')),
    evidence TEXT NOT NULL CHECK(evidence IN ('delivered','attended','opportunity')),
    intensity REAL NOT NULL DEFAULT 1 CHECK(intensity>=0),
    occurred_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL CHECK(recorded_at>=occurred_at)
);
CREATE INDEX IF NOT EXISTS ml_contact_time ON ml_contacts(occurred_at,recorded_at,user_id);

CREATE TABLE IF NOT EXISTS ml_contact_periods (
    period_id TEXT PRIMARY KEY,
    starts_at TEXT NOT NULL,
    ends_at TEXT NOT NULL CHECK(ends_at>starts_at),
    recorded_at TEXT NOT NULL CHECK(recorded_at>=ends_at)
);

-- Frozen schedule assigned BEFORE outcomes. Also log control/no-action decisions.
CREATE TABLE IF NOT EXISTS ml_decisions (
    decision_id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(user_id),
    course_id TEXT NOT NULL REFERENCES courses(course_id),
    as_of TEXT NOT NULL,
    recorded_at TEXT NOT NULL CHECK(recorded_at<=as_of),
    horizon_days INTEGER NOT NULL CHECK(horizon_days BETWEEN 1 AND 90),
    plan_json TEXT NOT NULL,
    assignment_kind TEXT NOT NULL CHECK(assignment_kind IN ('randomized','observational')),
    experiment_id TEXT,
    arm TEXT,
    assignment_probability REAL CHECK(assignment_probability>0 AND assignment_probability<1),
    CHECK(assignment_kind!='randomized' OR
          (experiment_id IS NOT NULL AND arm IS NOT NULL AND assignment_probability IS NOT NULL)),
    UNIQUE(user_id,course_id,as_of)
);
CREATE INDEX IF NOT EXISTS ml_decision_time ON ml_decisions(as_of,course_id);

-- A schedule for the audience expected to arrive after this placement.
CREATE TABLE IF NOT EXISTS ml_placement_plans (
    placement_id TEXT PRIMARY KEY REFERENCES placements(placement_id),
    plan_json TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);
