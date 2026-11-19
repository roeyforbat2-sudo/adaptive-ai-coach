# Roadmap

Each phase is small, ends with green tests and updated docs, and **stops for user review**. 🔒 marks an approval gate where something needs the user's explicit OK before that step.

Ordering principle: build and validate everything on **synthetic data** first, so the coach is useful and tested before any external account is connected.

| # | Phase | Status |
|---|---|---|
| 0 | Architecture & plan | ✅ approved 2026-11-17 |
| 1 | Project skeleton, config, DB, synthetic data | ✅ 2026-11-17 |
| 2 | **Streamlit dashboard on synthetic data** (+ basic analytics, planned workouts) | ✅ 2026-11-17 |
| 3 | **Real data: Garmin Personal API + Google Calendar (read-only)** | ✅ both live, 2026-11-17 |
| 3b | Training-load analytics (ATL/CTL/TSB, ACWR, readiness) + manual feedback & strength logging | ⏳ partly: daily check-in done (2026-11-18); load charts still to come |
| 4 | **Coach (free, rule-based)**: goals, safety rules, workout feedback, weekly review, goal progress, redesigned Today tab | ✅ 2026-11-18 (paid AI kept but switched off) |
| 8 | Scheduled brief / review / sync | ✅ 2026-11-18 on Windows; moved to Cloud Scheduler 2026-11-19 |
| 9 | Calendar write via approved preview | ✅ 2026-11-18 |
| 10 | Hardening & polish: backups, cloud deployment (Cloud Run + IAP + Neon) | ✅ 2026-11-19, see `cloud-deployment.md` |
| 11 | Multi-user product: anyone connects their Garmin and gets a plan for their race | 💡 next big idea |

The full story is in [`project-journey.md`](project-journey.md).

**Revised order (2026-11-17):** the dashboard moved ahead of the advanced analytics so the app is visible and usable early. Phase 2 ships *basic descriptive* metrics only; the load models and readiness move to Phase 3, and their charts are added to the existing dashboard.

## Confirmed decisions (2026-11-17)
Garmin: official **Garmin Connect Personal API** (the user already has a key). AI provider: Anthropic, default model `claude-sonnet-5-5`. Environment: `venv` + `pip`. Automation: Windows Task Scheduler. Timezone: `Asia/Jerusalem`. Local git repository: approved. Deployment: local Windows machine. Google Calendar: read-only first; changes only with explicit approval.

---

### Phase 0: Architecture & plan ✅
`CLAUDE.md`, `docs/*`, `.gitignore`, `.env.example`.

### Phase 1: Skeleton (authorized 2026-11-17)
- `pyproject.toml` (deps: `pydantic`, `pydantic-settings`, `tzdata`; dev: `pytest`, `pytest-cov`, `ruff`, `mypy`), `src/coach/`, `tests/`.
- `config` (pydantic-settings), redacting `logging`, `timeutil`.
- `db`: connection (WAL, FKs), forward-only migration runner, `0001_initial.sql` (STRICT tables, CHECK constraints, append-only audit log).
- Domain models (validation boundary for untrusted input) and repositories for activities, wellness, feedback, strength, exercises, audit log.
- `coach.synthetic`: seeded generator + CLI that refuses to touch a DB containing real data.
- Local git repository.
Tests: migrations (fresh, idempotent, rollback on failure, naming), constraints, repository idempotency and round trips, redaction (incl. Garmin `ApiKey` header), model validation, generator determinism and shape, CLI. Network is blocked in tests.

### Phase 2: Streamlit dashboard (synthetic data)
- Migration `0002_planned_workouts`; the synthetic generator also writes a training plan (block + 14 days ahead). Completion status is derived (activity of the same family on the same local day), not stored.
- `db.read_models` (DataFrames), `analytics.dashboard` (weekly distance, time-weighted pace, 28-day rolling pace median, training time, strength sessions/working sets, muscle-group volume, sleep/HRV/RHR 7-day means, HRV individual baseline band, plan status and adherence, 7-day overview vs. 4-week average). Missing data stays missing.
- `services.dashboard`: resolves the DB (configured → `data/dev.sqlite`), opens it **read-only**, and builds the view model.
- `ui/app.py`: five tabs (Overview, Running, Strength, Recovery, Training Calendar), Plotly charts, a period selector, and a **DEMO DATA** banner plus per-chart tag whenever synthetic data is present. Bound to localhost; usage stats are off.
Tests: hand-computed metric fixtures, service and read-only tests, chart invariants (demo tag, fixed entity colors, single y-axis, status symbols), and a headless AppTest render of the whole app.

