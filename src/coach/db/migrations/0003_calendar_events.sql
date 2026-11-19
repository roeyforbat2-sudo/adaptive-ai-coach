-- 0003: read-only cache of Google Calendar events (Phase 3 integrations).
-- Privacy: only time, status and free/busy are kept for every event. The title is stored
-- ONLY for events classified as training; descriptions, attendees and locations are never
-- requested from Google at all.

CREATE TABLE calendar_events (
    id              INTEGER PRIMARY KEY,
    calendar_id     TEXT    NOT NULL,
    event_id        TEXT    NOT NULL,
    etag            TEXT,
    start_utc       TEXT    NOT NULL,
    end_utc         TEXT    NOT NULL,
    all_day         INTEGER NOT NULL CHECK (all_day IN (0, 1)),
    busy            INTEGER NOT NULL CHECK (busy IN (0, 1)),
    is_training     INTEGER NOT NULL CHECK (is_training IN (0, 1)),
    title           TEXT,   -- untrusted; NULL unless is_training = 1
    last_synced_utc TEXT    NOT NULL,
    UNIQUE (calendar_id, event_id),
    CHECK (is_training = 1 OR title IS NULL)
) STRICT;
CREATE INDEX idx_calendar_events_start ON calendar_events (start_utc);
