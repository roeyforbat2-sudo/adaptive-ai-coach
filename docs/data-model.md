# Data Model

Single SQLite file (default `data/coach.sqlite`, git-ignored). `PRAGMA foreign_keys=ON`, `journal_mode=WAL`.

## Conventions

- **Units:** SI only (m, s, kg, bpm, ms for HRV). Pace and display units are computed in the UI.
- **Time:** `*_utc` columns hold ISO-8601 UTC text. Calendar-day facts (`date`) use the athlete's local date from `athlete_profile.timezone`.
- **Provenance:** every ingested row has `source` (`garmin`, `file_import`, `manual`, `gcal`, `synthetic`) and `source_id`. `UNIQUE(source, source_id)` makes sync idempotent (upsert).
- **IDs:** integer primary keys internally; external IDs live only in `source_id`.
- **Untrusted text:** free-text columns from external sources (activity names, event titles) are stored as-is but flagged `untrusted` by convention: never rendered as HTML (`unsafe_allow_html` is banned) and wrapped when sent to the LLM.
- **No raw payload storage by default.** Optional `raw_json` debug capture is off unless `COACH_STORE_RAW=true`.
- Migrations: `src/coach/db/migrations/NNNN_description.sql`, tracked in `schema_migrations`.

## Implementation status
Migration `0001_initial.sql` (Phase 1) creates: `athlete_profile`, `goals`, `activities`, `exercises` (seeded with 14 common lifts), `strength_sessions`, `strength_sets`, `daily_wellness`, `feedback`, `sync_state`, `audit_log`, `schema_migrations`. All tables are `STRICT` with `CHECK` constraints mirroring the Pydantic models (defence in depth). `audit_log` is append-only via triggers. The derived, calendar, and coaching tables below are added by later phases' migrations.
Migration `0002_planned_workouts.sql` (Phase 2) adds `planned_workouts`. Migration `0005_planned_detail.sql` adds `planned_workouts.detail`: the short session text (e.g. "12 km easy") read from events the app wrote to Google Calendar, untrusted. For those events `target_distance_m` comes from the event's `coachKm` private property.

## Tables

### Athlete & goals
**athlete_profile** (single row)
`id, display_name, birth_year, sex, timezone, max_hr, resting_hr_baseline, lthr, weight_kg, settings_json, updated_utc`
`settings_json` holds engine thresholds, for example: `acwr_low=0.8, acwr_high=1.3, weekly_volume_cap_pct=10, strength_min_sets_per_group=6, lower_body_vs_key_run_gap_h=36, review_time="06:30"`.

**goals**
`id, kind (race|volume|strength|body), description, target_date, target_value, unit, priority (1-3), active, created_utc`

### Training data
**activities**
`id, source, source_id, sport (run|trail_run|treadmill|strength|bike|walk|other), start_utc, duration_s, distance_m, elevation_gain_m, avg_hr, max_hr, training_load, aerobic_te, anaerobic_te, name, created_utc, updated_utc`
UNIQUE(source, source_id). Index on `start_utc`. Pace is derived (duration/distance), not stored. RPE lives in `feedback`.

**activity_laps** (optional detail; added in Phase 7 if the API or FIT file provides laps)
`id, activity_id FK, lap_index, duration_s, distance_m, avg_hr`

**exercises**
`id, name, primary_muscle_group (quads|hamstrings|glutes|calves|core|back|chest|shoulders|arms), is_lower_body, unilateral`

**strength_sessions**
`id, activity_id FK nullable (linked Garmin strength activity), date, source, source_id, notes, created_utc`

**strength_sets**
`id, session_id FK, exercise_id FK, set_index, reps, load_kg, rir (nullable), is_warmup`

### Recovery & feedback
**daily_wellness**
`id, date, source, sleep_duration_s, sleep_score, hrv_rmssd_ms (overnight avg), resting_hr, body_battery_high, body_battery_low, stress_avg, steps, updated_utc`
UNIQUE(source, date). Every metric is nullable; missing means *not measured*, never zero.

**feedback**
`id, date, activity_id FK nullable, source (manual|chat|synthetic), source_id nullable, rpe (1-10), soreness (0-10), fatigue (0-10), mood (1-5), sleep_quality (1-5), pain_flag bool, pain_location (requires pain_flag), illness_flag bool, notes, created_utc`
UNIQUE(source, source_id). Ad-hoc manual entries have a NULL `source_id` and never conflict; `chat` entries are saved only after the user confirms them.

### Derived (recomputable; safe to drop and rebuild)
**training_load_daily**
`date PK, run_load, strength_load, total_load, atl, ctl, tsb, acwr, monotony, weekly_run_distance_m, weekly_hard_sets_lower, weekly_hard_sets_upper`

**readiness_daily**
`date PK, hrv_z, rhr_delta, sleep_component, subjective_component, score (0-100), status (green|amber|red), reasons_json`

### Plan, calendar, coaching
**calendar_events** (read-only cache)
`id, calendar_id, event_id, etag, start_utc, end_utc, all_day, title, is_training (classified), busy, last_synced_utc`
UNIQUE(calendar_id, event_id). Attendees and descriptions are **not stored**.

**planned_workouts** (implemented, migration 0002; `detail` added in 0005)
`id, source (manual|synthetic|engine|calendar), source_id, date, sport, workout_type (easy|long|tempo|intervals|recovery|strength_full|strength_upper|strength_lower), target_duration_s, target_distance_m, notes (untrusted), created_utc, updated_utc`
UNIQUE(source, source_id). `workout_type` must match `sport` (validated in the model). **Status is derived, not stored:** a planned workout is *completed* when an activity of the same family (run/strength) exists on the same local date (each activity fulfils at most one plan); otherwise it is *missed* (past), *today*, or *upcoming*. Activities without a plan are *unplanned*. The future `calendar_event_ref` link arrives with the calendar phases.

**recommendations**
`id, date, readiness_status, recommended_workout_json, guardrails_json, engine_version, created_utc`

**coach_reviews**
`id, date, kind (daily|weekly), recommendation_id FK, llm_model, prompt_version, summary_md, output_json, fallback_used bool, created_utc`

**chat_messages**
`id, conversation_id, role (user|assistant), content, created_utc`; bounded retention (default 90 days).

**calendar_change_proposals**
`id, created_utc, origin (daily_review|chat|manual), action (create|move|delete|update), calendar_id, event_id nullable, observed_etag nullable, payload_json (exact new event fields), rationale, status (proposed|approved|applied|rejected|expired|failed), status_reason, decided_utc, applied_utc, expires_utc`
Invariant: the executor only acts on `status='approved'` rows whose `calendar_id` equals the configured training calendar.

### Operational
**sync_state**
`source PK, cursor, last_success_utc, last_error (sanitized), consecutive_failures`

**audit_log** (append-only)
`id, ts_utc, actor (user|scheduler|executor|system), action, entity, entity_id, details_json (no secrets)`

**schema_migrations**
`version PK, applied_utc`

## Synthetic data

`coach.synthetic` generates a seeded, deterministic block (base → build → taper; 5 runs/week including intervals, a build-phase tempo and a long run; 2 strength sessions/week with progression and taper; wellness that responds to recent load; ~10% poor-sleep nights; a 3-day calf-pain episode at ~60% with runs and lower-body lifting skipped). Everything is tagged `source='synthetic'`.

```bash
python -m coach.synthetic --days 120 --db data/dev.sqlite [--seed 42] [--end-date 2026-11-15]
```
Seeding is idempotent. The CLI **refuses** (exit code 2) to seed a database that contains any non-synthetic rows.
