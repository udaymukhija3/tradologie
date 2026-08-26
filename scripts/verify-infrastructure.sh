#!/bin/sh
set -eu

project_name=tradevoice_infrastructure_verification
app_port=18080
readiness_file=$(mktemp "${TMPDIR:-/tmp}/tradevoice-readiness.XXXXXX")

cleanup() {
  APP_PORT="$app_port" docker compose -p "$project_name" down -v --remove-orphans >/dev/null 2>&1 || true
  rm -f "$readiness_file"
}
trap cleanup EXIT INT TERM

docker compose config -q

IMAGE_TAG=verification \
DATABASE_URL=postgresql+psycopg://tradevoice:secret@postgres.example.com:5432/tradevoice \
REDIS_URL=rediss://redis.example.com:6380/0 \
JWT_SECRET=verification-secret-with-more-than-32-characters \
ALLOWED_ORIGINS=https://tradevoice.example.com \
PUBLIC_BASE_URL=https://tradevoice.example.com \
docker compose -f compose.production.yaml config -q

APP_PORT="$app_port" docker compose -p "$project_name" up --build --detach

attempt=0
until curl --fail --silent --show-error "http://127.0.0.1:$app_port/api/health/ready" >"$readiness_file"; do
  attempt=$((attempt + 1))
  if [ "$attempt" -ge 60 ]; then
    docker compose -p "$project_name" ps
    docker compose -p "$project_name" logs --tail=100
    echo "Compose stack did not become ready" >&2
    exit 1
  fi
  sleep 1
done

grep -q '"status":"ready"' "$readiness_file"
curl --fail --silent --show-error "http://127.0.0.1:$app_port/" >/dev/null

# The voice socket is the product's only interactive feature, and its origin
# allowlist has to track the published port. Readiness alone cannot see this:
# the health endpoint has no origin check, so a stack serving on a non-default
# port stayed green here while the voice panel failed to connect in a browser.
upgrade_status=$(curl --silent --output /dev/null --write-out '%{http_code}' \
  --header 'Connection: Upgrade' \
  --header 'Upgrade: websocket' \
  --header 'Sec-WebSocket-Version: 13' \
  --header 'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==' \
  --header "Origin: http://127.0.0.1:$app_port" \
  "http://127.0.0.1:$app_port/ws/voice")
if [ "$upgrade_status" != "101" ]; then
  echo "Voice WebSocket upgrade returned $upgrade_status, expected 101." >&2
  echo "The backend's ALLOWED_ORIGINS probably does not include port $app_port." >&2
  exit 1
fi

./scripts/verify-backup.sh "$project_name"

echo "Production config, container readiness, voice socket, and backup recovery verified."
