#!/bin/sh
set -eu

project_name=${1:-tradevoice}
restore_database=tradevoice_restore_verification
archive=$(mktemp "${TMPDIR:-/tmp}/tradevoice-backup.XXXXXX")

cleanup() {
  docker compose -p "$project_name" exec -T postgres \
    dropdb --username tradevoice --if-exists "$restore_database" >/dev/null 2>&1 || true
  rm -f "$archive"
}
trap cleanup EXIT INT TERM

docker compose -p "$project_name" exec -T postgres \
  pg_dump --username tradevoice --dbname tradevoice --format=custom > "$archive"
test -s "$archive"

docker compose -p "$project_name" exec -T postgres \
  createdb --username tradevoice "$restore_database"
docker compose -p "$project_name" exec -T postgres \
  pg_restore --username tradevoice --dbname "$restore_database" --no-owner < "$archive"

workspace_count=$(docker compose -p "$project_name" exec -T postgres \
  psql --username tradevoice --dbname "$restore_database" --tuples-only --no-align \
  --command "SELECT count(*) FROM workspaces;")

if [ "$workspace_count" -lt 1 ]; then
  echo "Restored backup did not contain seeded workspaces" >&2
  exit 1
fi

echo "Backup restore verified with $workspace_count workspace(s)."
