# Architecture

## Goals

- A local-first personal coach for **running performance, strength preservation, and recovery**, balanced rather than optimizing one at the expense of the others.
- **Hybrid interaction:** a scheduled daily review plus on-demand chat.
- **Recommendations grounded in data:** performance (Garmin activities), recovery (sleep, HRV, resting HR), and subjective feedback (RPE, soreness, mood, pain notes).
- **Human in the loop for any external write.** The calendar is never modified without explicit approval of a specific change.

## Non-goals (for now)

- Cloud hosting, multi-user support, or mobile apps.
- Medical diagnosis. The coach flags pain or illness signals and recommends rest or a professional; it does not diagnose.
- Writing anything to Garmin. The Personal API is read-only as documented.

## High-level diagram

```
 ┌──────────────────────── external (untrusted) ────────────────────────┐
 │ Garmin Personal API        Google Calendar API v3      Anthropic API │
 └──────┬─────────────────────────────┬──────────────────────▲──────────┘
        │ pull (GET), read-only       │ read-only (phase 6)  │ minimized context
        ▼                             ▼                      │
 ┌─────────────────┐       ┌───────────────────┐    ┌────────┴─────────┐
 │ integrations/   │       │ integrations/gcal │    │ coach/ (LLM)     │
 │  garmin, files  │       │                   │    │  prompt builder  │
 └──────┬──────────┘       └────────┬──────────┘    │  review, chat    │
        │ Pydantic-validated domain models          │  output schemas  │
        ▼                           ▼               └──▲───────┬───────┘
 ┌──────────────────────────────────────────────┐      │       │ proposals (JSON)
 │ db/ repositories  ──  SQLite (single file)   │◄─────┘       ▼
 └──────┬──────────────────────────▲────────────┘   ┌──────────────────────┐
        ▼                          │                │ proposals/           │
 ┌──────────────┐   ┌──────────────┴──┐             │  store (state machine)│
 │ analytics/   │──►│ engine/         │─guardrails─►│  executor (write)    │──► Google Calendar
 │ load, readiness,│ │ rules, plan     │            │  only after approval │    (phase 9 only)
 │ strength volume │ │ adjustments     │            └──────────▲───────────┘
 └──────────────┘   └─────────────────┘                        │ Approve click
                                                    ┌──────────┴───────────┐
   jobs/daily_review (OS scheduler) ───────────────►│ ui/ Streamlit+Plotly │ ◄── user
                                                    └──────────────────────┘
```

## Layers and responsibilities

| Package (`src/coach/`) | Responsibility | May depend on |
|---|---|---|
| `config` | Typed settings (pydantic-settings) from env/`.env`; secrets from keyring | – |
| `logging` | Structured logging with a redaction filter (tokens, emails, auth headers) | config |
| `domain` | Pydantic models: Activity, DailyWellness, StrengthSession/Set, Feedback, PlannedWorkout, Recommendation, Proposal | – |
| `db` | SQLite connection, migrations, repositories (all SQL lives here) | domain |
| `integrations/garmin` | Official **Garmin Connect Personal API** (pull-only; API key from keyring → 24 h bearer token, in memory) + mapper → domain; see `garmin-api.md` | domain, config |
| `integrations/gcal` | Google OAuth (installed-app flow), read events, map → domain; write client isolated in `writer.py` and only callable by the executor | domain, config |
| `analytics` | Pure functions: TRIMP/load, ATL/CTL/TSB, ACWR, monotony, readiness score, weekly strength volume per muscle group | domain |
| `engine` | Deterministic rules turning analytics + feedback + upcoming plan into recommendations and **guardrails** | analytics, domain |
| `coach` | LLM client wrapper, context builder (minimization + data delimiting), daily review, chat, output schema validation | engine, db, domain |
| `proposals` | Proposal store + state machine + executor (the only calendar-write path) + audit log | db, gcal writer |
| `jobs` | CLI entry points (`daily_review`, `sync`) invoked by Windows Task Scheduler or cron | services |
| `services` | Thin orchestration used by both UI and jobs (sync, review, chat, approve) | everything below |
| `ui` | Streamlit pages: Today, Trends, Strength, Calendar/Plan, Coach chat, Proposals, Settings | services only |
| `synthetic` | Deterministic, seeded fake-data generator for tests and demo | domain |

Dependency rule: arrows point downward only. `ui` never imports `db` or integrations directly. `analytics` and `engine` are pure (no I/O), which makes them trivially testable.

## Key flows

