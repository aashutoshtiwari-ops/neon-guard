#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ ! -f .env ]]; then
  echo "Create .env first (copy .env.example and add AI_GATEWAY_API_KEY and DATABASE_URL)."
  exit 1
fi
sudo apt-get update -qq
sudo apt-get install -y -qq python3 python3-venv python3-pip
python3 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -r requirements.txt
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-43127}" --reload --reload-dir app