#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

if [ ! -f .env ]; then
  echo "Error: .env not found. Create it first (e.g. cp .env.example .env)."
  exit 1
fi

openai_key="$(grep -E '^OPENAI_API_KEY=' .env | tail -n 1 | cut -d= -f2- || true)"
if [ -z "${openai_key}" ] || [ "${openai_key}" = "sk-your-openai-key" ]; then
  echo "Error: OPENAI_API_KEY is missing or still set to placeholder in .env."
  exit 1
fi

if [ ! -x scripts/deploy-prod.sh ]; then
  chmod +x scripts/deploy-prod.sh
fi

if [ ! -x scripts/deploy-prod.sh ]; then
  echo "Error: scripts/deploy-prod.sh is missing or not executable."
  exit 1
fi

./scripts/deploy-prod.sh