### 1. Sync
1. `services.sync` asks each integration for data since `sync_state.cursor`.
2. Raw payloads are validated against Pydantic models (`extra="forbid"` on our side, unknown fields dropped/logged as counts, never logged as values).
3. Repositories upsert by `(source, source_id)` so re-syncs are idempotent.
4. `analytics` recomputes derived daily tables for the affected date range.

### 2. Daily review (scheduled)
1. The OS scheduler runs `python -m coach.jobs.daily_review` (default 06:30 local).
2. Sync → analytics → `engine` produces today's recommendation + guardrails.
3. `coach.context` builds a **minimized** context: aggregates and recent sessions, no raw GPS, no calendar attendee data, free text wrapped in `<untrusted_data>` blocks.
4. The LLM returns JSON matching `ReviewOutput` (summary, rationale, recommended session, optional `proposed_changes[]`). Invalid output → one retry → fall back to the deterministic engine text.
5. Any proposed changes are validated against guardrails; violations are dropped and logged. Survivors are stored as `proposed`.
6. The review is stored in `coach_reviews`, and the UI shows it on next open.

### 3. Chat (on demand)
Same context builder, plus conversation history (bounded). The chat can explain, answer questions, and *create proposals*; it cannot execute anything. Subjective feedback stated in chat (e.g., "my calf is sore") is extracted as a structured `feedback` candidate that the user confirms with one click before it is saved.

### 4. Calendar change (approval workflow, phase 9)
```
proposed ──approve (UI click)──► approved ──executor──► applied
   │                                 │                    
   ├──reject──► rejected             └──conflict/error──► failed
   └──TTL / plan changed──► expired
```
- A proposal stores the exact diff (create/move/delete, target calendar, event id, and the event `etag` observed when proposed).
- The executor re-reads the event; if the `etag` changed, it aborts (`failed: stale`) and asks for a new proposal.
- Writes are restricted to a **dedicated "Training" calendar** configured by the user. The executor refuses other calendar IDs.
- Every transition is written to `audit_log`.

## Recommendation logic (balance of goals)

Full detail in [`coaching-methodology.md`](coaching-methodology.md). In short:
- Priorities: (1) running performance, (2) strength and muscle mass, (3) recovery and load management. **Deterministic safety rules outrank all priorities** and are kept in a separate module (`engine.safety`) from any AI output.
- ATL/CTL/TSB/ACWR are **descriptive indicators**, not injury predictors or absolute thresholds. They are combined with individualized HRV/RHR/sleep baselines, subjective fatigue, soreness, pain, and recent training history.
- Missing data is never invented: metrics carry an `ok / incomplete / insufficient_baseline` status.
- All thresholds are config values in `athlete_profile.settings_json`.

## Deployment and decisions (confirmed 2026-11-17)
Local Windows machine only; `venv` + `pip`; Windows Task Scheduler for the daily review; timezone `Asia/Jerusalem`; Anthropic with default model `claude-sonnet-5-5`; Google Calendar read-only at first.

## Technology choices

Phase 1 installs only `pydantic`, `pydantic-settings`, `tzdata` and the dev tools. Every other row is added in its phase, after approval.

| Need | Choice | Why |
|---|---|---|
| Runtime | Python ≥ 3.12 (developed on 3.14), `src/` layout, `pyproject.toml` | Modern typing, simple packaging |
| Storage | SQLite via stdlib `sqlite3` + hand-rolled numbered SQL migrations | Zero dependencies, transparent schema |
| Models/validation | `pydantic` v2, `pydantic-settings` | Boundary validation for untrusted data |
| Data wrangling | `pandas` | Rolling-window analytics |
| Dashboard | `streamlit`, `plotly` | As required |
| HTTP | `httpx` | Timeouts and easy mocking (`respx`) |
| Google | `google-auth-oauthlib`, `google-api-python-client` | Official libraries |
| Garmin | Official `garmin-connect-personal-api-python` client (recommended), with key from keyring and no file caches | Maintained by Garmin, Apache-2.0, only depends on `requests` |
| LLM | `anthropic` SDK, `COACH_LLM_MODEL` (default `claude-sonnet-5-5`) | Structured JSON output |
| Secrets | `keyring` (Windows Credential Manager) | Tokens never on disk in plaintext |
| Timezones | `zoneinfo` + `tzdata` (Windows has no system tz database) | Correct local dates for Asia/Jerusalem DST |
| Scheduling | Windows Task Scheduler → CLI job | No long-running daemon to babysit |
| Dev | `pytest`, `pytest-cov`, `respx`, `freezegun`, `ruff`, `mypy` | Tests, lint, types |
