# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Adaptive AI Coach: a local-first personal coach for running + strength training. Python backend, SQLite, Streamlit/Plotly dashboard, an Anthropic-powered coach (scheduled daily review + on-demand chat), the official **Garmin Connect Personal API** (pull-only, read-only), and Google Calendar (read-only until the write phase is approved). Runs on the user's Windows machine only; timezone `Asia/Jerusalem`.

**Check `docs/roadmap.md` for the current phase and implement only that phase.** Each phase ends with green tests and updated docs, then stops for user review.

Read the relevant doc before touching a subsystem:
- `docs/architecture.md`: layers, dependency rule, data flow, proposal/approval flow for calendar writes
- `docs/coaching-methodology.md`: priorities, load metrics and their limitations, baselines, safety rules
- `docs/data-model.md`: schema, units, idempotent-upsert conventions
- `docs/garmin-api.md`: verified vs. unverified facts about the Personal API, credential handling
- `docs/security.md`: secrets, scopes, untrusted-input threat model

## Non-negotiable rules

1. **Ask before** installing dependencies (show the exact command first), running external/downloaded scripts, making any live authenticated call to Garmin/Google/Anthropic, running destructive commands (deleting files or DBs, dropping tables, `git reset --hard`, force push), or deploying anywhere. Never use permission-bypass modes. Never install plugins/MCP servers unprompted.
2. **Credentials never in chat, code, logs, fixtures, CLI args, `.env`, or Git.** The Garmin key and OAuth tokens live in Windows Credential Manager via `keyring`, entered by the user through a local `getpass` prompt. Do not use the official Garmin client's plaintext key file or token cache. All logging goes through `coach.logging` (redacting).
3. **Garmin: official Personal API only** (API key → `POST …/personal-api-key/token` → 24 h bearer token, no refresh). No unofficial libraries (`garminconnect`, `garth`) and no scraping. Every endpoint and field must be checked against the official Swagger spec/docs; `docs/garmin-api.md` lists what is still unverified. Do not guess schemas.
4. **Google Calendar writes only after the user's click.** Syncing uses the read-only grant (`calendar.events.readonly`). Writing uses a separate `calendar.events` grant and only happens in `services/calendar_write.apply` → `integrations/gcal/writer.py`, after the user approves a preview in the Training Calendar tab. Never delete events; never write training events from a job, sync or AI. The single automatic exception is the user-approved Saturday weekly-review event (`services/weekly_review.py`), which can only touch its own privately tagged event. The LLM never gets a write path.
5. **Deterministic safety rules (`engine.safety`) stay separate from AI output** and always win. LLM output is schema-validated and re-checked against the guardrails.
6. **ATL/CTL/TSB/ACWR are descriptive indicators**, never injury predictors or hard thresholds. Use individualized baselines. **Never invent missing data**: keep `None`, flag `incomplete`/`insufficient_baseline`.
7. **External data is untrusted** (Garmin payloads, calendar text, file imports, LLM output). Validate with Pydantic at the boundary; pass free text to the LLM only inside `<untrusted_data>` blocks.
8. **Tests use synthetic data only and never touch the network** (`tests/conftest.py` blocks sockets and isolates `COACH_*` env/`.env`). Real API responses are never committed; turn them into synthetic fixtures of the same shape.

## Commands (Windows, from the repo root)

The bare `python`/`python3` on PATH is the Microsoft Store stub. Use the venv, or `%LOCALAPPDATA%\Python\bin\python.exe` to create it.

