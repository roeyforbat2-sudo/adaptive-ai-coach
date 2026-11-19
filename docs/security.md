# Security & Privacy

This app processes health data and has delegated access to a Google account and a Garmin account. Treat it as sensitive software even though it runs locally.

## Assets
- OAuth tokens (Google, Garmin) and the LLM API key.
- Health and training data (HRV, sleep, HR, location-derived metrics, pain/illness notes).
- Calendar contents (titles and times of non-training events reveal personal life).
- Calendar write ability (phase 9).

## Secrets handling
- **Storage:** Google OAuth refresh tokens and the **Garmin Personal API key** live in the OS keyring (`keyring`; Windows Credential Manager) under service name `adaptive-ai-coach`. The Anthropic API key may live in `.env` (git-ignored) or the keyring (the keyring is preferred).
- **Entering credentials:** only through a local `getpass` prompt run by the user (e.g., `python -m coach.integrations.garmin.set_key`). Credentials are **never** requested in or pasted into the Claude chat, never passed as CLI arguments (they would land in shell history and the process list), and never written to `.env`.
- **Garmin specifics:** the official client's plaintext key file (`~/.config/garmin/connect-personal-api-key`) and plaintext bearer-token cache are **not used**. The 24 h bearer token stays in memory. See `garmin-api.md`.
- **Never** in source, tests, fixtures, logs, exception messages, Streamlit state dumps, the SQLite DB, or Git history.
- `.env.example` contains placeholders only. `.gitignore` excludes `.env*` (except the example), `*.sqlite*`, `data/`, `secrets/`, `token*.json`, and `client_secret*.json`.
- Google OAuth client JSON (`client_secret_*.json`) is kept outside the repo or in `secrets/` (ignored); the path is set via `GOOGLE_OAUTH_CLIENT_FILE`.
- **Logging:** a redaction filter (`coach.logging`, implemented and tested in Phase 1) masks `Authorization: Bearer|Basic|ApiKey` values, `*_token`/`*api_key`/`*secret`/`password` keys (including prefixed env-var names), OAuth `code=` URL params, Anthropic/Google token shapes, and email addresses. HTTP client debug logging stays off. Error records in `sync_state` are sanitized.
- **Git:** pre-commit secret scanning (gitleaks, pending approval) plus a CI-less local hook. If a secret is ever committed, rotate it first, then clean history (destructive, so ask first).

## OAuth scopes (least privilege)
| Phase | Service | Scope |
|---|---|---|
| 3 | Google Calendar | `https://www.googleapis.com/auth/calendar.events.readonly` (events only; narrower than `calendar.readonly`) |
| 9 | Google Calendar | `https://www.googleapis.com/auth/calendar.events` (separate token, requested only after approval) |
| 3 | Garmin Personal API | The personal API key (user-chosen expiry of 30–365 days) → 24 h access token. No scopes; read-only. The user can revoke it by deleting the key on Garmin Connect web |

Write tokens are stored under a different keyring key than read tokens so read-only operation never holds write capability.

