-- 0002: planned workouts (Phase 2 dashboard: planned vs. completed).
-- Completion status is DERIVED by matching activities on local date + sport family; it is
-- not stored, so it can never drift from the activity data.

CREATE TABLE planned_workouts (
    id                INTEGER PRIMARY KEY,
    source            TEXT    NOT NULL CHECK (source IN ('manual', 'synthetic', 'engine', 'calendar')),
    source_id         TEXT    NOT NULL,
    date              TEXT    NOT NULL,
    sport             TEXT    NOT NULL CHECK (sport IN ('run', 'trail_run', 'treadmill', 'strength',
                                                        'bike', 'walk', 'other')),
    workout_type      TEXT    NOT NULL CHECK (workout_type IN ('easy', 'long', 'tempo', 'intervals',
                                                               'recovery', 'strength_full',
                                                               'strength_upper', 'strength_lower')),
    target_duration_s INTEGER CHECK (target_duration_s > 0),
    target_distance_m REAL    CHECK (target_distance_m >= 0),
    notes             TEXT,   -- untrusted free text
    created_utc       TEXT    NOT NULL,
    updated_utc       TEXT    NOT NULL,
    UNIQUE (source, source_id)
) STRICT;
CREATE INDEX idx_planned_workouts_date ON planned_workouts (date);
