"""
RAG Policy Agent — FastAPI application.

Endpoints:
  GET  /health            → liveness probe (Cloud Run compatible)
  GET  /                  → serve index.html
  POST /query             → sync multi-question Q&A
  GET  /stream-query      → SSE streaming single-question Q&A
  POST /reset             → clear in-memory document cache
"""

import hashlib
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Dict, List, Tuple, Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.document_loader import load_documents
from app.llm_client import answer, answer_stream
from app.retriever import retrieve
from app.vectorizer import build_index, get_model

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


# ── In-memory document cache ──────────────────────────────────────────────────
# Key   : MD5 of sorted, joined document URLs
# Value : {"indices": ..., "created_at": float, "hits": int}
DOCUMENT_CACHE: Dict[str, Any] = {}

MAX_CACHE_ENTRIES = 10  # evict LRU beyond this limit


def _cache_key(documents: List[str]) -> str:
    """Stable cache key regardless of URL order."""
    joined = "|".join(sorted(documents))
    return hashlib.md5(joined.encode()).hexdigest()


def _get_or_build_indices(documents: List[str]) -> Tuple[Any, bool]:
    """
    Return (indices, cache_hit).
    Builds and caches indices on the first call for a document set;
    returns the cached copy on every subsequent call.
    """
    key = _cache_key(documents)

    if key in DOCUMENT_CACHE:
        entry = DOCUMENT_CACHE[key]
        entry["hits"] += 1
        entry["last_used"] = time.time()
        logger.info(
            "Cache HIT for key=%s (hits=%d)", key[:8], entry["hits"]
        )
        return entry["indices"], True

    # ── Cache miss: full pipeline ─────────────────────────────────────────
    logger.info("Cache MISS for key=%s — running full pipeline", key[:8])

    table_text, full_text = load_documents(documents)
    if not table_text and not full_text:
        raise ValueError("Could not extract text from any supplied document.")

    indices = build_index(table_text, full_text)

    # ── Evict oldest entry if cache is full ───────────────────────────────
    if len(DOCUMENT_CACHE) >= MAX_CACHE_ENTRIES:
        oldest_key = min(
            DOCUMENT_CACHE, key=lambda k: DOCUMENT_CACHE[k]["last_used"]
        )
        del DOCUMENT_CACHE[oldest_key]
        logger.info("Evicted oldest cache entry key=%s", oldest_key[:8])

    DOCUMENT_CACHE[key] = {
        "indices": indices,
        "created_at": time.time(),
        "last_used": time.time(),
        "hits": 0,
        "documents": documents,
    }
    logger.info("Cached indices for key=%s", key[:8])
    return indices, False


# ── Lifespan: warm up embedding model at startup ──────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Warming up embedding model…")
    get_model()
    logger.info("Ready.")
    yield


