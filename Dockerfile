# Adaptive AI Coach: one image for the Cloud Run service (dashboard) and the 3 Cloud Run jobs.
#   service: default CMD (Streamlit on :8080, behind Google IAP)
#   jobs:    python -m coach.jobs morning-brief | weekly-review | nightly
# Nothing personal is baked in: no data/, secrets/, .env or program.json (see .dockerignore).
# Secrets and the program file come from Secret Manager at run time.
FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    # Streamlit, hardened for the cloud (env vars override .streamlit/config.toml):
    STREAMLIT_SERVER_ADDRESS=0.0.0.0 \
    STREAMLIT_SERVER_PORT=8080 \
    STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_SERVER_ENABLE_XSRF_PROTECTION=true \
    STREAMLIT_SERVER_FILE_WATCHER_TYPE=none \
    STREAMLIT_SERVER_MAX_UPLOAD_SIZE=1 \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false \
    STREAMLIT_CLIENT_SHOW_ERROR_DETAILS=none \
    STREAMLIT_CLIENT_TOOLBAR_MODE=minimal \
    # The app: cloud login only, Israel time, data and secrets from the environment.
    COACH_ENV=production \
    COACH_AUTH_MODE=iap \
    COACH_TIMEZONE=Asia/Jerusalem

WORKDIR /app

# Dependencies first (cached layer), then the code.
COPY pyproject.toml ./
COPY src ./src
COPY .streamlit ./.streamlit
RUN pip install --upgrade pip && pip install . && \
    useradd --create-home --uid 10001 coach && \
    mkdir -p /backups && chown coach /backups

# Never run as root.
USER coach

EXPOSE 8080
CMD ["python", "-m", "streamlit", "run", "src/coach/ui/app.py"]