### Phase 3: Real data (moved up 2026-11-17 at the user's request)
- Garmin: verified against the official OpenAPI spec; key in Windows Credential Manager; first live 90-day sync done. See `garmin-api.md`.
- Google Calendar: `calendar.events.readonly`, refresh token in the keyring, minimal `fields`, training events become `planned_workouts` (source `calendar`), migration `0003_calendar_events`.
- Both syncs are manual commands for now; scheduling comes with Phase 8.

### Phase 3b: Training-load analytics & logging
- Session load with an explicit `load_method` (Garmin load → TRIMP → sRPE), ATL/CTL/TSB, EWMA ACWR, monotony: all **descriptive** (see `coaching-methodology.md`), with `ok / incomplete / insufficient_baseline` status.
- Readiness components (HRV, RHR, sleep, subjective), and RIR-based hard sets.
- Dashboard additions: a load chart, readiness card, feedback form and strength log (the first dashboard writes, via repositories).

### Phase 4: AI coach (built 2026-11-17, merges the old Phases 4 and 5)
- Goals as data (`python -m coach.goals`): half marathon 2027-02-26, marathon October 2027 (date to be set).
- `engine.safety`: deterministic readiness (HRV vs personal range, resting HR vs 60-day baseline, 7-day sleep, run-volume jump, reported pain/illness). Binding: AI suggestions are capped to easy when intensity is blocked.
- `ai.context`: numbers-only summary (no names, titles, GPS, emails); `ai.coach`: daily advice + workout feedback (JSON-schema structured output) and chat; `claude-sonnet-5-5`, server-side refusal fallback on, rules-only fallback when unavailable.
- Coach tab (first tab): goal countdowns, readiness, today's advice, last-7-days workouts with feedback, chat. Every AI call is an explicit click or message; outputs are cached in `coach_outputs`, chat in `chat_messages` (migration 0004).

### (old) Phase 4: Recommendation engine & safety rules
`engine.safety` (deterministic guardrails, separate module) + `engine.planner` (priority-ordered recommendation). Table-driven scenario tests.

### Phase 5: AI coach 🔒 (Anthropic key entered locally by the user; `anthropic` SDK install)
Context builder (minimization, `<untrusted_data>` delimiting, missing-data markers), versioned prompts, schema-validated output, guardrail validator, fallback. Mocked LLM in tests; prompt-injection fixtures. 🔒 The user reviews the Garmin T&C on third-party sharing before real Garmin data is sent.

### Phase 6: Google Calendar read-only 🔒 (user creates OAuth client; first live call)
Installed-app flow, `calendar.readonly` only, refresh token in keyring, no attendee/description storage.

### Phase 7: Garmin Connect Personal API 🔒
1. User saves the Swagger/OpenAPI spec to `docs/vendor/`; verify endpoints, schemas, units, and paging against it. Update `garmin-api.md`.
2. 🔒 Install the official client (`garmin-connect-personal-api-python`, pinned), or choose a thin `httpx` client.
3. Key storage: `set_key` command (local `getpass` → Windows Credential Manager) plus a recorded expiry date. The key never appears in chat.
4. Adapter + mappers against spec-shaped **synthetic** fixtures.
5. 🔒 One approved live call per endpoint to confirm shape; then incremental sync + 30-day-window backfill, resumable cursor, conservative pacing, clean 401 handling.

### Phase 8: Scheduled daily review 🔒 (register a Task Scheduler task)
`jobs/daily_review` with `--dry-run`, a lock file, and sanitized error reporting.

### Phase 9: Plan → Google Calendar ✅ (built 2026-11-18)
Separate write token (`calendar.events`); preview + one approving click in the Training Calendar tab; one event per training day (default 06:00–07:00), English title (*Easy run*, *Long run*, *Quality run*, *Gym – Upper/Legs/Full body*), full session in the description (gym exercises from the plan); long run Friday; existing same-kind events updated in place; etag check; never deletes; audit log. `calendar_plan.py` (pure) → `services/calendar_write.py` → `integrations/gcal/writer.py`.

### Smart feedback (built 2026-11-18, free and rule-based)
The "Coach's take" banner on Today analyses the latest workout against its planned session. The weekly review (this week + next week's suggestions) is on Today and posted to Google Calendar every Saturday at 20:00 (user's choice). It is posted automatically while the dashboard runs; for when it isn't running, `coach.review --publish --if-due` is ready for a Windows scheduled task (not set up yet: needs the user's OK).

### Phone access (built 2026-11-18, user's choice: private via Tailscale)
- Passcode gate for non-local visits (`coach/access.py`, `coach/set_passcode.py`), a phone-width layout (`ui/style.py`), and setup steps in `docs/phone-access.md`. The user installs Tailscale themselves; no cloud hosting.

### Phase 10: Hardening
Backup/export, retention, weekly review, prompt eval set, coverage targets.
