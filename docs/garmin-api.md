# Garmin Connect Personal API: integration plan

Status: **implemented and live (2026-11-17).** First approved sync: 90 days, 11 requests, 71 activities, 90 wellness days. Code: `src/coach/integrations/garmin/`.

## Sources reviewed
- Program page: https://developer.garmin.com/gc-developer-program/personal/personal-api/
- FAQ: https://developer.garmin.com/gc-developer-program/personal/personal-faq
- Swagger page: https://developer.garmin.com/gc-developer-program/personal/swagger-docs (OpenAPI 3.0.1, "Garmin Connect Personal API" 1.0.0; 26 GET endpoints, 109 schemas). It renders client-side; the embedded spec was read in a browser on 2026-11-17 and is **not** copied into the repo.
- Official client: https://github.com/garmin/connect-personal-api-python (README, `pyproject.toml`, `client.py`, `auth.py`, `constants.py`)

## What is confirmed

| Topic | Finding | Source |
|---|---|---|
| Purpose | For individual users accessing **their own** data; not for commercial multi-user apps. Fits this project. | FAQ |
| Access model | **Pull only** (GET on demand). No webhook/callback is documented, so **no public HTTPS endpoint is needed**. | FAQ, client |
| Key creation | Created by the user on Connect web (`connect.garmin.com/app/settings/personalApiToken`) after accepting T&C; expiry 30/90/180/365 days; shown **once**. Garmin emails about a week before expiry. | Program page, FAQ |
| Auth | The key is **not** used on data calls. It is exchanged for an OAuth2 access token: `POST https://personal.connectapi.garmin.com/personalapitoken-service/personal-api/personal-api-key/token` with header `Authorization: ApiKey <key>`. The response body is the raw JWT (text, not JSON). | FAQ, `auth.py` |
| Token lifetime | Valid **24 h** or until the key expires, whichever is first. **No refresh tokens**: request a new token with the key. | FAQ |
| Data calls | `Authorization: Bearer <token>`, `Accept: application/json`; base URL `https://personal.connectapi.garmin.com`. | `client.py`, `constants.py` |
| Revocation | Deleting the key on Connect web invalidates the key and all its tokens. | FAQ |
| Writes | **None documented.** The client is read-only (no workout/calendar push). | client |
| Official client | PyPI `garmin-connect-personal-api-python`, import `garmin_personal_api`, Apache-2.0, Python ≥ 3.12, depends only on `requests`. | `pyproject.toml` |

## Endpoints relevant to the coach (from the official client's wrappers; to be re-verified against the Swagger spec)

| Need | Method & path | Notes |
|---|---|---|
| Activity list | `GET /activitylist-service/activities/search/activities?start=&limit=&startDate=&endDate=&activityType=` | paginate with `start`/`limit` (client defaults 0/20) |
| Activity detail | `GET /activity-service/activity/{activityId}` | laps/splits/strength sets: **not documented**, verify in spec |
| Original file | `GET /download-service/files/activity/{activityId}` | ZIP (FIT); possible source for laps & strength sets |
| Sleep | `GET /sleep-service/sleep/dailySleepSummariesByDate?startDate=&endDate=` | |
| HRV | `GET /hrv-service/hrv/daily/{start}/{end}` | |
| Heart rate / RHR | `GET /wellness-service/wellness/dailyHeartRate?date=` | per-day |
| Daily summaries | `GET /usersummary-service/v2/dailysummaries?startDate=&endDate=` | steps, stress, body battery likely here, verify |
| Body Battery | `GET /wellness-service/wellness/bodyBattery/reports/daily?startDate=&endDate=` | |
| Stress | `GET /usersummary-service/stats/stress/daily/{start}/{end}` | |
| VO2 max | `GET /metrics-service/metrics/maxmet/daily/{start}/{end}` | useful for running performance trend |
| HR zones | `GET /biometric-service/heartRateZones/v2` | athlete profile (max HR / LTHR) |

Dates are `YYYY-MM-DD`, inclusive ranges.

