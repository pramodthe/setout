#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export SETOUT_TEST_DATABASE_URL="${SETOUT_TEST_DATABASE_URL:-postgresql+psycopg:///postgres?host=/private/tmp&port=55432}"
.venv/bin/python -m pytest tests/test_jobs_v3.py -q --tb=short
