-- 0001: core athlete, training, recovery and operational tables (Phase 1).
-- Units are SI. *_utc columns are ISO-8601 UTC text ending in 'Z'. `date` columns are the
-- athlete's local calendar date (YYYY-MM-DD). Derived and coaching tables arrive in later
-- migrations.

CREATE TABLE athlete_profile (
    id                  INTEGER PRIMARY KEY CHECK (id = 1),
    display_name        TEXT    NOT NULL DEFAULT 'Athlete',
    birth_year          INTEGER CHECK (birth_year BETWEEN 1900 AND 2100),
    sex                 TEXT    CHECK (sex IN ('female', 'male', 'other')),
    timezone            TEXT    NOT NULL DEFAULT 'UTC',
    max_hr              INTEGER CHECK (max_hr BETWEEN 100 AND 250),
    resting_hr_baseline INTEGER CHECK (resting_hr_baseline BETWEEN 25 AND 120),
    lthr                INTEGER CHECK (lthr BETWEEN 80 AND 230),
    weight_kg           REAL    CHECK (weight_kg BETWEEN 25 AND 300),
    settings_json       TEXT    NOT NULL DEFAULT '{}' CHECK (json_valid(settings_json)),
    updated_utc         TEXT    NOT NULL
) STRICT;

CREATE TABLE goals (
    id           INTEGER PRIMARY KEY,
    kind         TEXT    NOT NULL CHECK (kind IN ('race', 'volume', 'strength', 'body')),
    description  TEXT    NOT NULL,
    target_date  TEXT,
    target_value REAL,
    unit         TEXT,
    priority     INTEGER NOT NULL DEFAULT 2 CHECK (priority BETWEEN 1 AND 3),
    active       INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    created_utc  TEXT    NOT NULL
) STRICT;

CREATE TABLE activities (
    id               INTEGER PRIMARY KEY,
    source           TEXT    NOT NULL CHECK (source IN ('garmin', 'file_import', 'manual', 'synthetic')),
    source_id        TEXT    NOT NULL,
    sport            TEXT    NOT NULL CHECK (sport IN ('run', 'trail_run', 'treadmill', 'strength',
                                                       'bike', 'walk', 'other')),
    start_utc        TEXT    NOT NULL,
    duration_s       INTEGER NOT NULL CHECK (duration_s > 0),
    distance_m       REAL    CHECK (distance_m >= 0),
    elevation_gain_m REAL    CHECK (elevation_gain_m >= 0),
    avg_hr           INTEGER CHECK (avg_hr BETWEEN 20 AND 250),
    max_hr           INTEGER CHECK (max_hr BETWEEN 20 AND 250),
    training_load    REAL    CHECK (training_load >= 0),
    aerobic_te       REAL    CHECK (aerobic_te BETWEEN 0 AND 5),
    anaerobic_te     REAL    CHECK (anaerobic_te BETWEEN 0 AND 5),
    name             TEXT,   -- untrusted free text
    created_utc      TEXT    NOT NULL,
    updated_utc      TEXT    NOT NULL,
    UNIQUE (source, source_id)
) STRICT;
CREATE INDEX idx_activities_start ON activities (start_utc);

CREATE TABLE exercises (
    id                   INTEGER PRIMARY KEY,
    name                 TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    primary_muscle_group TEXT    NOT NULL CHECK (primary_muscle_group IN
        ('quads', 'hamstrings', 'glutes', 'calves', 'core', 'back', 'chest', 'shoulders', 'arms')),
    is_lower_body        INTEGER NOT NULL CHECK (is_lower_body IN (0, 1)),
    unilateral           INTEGER NOT NULL DEFAULT 0 CHECK (unilateral IN (0, 1))
) STRICT;

INSERT INTO exercises (name, primary_muscle_group, is_lower_body, unilateral) VALUES
    ('Back Squat',           'quads',      1, 0),
    ('Bulgarian Split Squat','quads',      1, 1),
    ('Deadlift',             'hamstrings', 1, 0),
    ('Romanian Deadlift',    'hamstrings', 1, 0),
    ('Hip Thrust',           'glutes',     1, 0),
    ('Walking Lunge',        'glutes',     1, 1),
    ('Standing Calf Raise',  'calves',     1, 0),
    ('Pull-Up',              'back',       0, 0),
    ('Barbell Row',          'back',       0, 0),
    ('Bench Press',          'chest',      0, 0),
    ('Push-Up',              'chest',      0, 0),
    ('Overhead Press',       'shoulders',  0, 0),
    ('Plank',                'core',       0, 0),
    ('Pallof Press',         'core',       0, 1);

