# Installation

Three ways to run the agent: local Python, Docker, or Google Cloud Run.

---

## Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Python | 3.11+ | 3.12 recommended |
| Ghostscript | Any recent | Required by Camelot for PDF rendering |
| Docker | 24+ | For containerised setup only |
| Google Cloud SDK | Latest | For Cloud Run deploy only |
| Gemini API Key | — | [Get one free at AI Studio](https://aistudio.google.com/) |

### Install Ghostscript

Camelot's lattice mode calls Ghostscript as a subprocess. Without it, table extraction silently falls back to pdfplumber.

```bash
# macOS
brew install ghostscript

# Ubuntu / Debian
sudo apt-get install ghostscript libgl1

# Windows
# Download installer from https://www.ghostscript.com/releases/
```

---

## Option 1 — Local Python

### 1. Clone and configure

```bash
git clone https://github.com/your-org/rag-policy-agent.git
cd rag-policy-agent
cp .env.example .env
```

Edit `.env`:

```env
GEMINI_API_KEY=your_gemini_api_key_here
```

### 2. Create a virtual environment

```bash
python -m venv venv
source venv/bin/activate        # macOS / Linux
# venv\Scripts\activate         # Windows PowerShell
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

Key packages installed:

| Package | Purpose |
|---|---|
| `fastapi`, `uvicorn[standard]` | Web framework + ASGI server |
| `PyMuPDF` | Full-text PDF extraction |
| `camelot-py`, `opencv-python-headless` | Table extraction (lattice mode) |
| `ghostscript` | Python binding for Ghostscript |
| `sentence-transformers` | Embedding model loader |
| `optimum[onnxruntime]` | ONNX inference backend (replaces PyTorch) |
| `faiss-cpu` | Vector similarity search |
| `rank-bm25` | Lexical BM25 retrieval |
| `google-generativeai` | Gemini API client |

> **Why no `torch`?** `sentence-transformers` normally pulls in PyTorch (~2.5 GB). The `optimum[onnxruntime]` package provides CPU inference at identical speed with a fraction of the footprint. The Dockerfile excludes PyTorch entirely.

### 4. Run

```bash
python run.py
# → Uvicorn on http://0.0.0.0:8000 with --reload
```

Open [http://localhost:8000](http://localhost:8000). The first run downloads `all-MiniLM-L6-v2` (~90 MB) to `./model_cache/`.

---

## Option 2 — Docker

The image bakes the embedding model in at build time so container startup never hits the network for model weights.

```bash
# Build
docker build -t rag-policy-agent .

# Run
docker run -p 8000:8080 --env-file .env rag-policy-agent
```

The container listens on port `8080` internally (Cloud Run convention). The `-p 8000:8080` maps it to your local `8000`.

---

## Option 3 — Google Cloud Run

### One-shot deploy script

```bash
export GCP_PROJECT=your-project-id
export GEMINI_API_KEY=your-key

chmod +x deploy-cloudrun.sh
./deploy-cloudrun.sh
```

The script:
1. Enables `run.googleapis.com`, `cloudbuild.googleapis.com`, `containerregistry.googleapis.com`
2. Submits a Cloud Build job (builds and pushes the image — no local Docker needed)
3. Deploys to Cloud Run in `asia-south1` (Mumbai) with 2 GiB RAM, 2 vCPU, max 5 instances
4. Prints the service URL

### After deploy

Open the deployed URL and set the **API Base URL** in the UI sidebar to your Cloud Run service URL.

---

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `GEMINI_API_KEY` | ✅ | — | Google AI Studio key |
| `PORT` | No | `8080` | Port the server binds to (Cloud Run sets this automatically) |

---

## Verify

```bash
curl http://localhost:8000/health
```

```json
{ "status": "ok", "version": "2.1.0", "cached_document_sets": 0 }
```

The `cached_document_sets` field shows how many document index sets are currently held in the in-memory LRU cache.

---

## Troubleshooting

### `camelot` import error at startup

Camelot imports lazily (inside `extract_tables()`) so the app still starts. If table extraction then fails, it automatically falls back to pdfplumber. To fix Camelot:

```bash
# Ubuntu
sudo apt-get install ghostscript libgl1 poppler-utils

# macOS
brew install ghostscript poppler
```

### `GEMINI_API_KEY not configured`

The app calls `load_dotenv()` at import time in `llm_client.py`. Make sure `.env` exists in the project root and `python-dotenv` is installed.

### Model download hangs

The `all-MiniLM-L6-v2` model downloads from Hugging Face on first run. If you're behind a proxy, set `HF_HUB_DISABLE_PROGRESS_BARS=1` and ensure `~/.cache/huggingface` is writable.

### Port 8000 already in use

Change the port in `run.py`:

```python
uvicorn.run("app.main:app", host="0.0.0.0", port=8001, reload=True)
```
