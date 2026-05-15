# =============================================================================
# RAG Policy Agent — optimised multi-stage image (~4–5 GB)
#
# Size levers applied:
#   1. sentence-transformers installed WITHOUT torch (uses onnxruntime instead)
#      → saves ~2.5 GB vs a default install
#   2. System libs installed ONLY in the runtime stage (not duplicated)
#   3. Build toolchain (gcc, g++) discarded after pip install
#   4. pip cache never written (--no-cache-dir)
#   5. Embedding model baked in at build time — no cold-start download
#
# camelot (lattice mode) is the primary table extractor.
# It requires ghostscript + poppler-utils + opencv at runtime — all present.
# pdfplumber is kept in requirements.txt as the fallback.
# =============================================================================

# ── Stage 1: Python dependency builder ────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

# Compiler tools needed to build some wheels (opencv, faiss, etc.)
# These are NOT copied to the runtime stage.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        gcc \
        g++ \
        libglib2.0-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# Install into /install so we copy only the Python packages, not build tools
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir --prefix=/install -r requirements.txt


# ── Stage 2: Lean runtime image ───────────────────────────────────────────────
FROM python:3.11-slim AS runtime

# Runtime system libraries only:
#   ghostscript + poppler-utils  → camelot lattice mode
#   libgl1 + libglib2.0-0        → opencv-python-headless
#   libgomp1                     → faiss-cpu (OpenMP)
RUN apt-get update && apt-get install -y --no-install-recommends \
        ghostscript \
        poppler-utils \
        libgl1 \
        libglib2.0-0 \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Pull only the installed Python packages from the builder
COPY --from=builder /install /usr/local

WORKDIR /app

# Application source
COPY app/    ./app/
COPY static/ ./static/

# Writable runtime directories
RUN mkdir -p model_cache logs \
 && chmod 777 model_cache logs

# ── Bake embedding model ──────────────────────────────────────────────────────
# Loaded with ONNX backend — torch is never installed or needed.
# This eliminates the network call on Cloud Run cold starts.
RUN python -c "\
from sentence_transformers import SentenceTransformer; \
model = SentenceTransformer( \
    'all-MiniLM-L6-v2', \
    cache_folder='/app/model_cache', \
    backend='onnx' \
); \
_ = model.encode(['warmup']); \
print('Model baked OK')"

ENV PORT=8080 \
    PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 8080

CMD ["sh", "-c", \
     "uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 1 --log-level info"]