# RAG Policy Agent v2

Retrieval-Augmented Generation over insurance policy PDFs.
**FastAPI + FAISS + Sentence-Transformers + Google Gemini — production-ready, Cloud Run native.**

```
PDF URL(s) ──► extract text/tables ──► chunk ──► embed ──► FAISS index
Question   ──► embed ──► top-k retrieval ──► Gemini (streaming) ──► answer
```

---

## Project layout

```
rag-policy-agent/
├── app/
│   ├── __init__.py
│   ├── main.py            # FastAPI app + all routes
│   ├── document_loader.py # PDF download, camelot tables, PyMuPDF fallback
│   ├── vectorizer.py      # Chunking + embeddings + FAISS index builder
│   ├── retriever.py       # Semantic top-k search
│   └── llm_client.py      # Gemini wrapper (sync + SSE streaming)
├── static/
│   └── index.html         # Single-file UI (no build step needed)
├── Dockerfile             # Multi-stage, single image, model baked in
├── cloudbuild.yaml        # CI/CD for Cloud Build triggers
├── deploy-cloudrun.sh     # One-shot manual deploy script
├── run.py                 # Local dev runner (uvicorn --reload)
├── requirements.txt
├── .env.example
└── .gitignore
```

---

## Local development

```bash
# 1. Clone and set up env
cp .env.example .env
# → Edit .env and set GEMINI_API_KEY=your_key

# 2. Install dependencies (virtualenv recommended)
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# 3. Run
python run.py
# → http://localhost:8000
```

---

## Docker (local)

```bash
docker build -t rag-policy-agent .
docker run -p 8000:8080 --env-file .env rag-policy-agent
# → http://localhost:8000
```

The image is self-contained: the `all-MiniLM-L6-v2` embedding model is downloaded
during `docker build` and baked in — no network call at startup.

---

## Deploy to Google Cloud Run

### One-shot (manual)

```bash
export GCP_PROJECT=your-project-id
export GEMINI_API_KEY=your-key
chmod +x deploy-cloudrun.sh
./deploy-cloudrun.sh
```

### CI/CD via Cloud Build trigger

1. Store your Gemini key in Secret Manager:
   ```bash
   echo -n "your-key" | gcloud secrets create gemini-api-key --data-file=-
   ```

2. Create a Cloud Build trigger pointed at your repo with `cloudbuild.yaml`.

3. Push to main — the trigger builds, pushes, and deploys automatically.

### After deploy

Open the deployed URL, then in the **UI sidebar** change *API Base URL* to your
Cloud Run service URL (e.g. `https://rag-policy-agent-xxxx-el.a.run.app`).

---

## API reference

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Liveness probe — returns `{"status":"ok"}` |
| GET | `/` | Web UI |
| POST | `/query` | Synchronous multi-question Q&A |
| GET | `/stream-query` | SSE streaming single-question Q&A |
| POST | `/reset` | No-op (kept for UI compat) |

### POST /query

```json
{
  "documents": ["https://example.com/policy.pdf"],
  "questions": [
    "Is cataract surgery covered for 5L sum insured?",
    "What is the waiting period for pre-existing diseases?"
  ]
}
```

Response:
```json
{
  "answers": ["✅ Yes, cataract surgery is covered, up to ₹25,000.", "..."],
  "documents_with_content": 2,
  "processing_time": 4.21
}
```

### GET /stream-query (SSE)

```
GET /stream-query?question=Is+cataract+covered&documents=https://...
Accept: text/event-stream
```

Event sequence:
```
data: {"message": "📄 Loading documents…"}
data: {"message": "🔢 Building vector index…"}
data: {"message": "🤖 Generating answer…"}
data: {"token": "✅"}
data: {"token": " Yes,"}
...
data: {"answer": "✅ Yes, cataract surgery is covered, up to ₹25,000."}
```

---

## Key design decisions

**Why FastAPI over Flask?** Native async, SSE streaming without hacks, automatic
OpenAPI docs at `/docs`, and Pydantic validation with zero boilerplate.

**Why a single Docker image?** Multi-stage build keeps build tools out of the
runtime layer. The model is baked in so Cloud Run cold starts don't hit the
network. One image = one artifact to version, scan, and promote.

**Why `workers 1`?** The FAISS index is built in memory per request. Multiple
workers don't share memory so there's no benefit — and Cloud Run scales
horizontally across container instances instead.

**Why `min-instances 0`?** Cost — scale to zero when idle. If you need
sub-second P50 latency, set `--min-instances=1` in the deploy script.
