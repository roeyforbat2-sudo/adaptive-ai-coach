# Cloud deployment (Google Cloud, free tier)

## Live setup (2026-11-19)

| What | Value |
|---|---|
| App | Cloud Run service `coach`, europe-west1, **gen2** (gen1 hung at start), max 1 instance, request-based billing. URL: `https://coach-<PROJECT_NUMBER>.europe-west1.run.app` |
| Login | IAP directly on Cloud Run with the custom OAuth client `coach-iap`; only the user's account is allowed. The app also verifies the IAP JWT (aud `/projects/<PROJECT_NUMBER>/locations/europe-west1/services/coach`). |
| Identity | Service account `coach-runner`: reads only its secrets, writes only the backup bucket, and has **no project-wide roles**. The user shared their Google Calendar with it ("make changes to events"). Unsharing cuts its access. |
| Calendar | `COACH_GOOGLE_CALENDAR_AUTH=service_account`, `COACH_GOOGLE_CALENDAR_OWNER=<user's calendar>`. No refresh tokens, so nothing expires. The consent screen can stay in Testing. |
| Secrets (3) | `coach-database-url`, `coach-garmin-key`, `coach-program`. The refresh-token and OAuth-client secrets were deleted on 2026-11-19 (service-account mode doesn't need them). |
| Jobs | Cloud Run jobs `coach-morning-brief`, `coach-weekly-review`, `coach-nightly` (same image; the nightly job mounts the bucket at `/backups`) |
| Schedules | Cloud Scheduler (europe-west1, Asia/Jerusalem), run by the `coach-scheduler` service account, which has only `run.invoker` on the 3 jobs: 05:25 daily, Saturday 20:05, 03:00 daily |
| Backups | `gs://<BACKUP_BUCKET>` (us-central1, public access prevention, 40-day delete rule) |
| Cost guards | A ₪3/month **hard cap** on Cloud Run (it pauses rather than charges), and a ₪4/month all-services email alert that counts real cost, credits excluded |
| PC | The Windows tasks are **disabled** (re-enable with `Enable-ScheduledTask`), and the local dashboard is stopped. The local SQLite file and OneDrive backups stay as a fallback. |

**Redeploy:** `gcloud run deploy coach --source . --region europe-west1`, then point the 3 jobs at the new image (`gcloud run jobs update <job> --image <image>`).
**One posting path:** in the cloud only the jobs post the brief, review and backup; the dashboard doesn't. `upsert_tagged_event` removes duplicates of the app's own events, and the calendar reader ignores those events.

## Original plan

Decided 2026-11-18. The user chose Google Cloud over a no-card setup and wants it to cost **$0**. Everything below fits the free tiers, verified on Google's pricing pages on 2026-11-18. Free tiers can change with 30 days' notice, so re-check before relying on them.

## Architecture

| Piece | Service | Free tier used |
|---|---|---|
| Dashboard | **Cloud Run** service `coach` (europe-west1), max 1 instance, request-based billing | 180k vCPU-s, 360k GiB-s, 2M requests/month |
| Login | **IAP enabled directly on Cloud Run** (no load balancer), Google sign-in, allow-list of the user's email | IAP itself: no charge |
| Secrets | **Secret Manager** → injected as env vars / one file | 6 active secret versions, 10k accesses/month |
| Jobs | **Cloud Run jobs** `morning-brief`, `weekly-review`, `nightly` started by **Cloud Scheduler** | exactly 3 free scheduler jobs per billing account |
| Database | **Neon** Postgres, free plan, Frankfurt (not Google) | 1 GB storage (we use < 1 MB), no card |
| Backups | **Cloud Storage** bucket in us-central1, mounted at `/backups` in the nightly job | 5 GB-months (US regions only) |
| Image | **Cloud Build** + **Artifact Registry** | 2,500 build-min; 0.5 GB storage (the image may exceed this by a little: cents/month; keep 1 version) |

## Secrets (original plan: 6 = the free limit; only 3 remain live, see the table at the top)

| Secret Manager name | Becomes | Content |
|---|---|---|
| `coach-database-url` | `COACH_DATABASE_URL` | Neon connection URL (TLS) |
| `coach-garmin-key` | `COACH_SECRET_GARMIN_PERSONAL_API_KEY` | Garmin Personal API key |
| `coach-gcal-read` | `COACH_SECRET_GOOGLE_CALENDAR_READONLY_TOKEN` | Google refresh token, read-only |
| `coach-gcal-write` | `COACH_SECRET_GOOGLE_CALENDAR_WRITE_TOKEN` | Google refresh token, calendar.events |
| `coach-oauth-client` | file `/secrets/oauth/client_secret.json` (`GOOGLE_OAUTH_CLIENT_FILE`) | Desktop OAuth client |
| `coach-program` | file `/secrets/program/program.json` (`COACH_PROGRAM_FILE`) | the training program (personal) |

The values are copied from the local keyring and files by the user's own terminal session. They are never pasted into chat, logs or command lines.

## Other environment (not secret)

`COACH_AUTH_MODE=iap` (set in the image) · `COACH_IAP_AUDIENCE=<the service's IAP audience>` · `COACH_ALLOWED_EMAILS=<the user's Google email>` · `COACH_TIMEZONE=Asia/Jerusalem` (image) · `COACH_BACKUP_DIR=/backups` (nightly job).

## Security

- **Two locks on the door.** IAP blocks everyone except the allowed account before a request reaches the container. The app *also* verifies IAP's signed header on every session (signature against Google's IAP keys, issuer, audience, expiry, allow-listed email) and fails closed.
- **Nothing personal in the image** (`.dockerignore`): no `data/`, `secrets/`, `.env`, program file or tests. The container runs as a non-root user.
- **Streamlit hardened via env:** XSRF protection on, error details hidden, no file watcher, 1 MB upload cap, telemetry off.
- **Least-privilege service account** for the service and jobs: read only the secrets it needs, write only to the backup bucket.
- **TLS everywhere:** Cloud Run HTTPS, Neon `sslmode=require` (enforced in code). The dashboard's database session is read-only.
- **Redaction:** logs mask bearer and API keys, Google tokens, emails, Postgres URL passwords and `password=` pairs.
- **Cost guard:** max 1 instance, $1 budget alert, an Artifact Registry cleanup policy keeping 1 image.
- Calendar writes keep their existing limits: read-only sync; writes only of the app's tagged events or after an explicit click.

