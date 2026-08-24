.PHONY: install migrate seed verify-core verify verify-postgres verify-infrastructure backup verify-backup backend frontend docker-up docker-down docker-verify

PYTHON ?= python3.12

install:
	$(PYTHON) -m venv backend/venv
	backend/venv/bin/python -m pip install -r backend/requirements-dev.txt
	cd frontend && npm ci

migrate:
	cd backend && venv/bin/alembic -c alembic.ini upgrade head

seed:
	cd backend && venv/bin/python -m app.seed

verify-core:
	cd backend && venv/bin/python -m pytest -q
	cd frontend && npm run lint
	cd frontend && npm run build

verify: verify-core
	cd frontend && npm run test:e2e

verify-postgres:
	./scripts/verify-postgres.sh

verify-infrastructure:
	./scripts/verify-infrastructure.sh

backup:
	./scripts/backup-postgres.sh

verify-backup:
	./scripts/verify-backup.sh

backend:
	cd backend && venv/bin/alembic -c alembic.ini upgrade head && venv/bin/python -m app.seed && venv/bin/python -m app.main

frontend:
	cd frontend && npm run dev

docker-up:
	docker compose up --build

docker-down:
	docker compose down

docker-verify:
	docker compose config -q
	IMAGE_TAG=verification DATABASE_URL=postgresql+psycopg://tradevoice:secret@postgres.example.com:5432/tradevoice REDIS_URL=rediss://redis.example.com:6380/0 JWT_SECRET=verification-secret-with-more-than-32-characters ALLOWED_ORIGINS=https://tradevoice.example.com PUBLIC_BASE_URL=https://tradevoice.example.com docker compose -f compose.production.yaml config -q
	docker compose build
