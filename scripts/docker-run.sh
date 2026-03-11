#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

IMAGE_NAME="${IMAGE_NAME:-ai-enable-discovery:latest}"
CONTAINER_NAME="${CONTAINER_NAME:-discovery-app}"

if [ ! -f .env ]; then
  echo "Error: .env not found in ${ROOT_DIR}. Create it first (e.g. cp .env.example .env)."
  exit 1
fi

invalid_env_lines="$(grep -nEv '^[[:space:]]*($|#|[A-Za-z_][A-Za-z0-9_]*=)' .env || true)"
if [ -n "${invalid_env_lines}" ]; then
  echo "Error: .env contains invalid lines. Use KEY=value format with no spaces around '='."
  echo "${invalid_env_lines}"
  exit 1
fi

env_file_value() {
  local key="$1"
  local line
  line="$(grep -E "^${key}=" .env | tail -n 1 || true)"
  printf '%s' "${line#*=}"
}

resolve_value() {
  local key="$1"
  local fallback="$2"
  local current="${!key:-}"
  if [ -n "${current}" ]; then
    printf '%s' "${current}"
    return
  fi
  local from_file
  from_file="$(env_file_value "${key}")"
  if [ -n "${from_file}" ]; then
    printf '%s' "${from_file}"
    return
  fi
  printf '%s' "${fallback}"
}

container_port="$(resolve_value PORT 8000)"
host_port="$(resolve_value HOST_PORT "${container_port}")"
db_backend="$(resolve_value DB_BACKEND sqlite)"
db_backend="$(printf '%s' "${db_backend}" | tr '[:upper:]' '[:lower:]')"
mongodb_uri="$(resolve_value MONGODB_URI "")"

if ! docker build -t "${IMAGE_NAME}" .; then
  config_dir="${DOCKER_CONFIG:-${HOME}/.docker}"
  if [ -f "${config_dir}/config.json" ] && grep -q '"credsStore"[[:space:]]*:[[:space:]]*"desktop\.exe"' "${config_dir}/config.json"; then
    echo "Hint: Docker is configured with Windows credential helper in Linux."
    echo "Set ${config_dir}/config.json to: {\"auths\":{}}"
  fi
  exit 1
fi
docker rm -f "${CONTAINER_NAME}" >/dev/null 2>&1 || true

if [ "${db_backend}" = "mongodb" ] && [ -n "${mongodb_uri}" ]; then
  docker run -d --name "${CONTAINER_NAME}" -p "${host_port}:${container_port}" \
    --env-file .env \
    "${IMAGE_NAME}"
else
  mkdir -p data reports
  docker run -d --name "${CONTAINER_NAME}" -p "${host_port}:${container_port}" \
    --env-file .env \
    -e SQLITE_DB_PATH=/app/data/sessions.db \
    -e LOCAL_REPORTS_DIR=/app/reports \
    -v "$(pwd)/data:/app/data" \
    -v "$(pwd)/reports:/app/reports" \
    "${IMAGE_NAME}"
fi

echo "App started: http://localhost:${host_port} (container: ${CONTAINER_NAME})"
