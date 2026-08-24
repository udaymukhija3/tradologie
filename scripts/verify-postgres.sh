#!/bin/sh
set -eu

cleanup() {
  docker compose --profile test stop postgres-test >/dev/null 2>&1 || true
  docker compose --profile test rm -f postgres-test >/dev/null 2>&1 || true
}

trap cleanup EXIT INT TERM
docker compose --profile test run --build --rm test
