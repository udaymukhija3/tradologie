.PHONY: install migrate seed verify-core verify verify-postgres backend frontend docker-up docker-down docker-verify

install:
	python3 -m venv backend/venv
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
	docker compose build