CREATE TABLE strength_sessions (
    id          INTEGER PRIMARY KEY,
    activity_id INTEGER REFERENCES activities (id) ON DELETE SET NULL,
    date        TEXT    NOT NULL,
    source      TEXT    NOT NULL CHECK (source IN ('garmin', 'file_import', 'manual', 'synthetic')),
    source_id   TEXT    NOT NULL,
    notes       TEXT,
    created_utc TEXT    NOT NULL,
    updated_utc TEXT    NOT NULL,
    UNIQUE (source, source_id)
) STRICT;
CREATE INDEX idx_strength_sessions_date ON strength_sessions (date);

CREATE TABLE strength_sets (
    id          INTEGER PRIMARY KEY,
    session_id  INTEGER NOT NULL REFERENCES strength_sessions (id) ON DELETE CASCADE,
    exercise_id INTEGER NOT NULL REFERENCES exercises (id),
    set_index   INTEGER NOT NULL CHECK (set_index >= 0),
    reps        INTEGER NOT NULL CHECK (reps BETWEEN 0 AND 200),
    load_kg     REAL    CHECK (load_kg BETWEEN 0 AND 1000),
    rir         INTEGER CHECK (rir BETWEEN 0 AND 10),
    is_warmup   INTEGER NOT NULL DEFAULT 0 CHECK (is_warmup IN (0, 1)),
    UNIQUE (session_id, set_index)
) STRICT;

CREATE TABLE daily_wellness (
    id                INTEGER PRIMARY KEY,
    date              TEXT    NOT NULL,
    source            TEXT    NOT NULL CHECK (source IN ('garmin', 'file_import', 'manual', 'synthetic')),
    sleep_duration_s  INTEGER CHECK (sleep_duration_s BETWEEN 0 AND 86400),
    sleep_score       INTEGER CHECK (sleep_score BETWEEN 0 AND 100),
    hrv_rmssd_ms      REAL    CHECK (hrv_rmssd_ms BETWEEN 1 AND 300),
    resting_hr        INTEGER CHECK (resting_hr BETWEEN 25 AND 150),
    body_battery_high INTEGER CHECK (body_battery_high BETWEEN 0 AND 100),
    body_battery_low  INTEGER CHECK (body_battery_low BETWEEN 0 AND 100),
    stress_avg        INTEGER CHECK (stress_avg BETWEEN 0 AND 100),
    steps             INTEGER CHECK (steps >= 0),
    updated_utc       TEXT    NOT NULL,
    UNIQUE (source, date)
) STRICT;

CREATE TABLE feedback (
    id            INTEGER PRIMARY KEY,
    date          TEXT    NOT NULL,
    activity_id   INTEGER REFERENCES activities (id) ON DELETE SET NULL,
    source        TEXT    NOT NULL CHECK (source IN ('manual', 'chat', 'synthetic')),
    source_id     TEXT,   -- NULL for ad-hoc manual entries (NULLs never conflict)
    rpe           INTEGER CHECK (rpe BETWEEN 1 AND 10),
    soreness      INTEGER CHECK (soreness BETWEEN 0 AND 10),
    fatigue       INTEGER CHECK (fatigue BETWEEN 0 AND 10),
    mood          INTEGER CHECK (mood BETWEEN 1 AND 5),
    sleep_quality INTEGER CHECK (sleep_quality BETWEEN 1 AND 5),
    pain_flag     INTEGER NOT NULL DEFAULT 0 CHECK (pain_flag IN (0, 1)),
    pain_location TEXT,
    illness_flag  INTEGER NOT NULL DEFAULT 0 CHECK (illness_flag IN (0, 1)),
    notes         TEXT,
    created_utc   TEXT    NOT NULL,
    UNIQUE (source, source_id)
) STRICT;
CREATE INDEX idx_feedback_date ON feedback (date);

CREATE TABLE sync_state (
    source               TEXT    PRIMARY KEY,
    cursor               TEXT,
    last_success_utc     TEXT,
    last_error           TEXT,   -- sanitized via coach.logging.redact
    consecutive_failures INTEGER NOT NULL DEFAULT 0
) STRICT;

CREATE TABLE audit_log (
    id           INTEGER PRIMARY KEY,
    ts_utc       TEXT NOT NULL,
    actor        TEXT NOT NULL CHECK (actor IN ('user', 'scheduler', 'executor', 'system')),
    action       TEXT NOT NULL,
    entity       TEXT NOT NULL,
    entity_id    TEXT,
    details_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(details_json))
) STRICT;

-- audit_log is append-only.
CREATE TRIGGER audit_log_no_update BEFORE UPDATE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
CREATE TRIGGER audit_log_no_delete BEFORE DELETE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