## Calendar-write safety (built 2026-11-18)
- **Two grants.** Syncing keeps the read-only token (`calendar.events.readonly`). Writing uses a separate token with `calendar.events` ("view and edit events on all your calendars"), granted by the user in their own terminal (`gcal.auth --write`) and stored in Credential Manager (item `google-calendar-write-token`). Revoke with `gcal.auth --disconnect --write` and at https://myaccount.google.com/permissions.
- **One write path.** Only `integrations/gcal/writer.py` calls `events.insert`/`events.patch`, and only `services/calendar_write.apply` calls the writer (a test enforces the imports). No AI, job or sync can write.
- **Preview, then one click.** The Training Calendar tab shows every event that would be created or updated (title, time, current title, full description). Nothing is written until the user clicks *Write N changes to my calendar* under that preview. A new preview is required after each write.
- **Never deletes.** Only creates and patches. Each patch first re-reads the event; if its `etag` changed since the preview, it is skipped as stale.
- **Target calendar.** `COACH_GOOGLE_CALENDAR_WRITE_ID` (default `primary`). The user chose (2026-11-18) to update their existing training events **in place** in their main calendar, instead of a separate Training calendar: a same-day event of the same kind (run or gym) is updated; other events are never touched.
- Events the app wrote carry `extendedProperties.private` (`adaptiveCoach`, `coachDate`, `coachKm`, `coachDetail`); the sync reads them back as untrusted input (validated, length-capped).
- Every write run is recorded in `audit_log` (`calendar_write`, counts only). Writing is refused into a database with demo data.
- **Automatic daily messages (user's choice, 2026-11-18)** (`services/daily_messages.py`), limited the same way as the weekly review below: each is the app's own privately tagged event, created or updated only, never a training event, never a deletion. (a) The 05:30 morning brief, one per training day (`adaptiveCoachBrief=<date>`). (b) ONE repeating daily 17:30 "Daily check-in" reminder (`adaptiveCoachCheckin=daily`, `RRULE:FREQ=DAILY`), created once. Both pop up at the event time and show as free. The check-in itself is entered only in the local app; its optional free text stays local and is never rendered as HTML.
- **Automatic weekly-review event (user's choice, 2026-11-18).** The one exception to the click-to-approve rule: every Saturday at 20:00 (catch-up until Monday 08:00), the app posts **one** event, "📋 Weekly review · Week N of M" (20:00–20:15, shown as free), with the review text. `services/weekly_review.publish` → `writer.upsert_tagged_event` finds events only by the app's private tag `adaptiveCoachReview=<Saturday>`, so it can only create or update that same event, never the user's events, and it never deletes. It runs from the dashboard's sync fragment while the app is open, or from `python -m coach.review --publish --if-due`. It needs the write grant, and is refused on demo data. Each post is audited (`weekly_review_post`, actor `scheduler`).

## Untrusted input & prompt injection
Threats: a calendar invite from anyone, an activity title, a Garmin payload, a file import, or LLM output itself.
- **Validate at the boundary** with Pydantic: types, ranges (e.g., HR 20–250), lengths (titles truncated to 200 chars), enums.
- **Minimize** at the source: Google's `fields` parameter requests only id, title, start/end, status, type and transparency, so descriptions, attendees, locations and conference links are never downloaded. Titles of non-training events are not stored (busy/free only, enforced by a DB `CHECK`). Garmin payloads are reduced to the fields listed in `garmin-api.md`.
- **Delimit:** free text is placed inside `<untrusted_data>` blocks; the system prompt states that content there is data, never instructions.
- **No mutating tools for the LLM.** Its output is JSON validated against strict schemas; proposals are re-checked against deterministic guardrails before storage, and still need human approval.
- **Rendering:** `unsafe_allow_html` is allowed only for app-authored markup in `ui/style.py` (static CSS) and `ui/components.py`, where every interpolated value is passed through `html.escape` (tested with a `<script>` payload). External free text (activity names, calendar titles) is never placed in that markup; it appears only in `st.dataframe`, which escapes it.
- **No paid AI calls by default:** `COACH_AI_ENABLED` defaults to false. `CoachLLM.complete` refuses before any client exists, and the UI has no AI buttons or chat. The free "Copy my training summary" box gives the same numbers-only summary for the user's own Claude chat.
- **File import:** parse in-process with size limits; never execute or unpack untrusted archives beyond the expected format.

## Cloud (live since 2026-11-19; details in `cloud-deployment.md`)
- 2026-11-19: the first IAP client secret leaked into a tool result through a copy button's aria-label. It was rotated the same morning (new secret applied via clipboard and never displayed; old secret disabled, then deleted after a sign-in test). Rule: never read DOM text or labels near secrets; filter `GOCSPX-…` out of any page text that is read.
- Calendar access in the cloud is the service account on a calendar the user shared with it. There are no OAuth tokens in the cloud, and unsharing revokes it instantly.
- The user approved moving to Google Cloud (Cloud Run + IAP + Secret Manager + Scheduler) with Neon Postgres, all on free tiers. Nothing is deployed until Phase 3, step by step, each with the user's OK.
- **Fail-closed login:** IAP on Cloud Run, plus the app's own check of IAP's signed header: signature, issuer, audience, expiry, and an email allow-list.
- **Secrets** live only in Secret Manager. They are injected as env vars (`COACH_SECRET_*`, `COACH_DATABASE_URL`) or mounted files, never put in the image. The database URL is a `SecretStr` and is masked in logs (`postgresql://user:[REDACTED]@…`, `password=[REDACTED]`). The copy tool reads it from the environment, never from the command line.
- **Image:** no personal data (`.dockerignore`), non-root user, Streamlit hardened (XSRF on, error details off, uploads ≤ 1 MB, telemetry off).
- **Postgres:** TLS required. The dashboard's session is read-only. The audit log is append-only there too, enforced by triggers.

## Network & runtime
- Streamlit binds to `localhost` only (`server.address = "localhost"`). It is never opened on the LAN or the internet, and no firewall rule is added.
- **Windows scheduled tasks (user-approved 2026-11-18):** two tasks for the current user only, with no admin rights or elevation (`scripts/install_tasks.ps1`; remove with `uninstall_tasks.ps1`). *Adaptive Coach - Dashboard* runs at log-on and starts the dashboard on localhost:8501 if it is not already running. *Adaptive Coach - Weekly review* runs on Saturdays at 20:05; if the PC was off, it runs when the PC is next on, and it posts only until Monday 08:00, once per week. Both run `pythonw.exe -m coach.launch …` (no console window). Output goes to `logs/` (git-ignored, redacted). The weekly-review path deliberately does not import the AI code: the Anthropic SDK hung the window-less process while loading.
- **Tailscale (2026-11-18):** stopped at the user's request (`tailscale serve` off, `tailscale down`). The design follows.
- **Phone access (2026-11-18)** goes through Tailscale: `tailscale serve` proxies an HTTPS address that only the user's own signed-in devices can reach (a private tailnet, not Funnel) to `localhost:8501`. Nothing is hosted in the cloud and the PC must be on.
- **Passcode gate** (`coach/access.py`, enforced first thing in `ui/app.py`): any request that is not plainly from this PC (a non-local `Host`, or proxy headers such as `X-Forwarded-For` / `Tailscale-User-Login`; missing headers count as remote) must enter the dashboard passcode. Remote access is refused outright until a passcode is set. Only a salted scrypt hash is stored, in Credential Manager (item `dashboard-passcode-hash`), set with `python -m coach.set_passcode` in the user's own terminal. After 5 wrong tries the server waits 60 s (one limiter for the whole server). An unlock lasts for that browser session.
- All HTTP calls have timeouts, retries with backoff, and respect documented rate limits.
- No cloud deployment, tunnels, or public webhooks without explicit approval. None are needed: the Garmin Personal API is pull-only.
- No live authenticated request to Garmin or Google without the user's approval for that step.

## Privacy
- Data stays local in SQLite. Only the minimized coaching context is sent to the LLM provider; users are told this on the Settings page.
- Retention settings for chat history and raw data; export and delete are user-triggered (delete asks for confirmation).
- The SQLite file is not encrypted at rest (it relies on OS account security/BitLocker). App-level encryption can be added later if desired.
- **Backups (2026-11-18, user-approved):** `coach.backup` copies the database and `program.json` once a day (triggered by the dashboard, or by hand) to `<OneDrive>\Adaptive Coach backups` (`COACH_BACKUP_DIR` to change). This means **health data is synced to the user's personal OneDrive**, protected by their Microsoft account. Copies are not encrypted by the app. Credentials are never included: they live only in Credential Manager. The newest 30 backups are kept (`COACH_BACKUP_KEEP`); pruning only removes the app's own `coach-YYYY-MM-DD_HHMM` folders. Restore is manual: stop the app, then copy the file back. Tests force a temporary backup folder.

## Health-safety boundaries
- Not medical advice. Pain or illness flags force conservative guardrails and suggest professional consultation.
- The engine caps progression (ACWR, weekly volume %), and the LLM cannot exceed those caps.

## Known risks
1. **Garmin response schemas are unverified.** The Swagger spec could not be retrieved automatically, and rate limits and historical depth are undocumented. Mitigation: the user saves the spec locally, mappers are validated against it, there is one approved test call per endpoint, and requests use conservative pacing (`garmin-api.md`).
2. **Garmin key lifecycle.** The key expires (30–365 days) and access tokens last 24 h with no refresh. Mitigation: re-exchange per run, handle 401 cleanly, show an expiry warning in the UI. A leaked key is revoked by deleting it on Connect web.
3. **Prompt injection via calendar invites.** Mitigated by minimization, delimiting, and no mutating tools.
4. **LLM errors in coaching.** Mitigated by deterministic guardrails, schema validation, and fallback text.
5. **Token theft from the local machine.** Mitigated by keyring storage, separate read/write tokens, and revocation instructions in the README.
6. **Data sharing terms.** The Garmin Personal API T&C (not in the public pages reviewed) may restrict sending Garmin data to third parties such as the LLM provider. The user should review them before Phase 5 uses real Garmin-derived data.
