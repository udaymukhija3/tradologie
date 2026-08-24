#!/bin/sh
set -eu

alembic -c alembic.ini upgrade head
python -m app.seed
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips="*"