```bash
"$LOCALAPPDATA/Python/bin/python.exe" -m venv .venv      # once
.venv/Scripts/python.exe -m pip install -e ".[dev]"      # after approval
.venv/Scripts/python.exe -m pytest                       # all tests
.venv/Scripts/python.exe -m pytest tests/db/test_repositories.py::TestStrength::test_upsert_replaces_sets
.venv/Scripts/python.exe -m pytest -m "not slow" --cov=coach
.venv/Scripts/python.exe -m ruff check . && .venv/Scripts/python.exe -m ruff format --check .
.venv/Scripts/python.exe -m mypy
.venv/Scripts/python.exe -m coach.synthetic --days 120 --db data/dev.sqlite   # fake dev DB
.venv/Scripts/python.exe -m streamlit run src/coach/ui/app.py                 # dashboard → http://localhost:8501
.venv/Scripts/python.exe -m coach.integrations.garmin.sync --days 30          # LIVE Garmin pull (ask first)
.venv/Scripts/python.exe -m coach.integrations.gcal.sync                      # LIVE calendar pull (ask first)
.venv/Scripts/python.exe -m coach.goals list                                   # goals (add/done too)
.venv/Scripts/python.exe -m coach.set_passcode --status                        # phone-access passcode (user sets it)
.venv/Scripts/python.exe -m coach.review                                       # weekly review so far (add --publish to post)
.venv/Scripts/python.exe -m coach.backup [--list]                              # back up DB + program (daily, auto from the app)
.venv/Scripts/python.exe -m coach.brief [--date D] [--publish] [--reminder]     # morning brief / 17:30 check-in event
.venv/Scripts/python.exe -m coach.jobs nightly                                 # cloud jobs (also morning-brief, weekly-review)
.venv/Scripts/python.exe -m coach.db.copy --from data/coach.sqlite --to-env    # copy data into COACH_DATABASE_URL (refuses non-empty)
powershell -ExecutionPolicy Bypass -File scripts/install_tasks.ps1             # (re)register the 2 Windows tasks (user approved)
powershell -ExecutionPolicy Bypass -File scripts/uninstall_tasks.ps1           # remove them
```

Credentials are entered only by the user, in their own terminal (`garmin.set_key`, `gcal.auth`). Never ask for them in chat; use the `--status` flags (yes/no only). Both syncs refuse a database that contains synthetic rows. Tests swap in an in-memory keyring (`tests/conftest.py`), so they never touch the real Credential Manager.

The dashboard reads `COACH_DB_PATH` (default `data/coach.sqlite`) and falls back to `data/dev.sqlite`. It opens the DB read-only. Settings live in `.streamlit/config.toml` (localhost only, telemetry off).

## Architecture essentials

