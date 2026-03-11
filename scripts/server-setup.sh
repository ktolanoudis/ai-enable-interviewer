#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

if ! command -v docker >/dev/null 2>&1; then
  echo "Error: docker is not installed."
  exit 1
fi

if docker compose version >/dev/null 2>&1; then
  COMPOSE_CMD="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE_CMD="docker-compose"
else
  echo "Error: Docker Compose is not available (docker compose or docker-compose)."
  exit 1
fi

if [ ! -f .env ]; then
  echo "Error: .env not found. Create it first (e.g. cp .env.example .env)."
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

is_placeholder_image() {
  local image="$1"
  [ -z "${image}" ] \
    || [ "${image}" = "ghcr.io/your-org/discovery:latest" ] \
    || [ "${image}" = "ghcr.io/<owner>/<repo>:latest" ] \
    || [[ "${image}" == *"<"*">"* ]]
}

openai_key="$(resolve_value OPENAI_API_KEY "")"
if [ -z "${openai_key}" ] || [ "${openai_key}" = "sk-your-openai-key" ]; then
  echo "Error: OPENAI_API_KEY is missing or still set to placeholder in .env."
  exit 1
fi

discovery_image="$(resolve_value DISCOVERY_IMAGE "")"
if is_placeholder_image "${discovery_image}"; then
  remote_url="$(git config --get remote.origin.url || true)"
  repo_path=""

  if [[ "${remote_url}" =~ ^git@github\.com:(.+)\.git$ ]]; then
    repo_path="${BASH_REMATCH[1]}"
  elif [[ "${remote_url}" =~ ^https://github\.com/(.+)\.git$ ]]; then
    repo_path="${BASH_REMATCH[1]}"
  elif [[ "${remote_url}" =~ ^https://github\.com/(.+)$ ]]; then
    repo_path="${BASH_REMATCH[1]}"
  fi

  if [ -n "${repo_path}" ]; then
    discovery_image="ghcr.io/${repo_path}:latest"
    export DISCOVERY_IMAGE="${discovery_image}"
    if ! grep -qE '^DISCOVERY_IMAGE=' .env; then
      echo "DISCOVERY_IMAGE=${discovery_image}" >> .env
    fi
  fi
fi

if is_placeholder_image "${discovery_image}"; then
  echo "Error: Could not determine DISCOVERY_IMAGE."
  echo "Set DISCOVERY_IMAGE in .env, e.g. ghcr.io/<owner>/<repo>:latest"
  exit 1
fi

ghcr_user="$(resolve_value GHCR_USERNAME "")"
ghcr_token="$(resolve_value GHCR_TOKEN "")"
if [ -n "${ghcr_user}" ] && [ -n "${ghcr_token}" ]; then
  echo "${ghcr_token}" | docker login ghcr.io -u "${ghcr_user}" --password-stdin
fi

mkdir -p data reports

${COMPOSE_CMD} pull
${COMPOSE_CMD} up -d --remove-orphans

host_port="$(resolve_value HOST_PORT "$(resolve_value PORT 8000)")"

echo "Deployment complete."
echo "Image: ${discovery_image}"
echo "App URL: http://localhost:${host_port}"
