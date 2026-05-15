# RAG Policy Agent

**Retrieval-Augmented Generation over insurance policy PDFs.**
FastAPI · FAISS · Sentence-Transformers (ONNX) · Google Gemini · Cloud Run

```
PDF URL(s) ──► extract text / tables ──► semantic chunk ──► embed (ONNX)
             ──► FAISS index (in-memory, LRU-cached)
Question   ──► embed ──► hybrid retrieval (semantic + BM25 + rerank)
             ──► Gemini 2.5 Flash (streaming SSE) ──► answer
```

---

## Features

| Capability | Detail |
|---|---|
| **PDF ingestion** | PyMuPDF for text; camelot (lattice) for tables; pdfplumber as fallback |
| **Smart chunking** | Paragraph grouping, table preservation, heading detection, clause-safe overlap |
| **Hybrid retrieval** | Semantic (FAISS cosine) + lexical (BM25) + custom reranker |
| **Query expansion** | Abbreviation synonym map (PED, ICU, SI, NCB, …) |
| **Streaming answers** | Server-Sent Events (SSE) token-by-token via `/stream-query` |
| **In-memory LRU cache** | Same document set reuses FAISS index — no redundant processing |
| **Low-confidence fallback** | Widens retrieval from k=10 → k=15 on weak answers |
| **Production-ready image** | ~4–5 GB Docker image (ONNX inference, no PyTorch) |
| **Cloud Run native** | Scale-to-zero, Secret Manager integration, Cloud Build CI/CD |

---

## Project layout

```
rag-policy-agent/
├── app/
│   ├── __init__.py
│   ├── main.py             # FastAPI routes, LRU document cache, lifespan hooks
│   ├── document_loader.py  # PDF download, pdfplumber tables, PyMuPDF text
│   ├── vectorizer.py       # Semantic chunking + ONNX embeddings + FAISS builder
│   ├── retriever.py        # Hybrid search (semantic + BM25) + reranker
│   └── llm_client.py       # Gemini 2.5 Flash wrapper — sync & SSE streaming
├── static/
│   └── index.html          # Single-file UI (no build step)
├── Dockerfile              # Multi-stage, ONNX-only, model baked in (~3–4 GB)
├── cloudbuild.yaml         # Cloud Build CI/CD trigger config
├── deploy-cloudrun.sh      # One-shot manual deploy script
├── run.py                  # Local dev runner (uvicorn --reload)
├── requirements.txt
├── .env.example
├── .dockerignore
└── .gitignore
```

---

## Local development

### Prerequisites

