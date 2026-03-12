#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

BRANCH="${BRANCH:-main}"

if ! command -v git >/dev/null 2>&1; then
  echo "Error: git is not installed."
  exit 1
fi

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

git fetch origin "${BRANCH}"
git checkout "${BRANCH}"
git pull --ff-only origin "${BRANCH}"

mkdir -p data reports

${COMPOSE_CMD} -f compose.prod.yml up -d --build --remove-orphans

echo "Deployment complete from source."
