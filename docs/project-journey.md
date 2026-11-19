# Project journey: what we built and why

This is a plain-language account of how the Adaptive AI Coach came together over three days, 2026-11-17 to 2026-11-19. It covers the decisions made along the way, the problems we hit, and how each one was solved. The technical reference is in the other docs; this page is the story.

## Ground rules (set on day one, kept throughout)

- Work in small phases. Each phase ends with green tests and updated docs, then stops for review.
- **Ask before** installing anything, making a live call to Garmin or Google, changing the calendar, deploying, or running anything destructive.
- **Credentials never** go into code, chat, logs or Git.
- Use the **official** Garmin Personal API only.
- Google access starts read-only.
- Tests use **synthetic data** and never touch the network.
- It must cost nothing. The coaching is rule-based, so there's no paid AI.

---

## Day 1 (2026-11-17): from nothing to a working coach

**1. Foundations.** The project skeleton came first:
- typed settings and a logger that redacts secrets;
- a SQLite database with strict tables and forward-only migrations;
- Pydantic models that check all incoming data;
- a **synthetic-data generator**, so everything could be built and tested before any real account was connected.

**2. Dashboard.** A Streamlit and Plotly dashboard with Overview, Running, Strength, Recovery and Training Calendar tabs. Whenever fake data is shown, a "DEMO DATA" banner appears. Chart colors are fixed by sport (running blue, strength orange), and no chart uses a second y-axis.

**3. Real data.**
- **Garmin:** connected through the official Personal API. Every field was checked against Garmin's published spec, and the key is stored in Windows Credential Manager.
- **Google Calendar:** connected read-only. Training events titled "ריצה" (run) and "מכון" (gym) become the planned sessions.

**4. The coach.**
- Rule-based coaching: goals (a half marathon and a marathon), and deterministic **safety rules** (HRV, resting HR, sleep, volume jumps, pain or illness) that always win.
- A Today tab with the day's session.
- An AI path using Claude was built and tested, then **switched off**, because paid API calls weren't wanted.

**5. Training program.** The athlete's Hebrew training plan was translated into a structured `program.json`. It's kept private and out of Git. The Today tab shows today and tomorrow from it, plus the full program tables.

## Day 2 (2026-11-18): smarter, prettier, automatic

**6. Plan → Google Calendar.** Every future session is written to the calendar as a 06:00–07:00 event, with the session as the title and the full workout in the description.
- **Preview first, then one click**: nothing is written without that.
- Existing events are updated in place, nothing is deleted, and every write is logged.
- *Problem:* 38 events in a repeating series kept looking "changed" because their etags were stale. *Fix:* compare the actual content instead.

**7. Smart feedback.**
- A **"Coach's take"** banner that analyses the latest workout against the plan.
- A **weekly review** posted to the calendar every Saturday at 20:00, covering this week against the plan and suggestions for next week.

**8. Redesign.** A dark theme, HTML cards, a sidebar with the week's progress ring and sync status, and a layout that works at phone width.

**9. Phone access, attempt 1 (Tailscale).** A private network plus a passcode gate. The phone couldn't resolve the address, so this was set aside.

**10. Reliability.**
- A local Git repository.
- **Daily backups** of the database and program.
- **Windows scheduled tasks** that start the dashboard at log-on and post the review on time.
- *Problem:* the window-less scheduled task hung. *Cause:* importing the Anthropic SDK under `pythonw`. *Fix:* keep AI imports off scheduled paths.

**11. The daily loop.**
- A **05:30 morning brief** with the day's workout.
- A **17:30 check-in** (effort, soreness, sleep, pain or illness). Check-ins feed the workout analysis, the weekly review and the safety rules.

## Days 2–3 (2026-11-18 → 19): moving to the cloud, for free

The goal: open the coach from the phone with the PC switched off, keep it secure, and pay nothing.

**12. Choosing the platform.** Google Cloud's free tier was chosen:
- **Cloud Run** for the dashboard;
- **Cloud Run jobs + Cloud Scheduler** for the brief, review and nightly sync;
- **Secret Manager** for credentials;
- **Cloud Storage** for backups;
- **Neon** (a free Postgres provider, Frankfurt) for the database.

Billing safety comes from a hard spending cap on Cloud Run plus a budget alert.

**13. Code changes for the cloud.**
- The app speaks both SQLite and **Postgres** through one interface: a small wrapper makes psycopg behave like sqlite3, and the migrations are translated for Postgres.
- Secrets can come from environment variables.
- A copy tool moved the data from SQLite into Postgres.
- A hardened Docker image runs as a non-root user and contains nothing personal.

**14. Security: two locks on the door.**
- **Identity-Aware Proxy** lets only the owner's Google account through.
- The app **also** verifies IAP's signed token itself and refuses access if any check fails.

Problems along the way:
- The first deploy hung at startup. Switching to Cloud Run's **gen2** environment fixed it.
- IAP needed its own OAuth client.
- That client's secret was briefly exposed in a tool's output, so it was **rotated** right away, and the old one was disabled and then deleted.

**15. Calendar without expiring tokens.** Google's consent screen couldn't be published without an owned domain. That would have meant refresh tokens expiring every 7 days. Instead, the owner **shared their calendar with the app's own service account**, so no tokens are stored and nothing expires.

**16. Polishing the cloud setup.**
- *A brief was posted twice.* Cause: the dashboard and a scheduled task raced each other. Fixes:
  - only the jobs post in the cloud;
  - the writer cleans up duplicates of its own events.
- *The brief's own calendar event was read as a training session.* Fix: the app tags its own events and the reader skips them.
- *The cloud backup failed.* Cloud Storage can't rename folders, so backups are now copied and a manifest is written last.

**17. Go-live (2026-11-19).** The nightly job was verified end to end: Garmin activities synced, 65 calendar sessions read, and a backup written. The Windows tasks were disabled, so the PC is no longer needed. Leftover secrets (old refresh tokens, an unused OAuth client) were deleted, leaving only three.

---

## Where it stands

| | |
|---|---|
| Runs on | Google Cloud (Cloud Run + IAP), Neon Postgres. About $0/month. |
| Daily rhythm | 05:25 brief · 17:30 check-in · Saturday 20:05 review · 03:00 sync + backup |
| Code | ~11k lines of Python in `src/`, ~4.4k lines of tests (about 390 tests, all on synthetic data) |
| Next | Multi-user: anyone connects their Garmin and gets a personalized plan for their race |

## Lessons worth keeping

- **Build on synthetic data first.** The whole app was usable and tested before any real account was connected.
- **Keep safety rules deterministic and separate.** Suggestions can change; the guardrails can't be argued with.
- **Content beats metadata.** Comparing what an event actually says was more reliable than trusting etags.
- **Tag everything you write.** The app's own calendar events carry a private tag, so it can update or deduplicate them and never mistake them for training.
- **Prefer identities over tokens.** A shared service account replaced refresh tokens that would have expired.
- **Free tiers work if you guard them.** Max one instance, request-based billing, a hard cap and an alert.
