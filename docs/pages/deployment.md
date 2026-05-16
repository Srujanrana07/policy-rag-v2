# Deployment — Cloud Run

Everything needed to deploy the RAG Policy Agent to Google Cloud Run — both the one-shot script and the automated CI/CD pipeline.

---

## Prerequisites

- Google Cloud project with billing enabled
- `gcloud` CLI installed and authenticated (`gcloud auth login`)
- A Gemini API key from [AI Studio](https://aistudio.google.com/)

---

## Option A — One-Shot Script

`deploy-cloudrun.sh` handles everything in one command. No local Docker daemon needed — it uses Cloud Build to build and push the image remotely.

```bash
export GCP_PROJECT=your-project-id
export GEMINI_API_KEY=your-gemini-key

chmod +x deploy-cloudrun.sh
./deploy-cloudrun.sh
```

### What the script does

**Step 1 — Enable GCP APIs**

```bash
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  containerregistry.googleapis.com
```

**Step 2 — Build and push via Cloud Build**

```bash
gcloud builds submit \
  --tag "gcr.io/${PROJECT_ID}/${SERVICE_NAME}:latest" \
  --timeout=30m \
  .
```

The build runs in GCP. Your local machine only needs `gcloud` installed — not Docker, not Python, not Ghostscript.

**Step 3 — Deploy to Cloud Run**

```bash
gcloud run deploy rag-policy-agent \
  --image "gcr.io/${PROJECT_ID}/${SERVICE_NAME}:latest" \
  --platform managed \
  --region asia-south1 \
  --allow-unauthenticated \
  --memory 2Gi \
  --cpu 2 \
  --timeout 300 \
  --concurrency 4 \
  --min-instances 0 \
  --max-instances 5 \
  --set-env-vars "GEMINI_API_KEY=${GEMINI_API_KEY}"
```

**Step 4 — Print the URL**

```
✅  Deployed successfully!

    Service URL  : https://rag-policy-agent-xxxx-el.a.run.app
    Health check : https://rag-policy-agent-xxxx-el.a.run.app/health
    Web UI       : https://rag-policy-agent-xxxx-el.a.run.app/
    API docs     : https://rag-policy-agent-xxxx-el.a.run.app/docs
```

### Configurable variables

| Variable | Default | Override with |
|---|---|---|
| `GCP_PROJECT` | `gcloud config get-value project` | `export GCP_PROJECT=...` |
| `SERVICE_NAME` | `rag-policy-agent` | `export SERVICE_NAME=...` |
| `REGION` | `asia-south1` | `export REGION=us-central1` |
| `GEMINI_API_KEY` | (required) | `export GEMINI_API_KEY=...` |

---

## Option B — CI/CD via Cloud Build

For automated deploys on every push to `main`, use `cloudbuild.yaml`.

### Step 1 — Store the Gemini key in Secret Manager

```bash
echo -n "your-gemini-key" | gcloud secrets create gemini-api-key \
  --data-file=- \
  --project=your-project-id
```

### Step 2 — Grant Cloud Build access to the secret

```bash
PROJECT_NUMBER=$(gcloud projects describe your-project-id \
  --format='value(projectNumber)')

gcloud secrets add-iam-policy-binding gemini-api-key \
  --member="serviceAccount:${PROJECT_NUMBER}@cloudbuild.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor" \
  --project=your-project-id
```

### Step 3 — Create a Cloud Build trigger

In the GCP Console → Cloud Build → Triggers → Create trigger:

- **Repository**: connect your GitHub/GitLab repo
- **Branch**: `^main$`
- **Build configuration**: Cloud Build configuration file
- **Location**: `/cloudbuild.yaml`
- **Substitutions**: set `_SERVICE_NAME` and `_REGION` if you want non-default values

### Step 4 — Push to main

```bash
git push origin main
```

Cloud Build runs three steps automatically:

| Step | What happens |
|---|---|
| `build` | `docker build` with layer cache from `latest` tag |
| `push` | Push both `$SHORT_SHA` and `latest` tags |
| `deploy` | `gcloud run deploy` with the SHA-tagged image |

The deploy uses `$SHORT_SHA` (not `latest`) so every running revision is traceable to a specific commit.

---

## Cloud Run Configuration

| Setting | Value | Rationale |
|---|---|---|
| Memory | 2 GiB | Two FAISS indexes + embedding model fit comfortably |
| CPU | 2 vCPU | Embedding encode is CPU-bound; 2 vCPU cuts it ~in half |
| Request timeout | 300 s | Large PDFs (100+ pages) can take 30–60 s to process |
| Concurrency | 4 | 4 simultaneous requests per instance; CPU-bound so beyond 4 there's no benefit |
| Min instances | 0 | Scale to zero when idle — saves cost |
| Max instances | 5 | Caps spend; LRU cache is per-instance |
| Workers | 1 | Cache lives in process memory; multiple workers can't share it |

### Choosing min-instances

| Setting | Cold start | Cost |
|---|---|---|
| `--min-instances=0` | 5–10 s on first request | Cheapest — pay only when in use |
| `--min-instances=1` | None | One idle instance always running |

For internal tools or demos, `0` is fine. For production with SLA requirements, set `1`.

---

## After Deploying

### Update the UI

Open the deployed URL and in the web UI sidebar, change the **API Base URL** to your Cloud Run service URL:

```
https://rag-policy-agent-xxxx-el.a.run.app
```

### Verify the deployment

```bash
SERVICE_URL="https://rag-policy-agent-xxxx-el.a.run.app"

curl ${SERVICE_URL}/health
# → {"status":"ok","version":"2.1.0","cached_document_sets":0}

curl -X POST ${SERVICE_URL}/query \
  -H "Content-Type: application/json" \
  -d '{"documents":["https://example.com/policy.pdf"],"questions":["test"]}'
```

### Check Cloud Run logs

```bash
gcloud logging read \
  "resource.type=cloud_run_revision AND resource.labels.service_name=rag-policy-agent" \
  --limit=50 \
  --format="value(textPayload)" \
  --project=your-project-id
```

---

## Updating the Deployment

### Via the deploy script (re-run it)

```bash
./deploy-cloudrun.sh
```

A new image is built and deployed. Cloud Run performs a zero-downtime rollout — traffic shifts to the new revision only after its health check passes.

### Via CI/CD

Push a commit to `main`. Cloud Build triggers automatically.

### Rolling back

```bash
# List recent revisions
gcloud run revisions list \
  --service=rag-policy-agent \
  --region=asia-south1

# Roll back to a specific revision
gcloud run services update-traffic rag-policy-agent \
  --to-revisions=rag-policy-agent-00005-abc=100 \
  --region=asia-south1
```

---

## Environment Variables on Cloud Run

| Variable | How to set |
|---|---|
| `GEMINI_API_KEY` (local script) | `--set-env-vars` in `deploy-cloudrun.sh` |
| `GEMINI_API_KEY` (CI/CD) | `--set-secrets` pulls from Secret Manager |
| `PORT` | Set automatically by Cloud Run — do not override |

> **Security note:** The CI/CD pipeline uses Secret Manager (`--set-secrets`) rather than `--set-env-vars`. This means the key is never stored in Cloud Run environment variable history, never printed in build logs, and access is controlled via IAM.
