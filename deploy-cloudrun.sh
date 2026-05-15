#!/usr/bin/env bash
# =============================================================================
# deploy-cloudrun.sh — one-command manual deploy to Google Cloud Run
#
# Usage:
#   export GEMINI_API_KEY=your_key
#   export GCP_PROJECT=your-project-id   # or set via: gcloud config set project
#   chmod +x deploy-cloudrun.sh
#   ./deploy-cloudrun.sh
#
# Optional overrides (all have sensible defaults):
#   SERVICE_NAME   (default: rag-policy-agent)
#   REGION         (default: asia-south1)
# =============================================================================

set -euo pipefail

# ── Configuration ─────────────────────────────────────────────────────────────
PROJECT_ID="${GCP_PROJECT:-$(gcloud config get-value project 2>/dev/null)}"
SERVICE_NAME="${SERVICE_NAME:-rag-policy-agent}"
REGION="${REGION:-asia-south1}"
IMAGE="gcr.io/${PROJECT_ID}/${SERVICE_NAME}"

if [[ -z "${GEMINI_API_KEY:-}" ]]; then
  echo "❌ GEMINI_API_KEY is not set. Export it before running this script."
  exit 1
fi

if [[ -z "${PROJECT_ID}" ]]; then
  echo "❌ GCP project not set. Run: gcloud config set project <your-project-id>"
  exit 1
fi

echo ""
echo "🚀  RAG Policy Agent — Cloud Run deployment"
echo "    Project      : ${PROJECT_ID}"
echo "    Service      : ${SERVICE_NAME}"
echo "    Region       : ${REGION}"
echo "    Image        : ${IMAGE}"
echo ""

# ── 1. Enable required GCP APIs ───────────────────────────────────────────────
echo "📦  Enabling GCP APIs…"
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  containerregistry.googleapis.com \
  --project="${PROJECT_ID}" \
  --quiet

# ── 2. Build & push image via Cloud Build ─────────────────────────────────────
# No local Docker daemon needed — Cloud Build runs in GCP.
echo "🔨  Building image with Cloud Build (this takes a few minutes)…"
gcloud builds submit \
  --tag "${IMAGE}:latest" \
  --project="${PROJECT_ID}" \
  --timeout=30m \
  .

# ── 3. Deploy to Cloud Run ────────────────────────────────────────────────────
echo "☁️   Deploying to Cloud Run…"
gcloud run deploy "${SERVICE_NAME}" \
  --image "${IMAGE}:latest" \
  --platform managed \
  --region "${REGION}" \
  --allow-unauthenticated \
  --memory 2Gi \
  --cpu 2 \
  --timeout 300 \
  --concurrency 4 \
  --min-instances 0 \
  --max-instances 5 \
  --set-env-vars "GEMINI_API_KEY=${GEMINI_API_KEY}" \
  --project="${PROJECT_ID}"

# ── 4. Print service details ──────────────────────────────────────────────────
URL=$(gcloud run services describe "${SERVICE_NAME}" \
  --region="${REGION}" \
  --project="${PROJECT_ID}" \
  --format='value(status.url)')

echo ""
echo "✅  Deployed successfully!"
echo ""
echo "    Service URL  : ${URL}"
echo "    Health check : ${URL}/health"
echo "    Web UI       : ${URL}/"
echo "    API docs     : ${URL}/docs"
echo ""
echo "💡  In the UI sidebar, set API Base URL to: ${URL}"