app = FastAPI(
    title="RAG Policy Agent",
    description="Retrieval-Augmented Generation over insurance policy PDFs.",
    version="2.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve static UI
STATIC_DIR = os.path.join(os.path.dirname(__file__), "..", "static")
STATIC_DIR = os.path.abspath(STATIC_DIR)
if os.path.isdir(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# ── Schemas ───────────────────────────────────────────────────────────────────
class QueryRequest(BaseModel):
    documents: List[str]
    questions: List[str]


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {
        "status": "ok",
        "version": "2.1.0",
        "cached_document_sets": len(DOCUMENT_CACHE),
    }


@app.get("/", include_in_schema=False)
def serve_ui():
    index = os.path.join(STATIC_DIR, "index.html")
    if os.path.isfile(index):
        return FileResponse(index)
    return {"message": "RAG Policy Agent is running. See /docs for API reference."}


@app.post("/query")
def sync_query(req: QueryRequest):
    """
    Synchronous Q&A. Accepts multiple questions and returns all answers.
    Document indices are cached in memory — repeated calls with the same
    documents skip PDF download, extraction, embedding, and FAISS rebuild.
    """
    t0 = time.perf_counter()

    if not req.documents:
        raise HTTPException(400, "Provide at least one document URL or path.")
    if not req.questions:
        raise HTTPException(400, "Provide at least one question.")

    logger.info(
        "sync_query: %d docs, %d questions", len(req.documents), len(req.questions)
    )

    try:
        indices, cache_hit = _get_or_build_indices(req.documents)
    except ValueError as exc:
        raise HTTPException(400, str(exc))

    model = indices["model"]
    answers = []

    for q in req.questions:
        top_chunks = retrieve(q, indices, model, k=10)
        ans = answer(q, top_chunks)

        # Fallback: low-confidence answer → retry with wider retrieval
        if "not specify" in ans.lower() or not ans.strip():
            logger.warning("Low-confidence retrieval for query: %s", q)
            expanded_chunks = list(
                dict.fromkeys(retrieve(q, indices, model, k=15))
            )
            ans = answer(q, expanded_chunks)

        answers.append(ans)

    elapsed = time.perf_counter() - t0
    logger.info("sync_query done in %.2fs (cache_hit=%s)", elapsed, cache_hit)

    return {
        "answers": answers,
        "documents_with_content": len(req.documents),
        "processing_time": round(elapsed, 3),
        "cache_hit": cache_hit,
    }


@app.get("/stream-query")
def stream_query(
    question: str = Query(..., description="The question to answer"),
    documents: str = Query(..., description="Comma-separated document URLs"),
):
    """
    SSE streaming Q&A for a single question.
    Emits JSON lines: {message}, {token}, {answer}, or {error}.
    Subsequent calls with the same documents reuse cached indices.
    """
    doc_list = [d.strip() for d in documents.split(",") if d.strip()]
    if not doc_list:
        raise HTTPException(400, "No documents provided.")

    def event_stream():
        def send(obj: dict) -> str:
            return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"

        try:
            # ── Index retrieval (cached or fresh) ─────────────────────────
            key = _cache_key(doc_list)
            cache_hit = key in DOCUMENT_CACHE

            if cache_hit:
                yield send({"message": "⚡ Using cached document index…"})
            else:
                yield send({"message": "📄 Loading documents…"})

            try:
                indices, cache_hit = _get_or_build_indices(doc_list)
            except ValueError as exc:
                yield send({"error": str(exc)})
                return

            if not cache_hit:
                # Only show this step message on a cache miss (we just built it)
                yield send({"message": "🔢 Vector index ready."})

            model = indices["model"]

            yield send({"message": "🤖 Generating answer…"})
            top_chunks = retrieve(question, indices, model, k=10)

            full_answer = ""
            for token in answer_stream(question, top_chunks):
                full_answer += token
                yield send({"token": token})

            # Fallback: low-confidence → retry with wider retrieval
            if "not specify" in full_answer.lower() or not full_answer.strip():
                logger.warning("Retrying retrieval with expanded context")
                expanded_chunks = list(
                    dict.fromkeys(retrieve(question, indices, model, k=15))
                )
                full_answer = ""
                for token in answer_stream(question, expanded_chunks):
                    full_answer += token
                    yield send({"token": token})

            yield send({"answer": full_answer, "cache_hit": cache_hit})

        except Exception as exc:
            logger.error("stream_query error: %s", exc, exc_info=True)
            yield send({"error": str(exc)})

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.post("/reset")
def reset():
    """Clear the in-memory document cache, forcing re-processing on next query."""
    count = len(DOCUMENT_CACHE)
    DOCUMENT_CACHE.clear()
    logger.info("Document cache cleared (%d entries removed)", count)
    return {"status": "reset", "entries_cleared": count}


@app.get("/cache-status")
def cache_status():
    """Inspect what's currently in the document cache."""
    return {
        "cached_document_sets": len(DOCUMENT_CACHE),
        "entries": [
            {
                "key": k[:8] + "…",
                "documents": v["documents"],
                "hits": v["hits"],
                "age_seconds": round(time.time() - v["created_at"]),
            }
            for k, v in DOCUMENT_CACHE.items()
        ],
    }