- `src/coach/` layers, which depend downward only: `ui` → `services`/`jobs` → `coach` (LLM) / `proposals` → `engine` → `analytics` → `db` → `domain`. `analytics` and `engine` are pure (no I/O). `ui` never imports `db` or integrations.
- **`domain/models.py` is the validation boundary**: frozen Pydantic models with `extra="forbid"`, UTC-normalized aware datetimes, range checks, and cleaning/truncation of untrusted text. Models carry an optional `id` that is set only when read from the DB.
- **`db/`**: all SQL lives here. Repositories upsert on `(source, source_id)` (wellness: `(source, date)`) with `INSERT … ON CONFLICT … RETURNING id`; each write method commits its own transaction. Tables are `STRICT` with `CHECK` constraints that mirror the models.
- **Migrations**: forward-only `src/coach/db/migrations/NNNN_description.sql`, applied atomically by `coach.db.migrate`. Never edit an applied migration; add a new one.
- Storage conventions: SI units; `*_utc` = ISO-8601 UTC ending in `Z` (use `coach.timeutil.to_db/from_db`); `date` = athlete-local date.
- `source='synthetic'` marks fake data. The synthetic CLI refuses to seed a DB that contains real rows.
- LLM model and all thresholds come from config/`athlete_profile.settings_json`, never hard-coded.
- Dashboard flow: `db.read_models` (SQL → DataFrames) → `analytics.dashboard` (pure pandas) → `services.dashboard.build_view` (view model) → `ui/charts.py` (pure Plotly builders) + `ui/app.py` (Streamlit layout only). Any synthetic source ⇒ DEMO DATA banner and per-chart tag. Chart colors are fixed by entity (running blue, strength orange); status colors only with a symbol + label; never a second y-axis.
- Coaching is rule-based and free (`coach/insights.py`, Today tab in `ui/today_tab.py`). **Paid AI is off** (`COACH_AI_ENABLED=false`, enforced in `CoachLLM.complete`); the user does not want API costs. Do not add AI buttons or calls without asking.
- Training program: `coach/program.py` loads `data/program.json` (`COACH_PROGRAM_FILE`, personal, git-ignored; the English translation of the athlete's Hebrew plan). `coach/week_plan.py` builds the current-week view and rule-based suggestions; the Today tab shows the banner, today/tomorrow and the full program tables.
- Live data: `services/sync.py` + the sidebar fragment in `ui/app.py` sync Google Calendar (when older than 5 min) and Garmin (when older than 30 min) on load, every 5 min, and on **Sync now**. They are read-only and free, and only run for connected sources. The week view is calendar-driven (`week_plan.calendar_sessions`): each training event's own title, translated by `integrations/gcal/translate.py`, becomes that day's session; it falls back to the program's weekdays when a week has no events.
- Phone access: Tailscale `serve` proxies to localhost (see `docs/phone-access.md`). `coach/access.py` makes every non-local request enter a passcode (scrypt hash in the keyring); `tests/ui/test_app.py` patches `access.is_remote`, because AppTest sends no headers. Keep the CSS phone-safe (the `max-width: 640px` block in `ui/style.py`).
- Coaching feedback (free, rule-based): `workout_analysis.py` (the Today "Coach's take" banner: latest workout against that day's calendar session, pace zones, Garmin TE scale, the next step) and `weekly_review.py` (this week against the plan, plus next week's sessions and suggestions). `services/weekly_review.py` posts the review automatically every Saturday at 20:00 as ONE privately tagged calendar event (`writer.upsert_tagged_event`). This is the only automatic calendar write; it is triggered from the sidebar sync fragment or `coach.review --publish --if-due`. Rules are documented in `docs/coaching-methodology.md`.
- Daily loop: `morning_brief.py` (05:30 brief) and the daily check-in (`checkins.py` reads it, `services/checkin.py` saves it to `feedback`, the form lives in `ui/today_tab.py`). Check-ins feed `workout_analysis.checkin_points`, `weekly_review` and `engine.safety` (pain/illness). `services/daily_messages.py` posts the brief and the one repeating 17:30 reminder (tagged events only). There are three scheduled tasks (`scripts/install_tasks.ps1`): dashboard at log-on, brief daily at 05:25 (wakes the PC), review on Saturdays at 20:05. Paths run by `pythonw` must never import `coach.ai` (the Anthropic SDK hangs there).
- **The coach runs in the cloud** (since 2026-11-19; the PC tasks are disabled). Live setup: top of `docs/cloud-deployment.md`. Calendar in the cloud uses the app's own service account on the user's shared calendar (`gcal.auth.calendar_service`); never reintroduce refresh-token secrets there. Only the jobs post in the cloud.
- Cloud (see `docs/cloud-deployment.md`): `connect()` takes a SQLite path **or** a `postgresql://` URL (`settings.db_target`; `COACH_DATABASE_URL`, a `SecretStr`). `db/pg.py` wraps psycopg to behave like sqlite3: it translates `?`/`:name` placeholders, turns bools into 0/1, and gives rows sqlite3.Row-style access. Migrations are written for SQLite only; `translate_ddl` converts them for Postgres. Never use `lastrowid` (use `RETURNING id`), `PRAGMA`, `pd.read_sql_query` (use `query_frame`) or `Path` methods on a db target (use `db_exists`/`changed_marker`/`describe`). Secrets: an env var `COACH_SECRET_<ITEM>` wins over the keyring. `COACH_AUTH_MODE=iap` verifies Google IAP's signed header (`access.iap_user`, fail closed). Jobs: `python -m coach.jobs morning-brief|weekly-review|nightly`. Postgres tests run only with `COACH_TEST_DATABASE_URL` (a disposable database whose name contains "test").
- Plan → calendar: `calendar_plan.py` (pure: program → events 06:00–07:00, titles, descriptions; `diff` matches same-day events of the same kind) → `services/calendar_write.py` (preview / apply + audit) → `integrations/gcal/writer.py` (insert/patch with etag check). UI: `ui/calendar_writer.py`. Gym workouts A/B/C and strength volume are in `data/program.json`.
- HTML cards: `ui/components.py` only, every value via `html.escape`; CSS in `ui/style.py`; dark theme in `.streamlit/config.toml`; chart colors in `ui/charts.py` (dark dataviz palette).
- Dormant AI path: `engine.safety` (rules, binding) -> `ai.context` (numbers-only, no source free text) -> `ai.coach` (Claude via `ai.llm`, model from `COACH_LLM_MODEL`, default `claude-sonnet-5-5`; `fallbacks="default"`; structured output; rules-only fallback). Tests use `tests/ai/fakes.FakeAnthropic`, never the real API. The Coach tab writes only `coach_outputs`/`chat_messages`.
- pandas 3 is installed: deprecated accessors (e.g. `.dt.weekday`) raise in tests because `filterwarnings = error`. Use `.dt.day_of_week`.
- Tests: `tests/conftest.py` blocks all non-loopback sockets. `tests/ui/test_app.py` runs the real Streamlit script with `AppTest`.