## Phase 1: code (done, local)

Postgres support behind the same API (`coach/db/pg.py`, `connection.py`; SQLite stays for local runs and tests). Secrets from the environment (`coach/secrets.py`). IAP login check (`coach/access.py`). Jobs (`coach/jobs.py`). Cloud backups as a JSON export (`coach/backup.py`). Copy tool (`python -m coach.db.copy`). `Dockerfile`, `.dockerignore`.

## Phase 2: accounts (the user, with guidance)

1. Google Cloud: add a billing account (card; 90-day trial, no automatic charging) and link it to the project. Then create a **$1 budget alert**.
2. Neon: free account → project `coach` in **AWS Frankfurt (eu-central-1)** → copy the connection string into a local, git-ignored place only, never into chat. Create a second, disposable database `coach_test` for the Postgres tests.
3. Run the Postgres tests locally against `coach_test`, with `COACH_TEST_DATABASE_URL` set in your own terminal.

## Phase 3: deploy (each step with the user's OK)

1. Install the Google Cloud CLI (official installer).
2. Enable the APIs: Cloud Run, Cloud Build, Artifact Registry, Secret Manager, Cloud Scheduler, IAP.
3. Create the 6 secrets from the local values.
4. Copy the data: `python -m coach.db.copy --from data/coach.sqlite --to-env`.
5. Deploy the service (`gcloud run deploy --source .`), max 1 instance, IAP on, allow the user's email, and set `COACH_IAP_AUDIENCE`.
6. Create the 3 jobs and their schedules (Asia/Jerusalem: 05:25 daily, Sat 20:05, 03:00 daily). The nightly job gets the backup bucket mounted.
7. Test from the phone. Then disable the Windows tasks; local OneDrive backups stay until the cloud ones are proven.
