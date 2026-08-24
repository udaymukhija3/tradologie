#!/bin/sh
set -eu

E2E_DATABASE_PATH="/tmp/tradevoice-e2e-$$.db"
export DATABASE_URL="sqlite:///$E2E_DATABASE_PATH"
export AUTO_CREATE_SCHEMA=false
export AUTO_SEED=false
export REDIS_URL=redis://127.0.0.1:6399/15
export JWT_SECRET=e2e-secret-that-is-long-enough-for-tests
export ALLOWED_ORIGINS=http://127.0.0.1:15173
export PYTHONPYCACHEPREFIX=/tmp/tradevoice_e2e_pycache

cd ../backend
if [ -x venv/bin/python ]; then
  PYTHON_BIN=venv/bin/python
else
  PYTHON_BIN=python3
fi

"$PYTHON_BIN" -m alembic -c alembic.ini upgrade head
"$PYTHON_BIN" -m app.seed
exec "$PYTHON_BIN" -m uvicorn app.main:app --host 127.0.0.1 --port 18000
