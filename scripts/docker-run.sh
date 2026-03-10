#!/usr/bin/env bash
set -euo pipefail

IMAGE_NAME="${IMAGE_NAME:-ai-enable-discovery:latest}"
CONTAINER_NAME="${CONTAINER_NAME:-discovery-app}"
PORT="${PORT:-8000}"

docker build -t "${IMAGE_NAME}" .
docker rm -f "${CONTAINER_NAME}" >/dev/null 2>&1 || true

if grep -Eq '^DB_BACKEND=mongodb$' .env; then
  docker run -d --name "${CONTAINER_NAME}" -p "${PORT}:8000" \
    --env-file .env \
    "${IMAGE_NAME}"
else
  mkdir -p data reports
  docker run -d --name "${CONTAINER_NAME}" -p "${PORT}:8000" \
    --env-file .env \
    -e SQLITE_DB_PATH=/app/data/sessions.db \
    -e LOCAL_REPORTS_DIR=/app/reports \
    -v "$(pwd)/data:/app/data" \
    -v "$(pwd)/reports:/app/reports" \
    "${IMAGE_NAME}"
fi

echo "App started: http://localhost:${PORT} (container: ${CONTAINER_NAME})"
