#!/bin/sh
set -eu

destination=${1:-"tradevoice-$(date -u +%Y%m%dT%H%M%SZ).dump"}
destination_directory=$(dirname "$destination")

if [ ! -d "$destination_directory" ]; then
  echo "Destination directory does not exist: $destination_directory" >&2
  exit 1
fi

umask 077
docker compose exec -T postgres \
  pg_dump --username tradevoice --dbname tradevoice --format=custom > "$destination"

if [ ! -s "$destination" ]; then
  echo "Backup was empty: $destination" >&2
  exit 1
fi

echo "Created PostgreSQL backup: $destination"
