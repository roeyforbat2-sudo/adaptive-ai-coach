# Runs the Postgres tests in a throw-away schema (coach_pytest) of the database saved with
#   python -m coach.set_db_url   (or --test). Real tables are never touched.
# The URL is read from Windows Credential Manager inside the test process (never printed).
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$env:COACH_PG_TESTS = "1"
& (Join-Path $root ".venv\Scripts\python.exe") -m pytest -m postgres -q @args
