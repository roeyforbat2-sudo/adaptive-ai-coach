-- 0004: AI coach outputs and chat history (Phase 4).
-- Outputs are cached per (kind, ref) so the dashboard does not re-query Claude on every
-- page load. `guardrails_json` records the deterministic safety rules that applied.

CREATE TABLE coach_outputs (
    id              INTEGER PRIMARY KEY,
    kind            TEXT    NOT NULL CHECK (kind IN ('daily', 'workout')),
    ref             TEXT    NOT NULL,  -- daily: YYYY-MM-DD; workout: activities.id
    model           TEXT    NOT NULL,  -- model that answered, or 'rules' for the fallback
    prompt_version  TEXT    NOT NULL,
    output_json     TEXT    NOT NULL CHECK (json_valid(output_json)),
    guardrails_json TEXT    NOT NULL DEFAULT '[]' CHECK (json_valid(guardrails_json)),
    fallback_used   INTEGER NOT NULL DEFAULT 0 CHECK (fallback_used IN (0, 1)),
    input_tokens    INTEGER,
    output_tokens   INTEGER,
    created_utc     TEXT    NOT NULL,
    UNIQUE (kind, ref)
) STRICT;

CREATE TABLE chat_messages (
    id              INTEGER PRIMARY KEY,
    conversation_id TEXT    NOT NULL,
    role            TEXT    NOT NULL CHECK (role IN ('user', 'assistant')),
    content         TEXT    NOT NULL,
    model           TEXT,
    input_tokens    INTEGER,
    output_tokens   INTEGER,
    created_utc     TEXT    NOT NULL
) STRICT;
CREATE INDEX idx_chat_messages_conversation ON chat_messages (conversation_id, id);
