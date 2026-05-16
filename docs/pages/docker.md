# Docker

Everything you need to build, run, and understand the RAG Policy Agent Docker image.

---

## Quick Reference

```bash
# Build
docker build -t rag-policy-agent .

# Run locally
docker run -p 8000:8080 --env-file .env rag-policy-agent

# Health check
curl http://localhost:8000/health
```

---

## Why Docker?

Running the agent without Docker requires installing Ghostscript, OpenCV system libraries, and either PyTorch or ONNX Runtime manually — steps that differ across operating systems and frequently break. The Docker image encapsulates all system dependencies, the embedding model, and the Python packages into a single artifact that behaves identically on a developer laptop and on Cloud Run.

---

## Image Architecture

The Dockerfile uses a **multi-stage build**:

```
Stage 1: builder
├── Python 3.11 slim base
├── Install system packages (ghostscript, libgl1, poppler-utils)
├── Install Python dependencies (including sentence-transformers + optimum)
└── Download and export all-MiniLM-L6-v2 to ONNX format
         ↓  copy only runtime artifacts
Stage 2: runtime
├── Python 3.11 slim base (clean, no build tools)
├── Install system packages (runtime only)
├── Copy installed Python packages from builder
├── Copy ONNX model from builder
└── Copy application source
```

Build tools, compiler toolchains, and Hugging Face download caches are present only in the builder stage and are discarded before the final layer is committed. This keeps the runtime image as lean as possible.

---

## ONNX Instead of PyTorch

This is the single most impactful size optimisation. By default, `sentence-transformers` pulls in PyTorch as a dependency — approximately 2.5 GB for the CPU-only wheel alone.

The `optimum[onnxruntime]` package exports the embedding model to ONNX format during the build, then runs inference with ONNX Runtime. The result:

| Approach | PyTorch size | Inference speed |
|---|---|---|
| Standard sentence-transformers | ~2.5 GB | Baseline |
| With optimum + ONNX Runtime | ~250 MB | Equivalent on CPU |

The `all-MiniLM-L6-v2` model is only 22 MB — PyTorch was never needed at runtime. Removing it saves over 2 GB from the final image and also reduces cold-start memory pressure on Cloud Run.

---

## System Dependencies

These are installed in both stages because they are required at runtime:

| Package | Required by | Purpose |
|---|---|---|
| `ghostscript` | Camelot | PDF rendering for lattice table extraction |
| `libgl1` | OpenCV | OpenCV headless runtime (Camelot dependency) |
| `poppler-utils` | Camelot | PDF information utilities |

Without Ghostscript, Camelot's lattice mode fails silently and the system falls back to pdfplumber. Without `libgl1`, OpenCV raises an import error on import.

---

## Model Baked Into the Image

The embedding model is downloaded and converted to ONNX **during `docker build`**, not at container startup:

```dockerfile
# In the builder stage
RUN python -c "
from optimum.onnxruntime import ORTModelForFeatureExtraction
from transformers import AutoTokenizer

model = ORTModelForFeatureExtraction.from_pretrained(
    'sentence-transformers/all-MiniLM-L6-v2',
    export=True
)
model.save_pretrained('/app/model_cache/all-MiniLM-L6-v2')

tokenizer = AutoTokenizer.from_pretrained('sentence-transformers/all-MiniLM-L6-v2')
tokenizer.save_pretrained('/app/model_cache/all-MiniLM-L6-v2')
"
```

This means:
- **No network call at container startup** — the model is already on disk inside the image
- **Cloud Run cold starts** are purely container boot time (~3–5 s), not model download time
- **Reproducible builds** — the model version is locked to whatever was current when the image was built

---

## Port Convention

The container listens on port `8080` (Cloud Run's expected default). When running locally, map it to any free port:

```bash
docker run -p 8000:8080 --env-file .env rag-policy-agent
# App is at http://localhost:8000
# Container listens on :8080
```

Cloud Run injects a `PORT` environment variable and the app reads it via Uvicorn. You never need to set `PORT` manually.

---

## Environment Variables

Pass secrets via `--env-file` locally. Never bake secrets into the image:

```bash
# .env file
GEMINI_API_KEY=your_key_here
```

```bash
docker run -p 8000:8080 --env-file .env rag-policy-agent
```

In production (Cloud Run), the Gemini API key is stored in Secret Manager and injected at deploy time via `--set-secrets`, keeping it out of the image, out of environment variable listings in the Cloud Console, and out of your CI logs.

---

## Build Time vs. Runtime

| Activity | When | Duration |
|---|---|---|
| System package install | Build | ~60 s |
| Python package install | Build | ~120 s |
| ONNX model export | Build | ~90 s |
| Container start | Runtime | ~3–5 s |
| Embedding model load | Runtime (once) | ~0.5 s (from disk) |
| First query (cold cache) | Runtime | ~5–10 s |
| Repeat query (warm cache) | Runtime | ~1–2 s |

---

## Running With Docker Compose (optional)

For local development with automatic restart:

```yaml
# docker-compose.yml
services:
  rag-agent:
    build: .
    ports:
      - "8000:8080"
    env_file:
      - .env
    volumes:
      - ./app:/app/app      # live-reload app source
    restart: unless-stopped
```

```bash
docker compose up --build
```

---

## Common Docker Issues

### Build fails: `ghostscript not found`

```
ERROR: Could not find a version that satisfies the requirement ghostscript
```

This is the Python `ghostscript` binding — it requires the system `ghostscript` binary to be present first. In the Dockerfile, the `apt-get install ghostscript` step must come **before** `pip install -r requirements.txt`. If you're building on an unusual base image, verify:

```bash
docker run --rm your-image gs --version
```

### Container exits immediately

Check that `GEMINI_API_KEY` is set. The app does not exit on a missing key (it only skips `genai.configure()`), but all LLM calls will return error messages. Run with logs visible:

```bash
docker run -p 8000:8080 --env-file .env rag-policy-agent 2>&1 | head -30
```

### Port conflict

```
Error: bind: address already in use
```

Change the host-side port:

```bash
docker run -p 8001:8080 --env-file .env rag-policy-agent
```

### Image too large

If you installed the full `torch` wheel accidentally (e.g. by running `pip install sentence-transformers` without pinning `optimum[onnxruntime]` first), the image will be ~4 GB larger. Verify:

```bash
docker images rag-policy-agent
# should be roughly 3-4 GB, not 6-7 GB
```

To fix, ensure `requirements.txt` does **not** include `torch` directly, and that `optimum[onnxruntime]` is listed. Rebuild from scratch:

```bash
docker build --no-cache -t rag-policy-agent .
```
