#!/usr/bin/env bash
# Create (or describe) a free-tier-eligible e2-micro in us-central1.
# Requires: gcloud auth login, a project with billing, Compute Engine API enabled.
set -euo pipefail

PROJECT="${PROJECT:-$(gcloud config get-value project 2>/dev/null)}"
ZONE="${ZONE:-us-central1-a}"
NAME="${NAME:-chat-proxy}"
PORT="${PORT:-43127}"

if [[ -z "$PROJECT" || "$PROJECT" == "(unset)" ]]; then
  echo "Set a project: gcloud config set project YOUR_PROJECT_ID"
  exit 1
fi

gcloud config set project "$PROJECT"
gcloud services enable compute.googleapis.com

if gcloud compute instances describe "$NAME" --zone="$ZONE" >/dev/null 2>&1; then
  echo "Instance $NAME already exists in $ZONE"
else
  gcloud compute instances create "$NAME" \
    --zone="$ZONE" \
    --machine-type=e2-micro \
    --image-family=debian-12 \
    --image-project=debian-cloud \
    --tags=chat-proxy
fi

gcloud compute firewall-rules describe allow-chat-proxy >/dev/null 2>&1 || \
  gcloud compute firewall-rules create allow-chat-proxy \
    --allow="tcp:${PORT}" \
    --target-tags=chat-proxy \
    --description="Chat proxy uvicorn port"

echo
echo "External IP (put this on Neon IP Allow if you upgrade to Scale):"
gcloud compute instances describe "$NAME" --zone="$ZONE" \
  --format='get(networkInterfaces[0].accessConfigs[0].natIP)'