## Verified from the official OpenAPI spec (2026-11-17)
| Endpoint | Limits / params | Fields used |
|---|---|---|
| `GET /activitylist-service/activities/search/activities` | `startDate`/`endDate` (inclusive, **local** start date), `start` + `limit` (default 99), `sortOrder` | `activityId`, `activityName`, `activityType.typeKey`, `startTimeGMT` ("yyyy-MM-dd HH:mm:ss", UTC; fallback `beginTimestamp` ms), `duration` s, `distance` m, `elevationGain` m, `averageHR`, `maxHR`, `activityTrainingLoad`, `aerobicTrainingEffect`, `anaerobicTrainingEffect` |
| `GET /sleep-service/sleep/dailySleepSummariesByDate` | `startDate`/`endDate`, **max 31 days** | `calendarDate`, `sleepTimeSeconds`, `sleepScores.overall.value` (map keys are not enumerated in the spec; read only if present) |
| `GET /hrv-service/hrv/daily/{fromDate}/{toDate}` | range limit undocumented, so 31-day chunks; **204 = no data** | `hrvSummaries[].calendarDate`, `.lastNightAvg` (ms) |
| `GET /usersummary-service/v2/dailysummaries` | `startDate`/`endDate`, max 1 year (we chunk 31 days) | `data[].calendarDate`, `heartRate.restingValue`, `bodyBattery.maxValue/minValue`, `stress.avgLevel` (negative = no data, stored as missing), `movement.steps.value` |

Errors per spec: 400, 401 ("authentication missing or invalid"), 403, 500. Rate limits remain undocumented; the client paces requests at least 1 s apart and retries 429/5xx with backoff (honouring `Retry-After`). `activityType.typeKey` values are not enumerated; unknown keys map to `other` and are reported by the sync (first sync: `indoor_cardio`, `jump_rope`). Skipped records are reported by reason.

Personal data in payloads (owner names, GPS coordinates, comments, notes) is **never read or stored**: the mapper picks only the fields above.

## Still open
1. Strength sets: the API only exposes category-level `summarizedExerciseSets` (category, reps, sets, max weight in grams). Mapping Garmin categories to muscle groups is pending, so strength sessions currently have no sets.
2. Terms on sending Garmin data to third parties (Anthropic): review the Personal API T&C before the AI-coach phase.

## Credential handling (stricter than the client's defaults)
The official client can read the key from `--api-key`, a plaintext key file (`~/.config/garmin/connect-personal-api-key`), or `GARMIN_PERSONAL_API_KEY`, and caches the bearer token in a plaintext JSON file. **This project uses none of those defaults.**
- The key is stored in **Windows Credential Manager** via `keyring` (service `adaptive-ai-coach`, item `garmin-personal-api-key`), entered by the user through a local prompt (`python -m coach.integrations.garmin.set_key`, using `getpass`, so nothing is echoed). It is **never** pasted into chat, `.env`, shell history, or files.
- The 24 h bearer token is held **in memory only** (re-exchanged per sync run) and is never written to disk.
- `Authorization: ApiKey …` and `Bearer …` values are masked by `coach.logging.redact` (tested).
- On 401: no retry loop; mark `sync_state` as failed with "key expired or revoked" and show the user how to create a new key. The dashboard warns 7 days before the user-entered key expiry date.

## Implementation (own minimal client, approved 2026-11-17)
`client.py` is a small `httpx` client for the two documented calls (key to token, GET). It only allows `https://personal.connectapi.garmin.com` (or `.cn`), never follows redirects (they could leak the Authorization header), and treats 401/403 as fatal with guidance to create a new key. `mapping.py` validates payloads into domain models. `sync.py` fetches first, then writes, and records `sync_state` plus an audit entry.

```bash
.venv/Scripts/python.exe -m coach.integrations.garmin.set_key            # store key (hidden prompt)
.venv/Scripts/python.exe -m coach.integrations.garmin.set_key --status   # yes/no only
.venv/Scripts/python.exe -m coach.integrations.garmin.sync --days 30     # LIVE read-only sync
```
