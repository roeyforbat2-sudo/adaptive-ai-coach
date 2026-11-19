# Adaptive AI Coach

A personal running and strength coach that I built and use every day. It pulls my workouts from **Garmin** and my planned sessions from **Google Calendar**. A **Streamlit** dashboard shows progress, recovery and the training plan. It also sends rule-based feedback: a morning brief before each workout, an evening check-in, and a weekly review every Saturday, all posted to my calendar.

It runs on **Google Cloud** for free. It works from my phone with the PC switched off, behind Google sign-in that lets only my account in.

> Built for a real training block: a half marathon followed by a full marathon, with 3 runs and 3 gym sessions a week.

---

## What it does

| | |
|---|---|
| **Today** | The day's session with the full workout, tomorrow's session, and a "Coach's take" banner. The banner compares the latest workout with what was planned (pace zones, Garmin Training Effect, check-in) and suggests the next step. |
| **Dashboards** | Overview, Running, Strength and Recovery tabs: weekly distance, time-weighted pace, a 28-day pace trend, strength volume per muscle group, sleep, HRV against a personal baseline band, resting HR and plan adherence. |
| **Training Calendar** | Turns the training program into Google Calendar events (06:00–07:00, title = session, description = full workout). You see a **preview first** and nothing is written until you click once. Existing events are updated in place, with an etag check, and nothing is ever deleted. |
| **Daily loop** | A **05:30 morning brief** with the day's workout, a **17:30 check-in** (RPE, soreness, sleep, pain or illness) and a **Saturday 20:00 weekly review** that covers this week against the plan and gives suggestions for next week. |
| **Safety rules** | Deterministic rules (`engine.safety`) look at HRV against your personal range, resting HR against a 60-day baseline, sleep, jumps in run volume, and reported pain or illness. They always override any suggestion. |
| **Live sync** | Garmin (official Personal API) and Google Calendar are synced on a schedule and on demand, read-only. |

Training-load numbers (ATL/CTL/TSB, ACWR) are shown as **descriptive indicators only**, never as injury predictions. Missing data stays missing: the app shows `incomplete` or `insufficient_baseline` rather than guessing.

## Architecture

```
ui (Streamlit) ─► services / jobs ─► coach (rules, dormant LLM) ─► engine (safety) ─► analytics (pure) ─► db ─► domain
                                        ▲                                                             │
                     integrations: Garmin Personal API, Google Calendar  ◄────── untrusted input, validated at the boundary
```

- **Strict layering.** `analytics` and `engine` are pure (no I/O). The `ui` layer never imports `db` or integrations.
- **One validation boundary.** `domain/models.py` holds frozen Pydantic models (`extra="forbid"`, UTC-normalized datetimes, range checks, truncation of untrusted text).
- **One database API, two engines.** The app uses SQLite locally and **Postgres** in the cloud. A small `psycopg` wrapper behaves like `sqlite3` (placeholders, rows, transactions). Forward-only SQL migrations are translated for Postgres.
- **One calendar write path.** Events are written only after the user clicks, or for the app's own privately tagged messages (brief, review, reminder). The calendar reader ignores those tagged events, so they're never mistaken for training sessions.

More detail: [architecture](docs/architecture.md) · [data model](docs/data-model.md) · [coaching methodology](docs/coaching-methodology.md) · [Garmin API notes](docs/garmin-api.md) · [security](docs/security.md) · [cloud deployment](docs/cloud-deployment.md) · [project journey](docs/project-journey.md)

## Cloud setup (free tier)

| Piece | Service |
|---|---|
| Dashboard | Cloud Run (gen2, max 1 instance, request-based billing) |
| Login | Identity-Aware Proxy on Cloud Run. The app **also** verifies IAP's signed JWT (signature, issuer, audience, expiry, allow-listed email) and refuses access if any check fails. |
| Scheduled work | 3 Cloud Run jobs run by Cloud Scheduler: morning brief, weekly review, and a nightly sync plus backup |
| Database | Neon Postgres (TLS enforced) |
| Secrets | Secret Manager. A least-privilege service account with no project-wide roles. |
| Calendar access | The user shares their calendar with the app's own service account. No refresh tokens are stored. |
| Backups | A JSON export written nightly to a private Cloud Storage bucket, kept for 40 days |
| Cost guards | A hard spending cap on Cloud Run plus a budget alert |

## Security

- **No credentials in code, logs, chat or Git.** Locally, secrets live in Windows Credential Manager (`keyring`) and the user types them at a `getpass` prompt. In the cloud they come from Secret Manager. All logging goes through a redacting logger that masks bearer and API keys, Google tokens, emails and Postgres passwords.
- **All external data is untrusted.** Garmin payloads, calendar text, imports and LLM output are validated with Pydantic before use.
- **Official APIs only.** The Garmin Connect Personal API (no scraping or unofficial libraries). Every endpoint is checked against the official spec.
- **Hardened container.** Runs as a non-root user, with nothing personal in the image. Streamlit runs with XSRF protection, hidden error details and an upload cap.

## Tech stack

Python 3.12+ · Streamlit · Plotly · pandas · Pydantic / pydantic-settings · SQLite / Postgres (psycopg 3) · Google Calendar API · Garmin Connect Personal API · Docker · Google Cloud Run, IAP, Secret Manager, Cloud Scheduler, Cloud Storage · pytest · ruff · mypy

## Testing

About **390 tests** run on **synthetic data only**. `tests/conftest.py` blocks every non-loopback socket and swaps in an in-memory keyring, so tests can never reach the network or real credentials. The suite covers:

- migrations (fresh, idempotent, rollback);
- repositories and database constraints;
- analytics against hand-computed fixtures;
- safety rules;
- calendar preview and apply;
- IAP token checks;
- log redaction;
- a headless render of the whole Streamlit app (`AppTest`).

The Postgres tests run against a disposable database when `COACH_TEST_DATABASE_URL` is set.

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -e ".[dev]"
.venv/Scripts/python.exe -m pytest
.venv/Scripts/python.exe -m ruff check . && .venv/Scripts/python.exe -m mypy
```

## Try it with demo data

```bash
.venv/Scripts/python.exe -m coach.synthetic --days 120 --db data/dev.sqlite
.venv/Scripts/python.exe -m streamlit run src/coach/ui/app.py
```

The dashboard opens at http://localhost:8501 with a **DEMO DATA** banner. Connecting real Garmin and Google accounts is described in [docs/garmin-api.md](docs/garmin-api.md) and [docs/cloud-deployment.md](docs/cloud-deployment.md).

## Roadmap

The next step is to turn it into a **multi-user product**: anyone could connect their own Garmin account and get a personalized plan for a race they choose.
