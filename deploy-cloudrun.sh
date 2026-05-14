#!/usr/bin/env bash
# deploy-cloudrun.sh — one-command deploy to Google Cloud Run
# Usage: GEMINI_API_KEY=xxx ./deploy-cloudrun.sh

set -euo pipefail

# ── Configuration (edit these) ────────────────────────────────────────────────
PROJECT_ID="${GCP_PROJECT:-$(gcloud config get-value project)}"
SERVICE_NAME="${SERVICE_NAME:-rag-policy-agent}"
REGION="${REGION:-asia-south1}"          # Mumbai — close to India
IMAGE="gcr.io/${PROJECT_ID}/${SERVICE_NAME}"

echo "🚀 Deploying ${SERVICE_NAME} to Cloud Run (${REGION})…"
echo "   Project : ${PROJECT_ID}"
echo "   Image   : ${IMAGE}"

# ── 1. Ensure required APIs are enabled ──────────────────────────────────────
echo "📦 Enabling GCP APIs…"
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  containerregistry.googleapis.com \
  --project="${PROJECT_ID}" --quiet

# ── 2. Build & push image via Cloud Build (no local Docker needed) ────────────
echo "🔨 Building image with Cloud Build…"
gcloud builds submit \
  --tag "${IMAGE}" \
  --project="${PROJECT_ID}" \
  --timeout=20m \
  .

# ── 3. Deploy to Cloud Run ────────────────────────────────────────────────────
echo "☁️  Deploying to Cloud Run…"
gcloud run deploy "${SERVICE_NAME}" \
  --image "${IMAGE}" \
  --platform managed \
  --region "${REGION}" \
  --allow-unauthenticated \
  --memory 2Gi \
  --cpu 2 \
  --timeout 300 \
  --concurrency 4 \
  --min-instances 0 \
  --max-instances 3 \
  --set-env-vars "GEMINI_API_KEY=${GEMINI_API_KEY}" \
  --project="${PROJECT_ID}"

# ── 4. Print the service URL ──────────────────────────────────────────────────
URL=$(gcloud run services describe "${SERVICE_NAME}" \
  --region="${REGION}" --project="${PROJECT_ID}" \
  --format='value(status.url)')

echo ""
echo "✅ Deployed successfully!"
echo "   Service URL : ${URL}"
echo "   Health      : ${URL}/health"
echo "   UI          : ${URL}/"
echo ""
echo "💡 Update the API Base URL in the UI sidebar to: ${URL}"