- Python 3.11+
- A Google Gemini API key ([get one here](https://aistudio.google.com/app/apikey))

### Setup

```bash
# 1. Copy and fill in environment variables
cp .env.example .env
# Edit .env → set GEMINI_API_KEY=your_key

# 2. Create a virtual environment
python -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Run the dev server
python run.py
# → http://localhost:8000
```

The embedding model (`all-MiniLM-L6-v2`) downloads automatically on first run and is cached in `./model_cache/`.

---

## Docker

### Build

```bash
docker build -t rag-policy-agent .
```

The build bakes the embedding model into the image (ONNX format) so Cloud Run cold starts never hit the network for model weights.

> **Expected final image size: 4–5 GB**
> Key optimisations vs. a naive install:
> - Sentence-Transformers runs on **ONNX Runtime** — PyTorch (~2.5 GB) is never installed
> - `torch` (~2.5 GB) is excluded — ONNX Runtime handles inference instead
> - `ghostscript`, `poppler-utils`, `libgl1` are still present (required by camelot)
> - Multi-stage build discards all compiler tooling from the runtime layer

### Run locally

```bash
docker run -p 8000:8080 --env-file .env rag-policy-agent
# → http://localhost:8000
```

---

## Deploy to Google Cloud Run

### Option A — one-shot manual deploy

```bash
export GCP_PROJECT=your-project-id
export GEMINI_API_KEY=your_key

chmod +x deploy-cloudrun.sh
./deploy-cloudrun.sh
```

The script enables the necessary GCP APIs, submits a Cloud Build job (no local Docker needed), then deploys the resulting image to Cloud Run.

### Option B — automated CI/CD via Cloud Build trigger

1. **Store the Gemini key in Secret Manager:**

   ```bash
   echo -n "your-key" | gcloud secrets create gemini-api-key \
     --data-file=- --project=your-project-id
   ```

2. **Grant the Cloud Build service account access:**

   ```bash
   PROJECT_NUMBER=$(gcloud projects describe your-project-id \
     --format='value(projectNumber)')

   gcloud secrets add-iam-policy-binding gemini-api-key \
     --member="serviceAccount:${PROJECT_NUMBER}@cloudbuild.gserviceaccount.com" \
     --role="roles/secretmanager.secretAccessor" \
     --project=your-project-id
   ```

3. **Create a Cloud Build trigger** pointing at your repository with `cloudbuild.yaml` as the config file. Set substitution variables `_SERVICE_NAME` and `_REGION` in the trigger UI.

4. **Push to main** — Cloud Build builds, pushes, and deploys automatically.

### After deploying

Open the deployed URL and update the **API Base URL** in the UI sidebar to your Cloud Run service URL (e.g. `https://rag-policy-agent-xxxx-el.a.run.app`).

---

## API reference

### `GET /health`

Liveness probe — Cloud Run health check compatible.

```json
{ "status": "ok", "version": "2.1.0", "cached_document_sets": 2 }
```

---

### `POST /query` — synchronous multi-question Q&A

```json
{
  "documents": [
    "https://example.com/policy.pdf"
  ],
  "questions": [
    "Is cataract surgery covered for a 5L sum insured?",
    "What is the waiting period for pre-existing diseases?"
  ]
}
```

**Response:**

```json
{
  "answers": [
    "✅ Cataract surgery is covered up to ₹25,000 for a 5L sum insured.",
    "Pre-existing diseases have a 48-month waiting period."
  ],
  "documents_with_content": 1,
  "processing_time": 4.21,
  "cache_hit": false
}
```

Repeated calls with the same document URLs return `"cache_hit": true` and skip all PDF download, extraction, embedding, and FAISS build steps.

---

### `GET /stream-query` — SSE streaming single-question Q&A

```
GET /stream-query?question=Is+cataract+covered&documents=https://example.com/policy.pdf
Accept: text/event-stream
```

**Event sequence:**

```
data: {"message": "⚡ Using cached document index…"}
data: {"message": "🤖 Generating answer…"}
data: {"token": "✅"}
data: {"token": " Cataract surgery is covered"}
...
data: {"answer": "✅ Cataract surgery is covered up to ₹25,000.", "cache_hit": true}
```

Each `data:` line is a JSON object. Possible keys:

| Key | Description |
|---|---|
| `message` | Progress status update |
| `token` | Streamed answer fragment |
| `answer` | Final complete answer (last event) |
| `error` | Error message if something failed |
| `cache_hit` | Boolean — present on the final `answer` event |

---

### `GET /cache-status`

Inspect the in-memory document cache.

```json
{
  "cached_document_sets": 1,
  "entries": [
    {
      "key": "a3f9b2c1…",
      "documents": ["https://example.com/policy.pdf"],
      "hits": 5,
      "age_seconds": 142
    }
  ]
}
```

---

### `POST /reset`

Clear all cached document indices, forcing full re-processing on the next query.

```json
{ "status": "reset", "entries_cleared": 2 }
```

---

## Architecture notes

### Why ONNX instead of PyTorch?

PyTorch adds ~2.5 GB to the Docker image for CPU-only sentence embedding — none of which is used at runtime since `all-MiniLM-L6-v2` is a tiny 22 MB model. ONNX Runtime provides equivalent inference speed on CPU with a fraction of the footprint. The `optimum` library handles the export automatically during the image build.

### Why camelot over pdfplumber?

Camelot's lattice mode reconstructs table structure from PDF ruling lines, which makes it significantly more accurate on insurance sublimit tables (the kind with bordered cells, multi-row headers, and tier columns). `pdfplumber` is kept as an automatic fallback for PDFs where camelot fails (e.g. stream-mode tables with no visible borders).

### Why `workers 1` on Cloud Run?

The FAISS index lives in process memory. Multiple workers would each build their own copy (wasted memory, duplicated work) without sharing the LRU cache. Cloud Run scales horizontally across container instances instead, which achieves the same concurrency goal without in-process overhead.

### Why `min-instances 0`?

Cost efficiency — the service scales to zero when idle. The embedding model is baked into the image so cold start latency (typically 5–10 s) is only the container boot time, not a model download. Set `--min-instances=1` in the deploy script if sub-second P50 latency is required.

### Document cache behaviour

The in-memory LRU cache keys on an MD5 of the sorted document URL list (order-independent). Capacity is 10 document sets; the least-recently-used entry is evicted when the limit is reached. The cache does not survive a container restart — this is intentional for stateless Cloud Run deployments.

---

## Environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `GEMINI_API_KEY` | ✅ | — | Google Gemini API key |
| `PORT` | No | `8080` | Port the server listens on |

---

## Sample policy URL for testing

```
https://raw.githubusercontent.com/SrujanRana/pdf/4f08d9fee570e7a0183ba9300d2b0a2b7cb605d8/BAJHLIP23020V012223.pdf
```

---

## License

MIT