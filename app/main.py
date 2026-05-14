"""
RAG Policy Agent — FastAPI application.

Endpoints:
  GET  /health            → liveness probe (Cloud Run compatible)
  GET  /                  → serve index.html
  POST /query             → sync multi-question Q&A
  GET  /stream-query      → SSE streaming single-question Q&A
  POST /reset             → clear server-side state (no-op; index is per-request)
"""

import json
import logging
import os
from pyexpat import model
import time
from contextlib import asynccontextmanager
from typing import List

# from matplotlib.pylab import indices

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
    version="2.0.0",
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
    return {"status": "ok", "version": "2.0.0"}


@app.get("/", include_in_schema=False)
def serve_ui():
    index = os.path.join(STATIC_DIR, "index.html")
    if os.path.isfile(index):
        return FileResponse(index)
    return {"message": "RAG Policy Agent is running. See /docs for API reference."}


@app.post("/query")
def sync_query(req: QueryRequest):
    """
    Synchronous Q&A.  Accepts multiple questions and returns all answers.
    """
    t0 = time.perf_counter()
    if not req.documents:
        raise HTTPException(400, "Provide at least one document URL or path.")
    if not req.questions:
        raise HTTPException(400, "Provide at least one question.")

    logger.info("sync_query: %d docs, %d questions", len(req.documents), len(req.questions))

    table_text, full_text = load_documents(req.documents)
    if not table_text and not full_text:
        raise HTTPException(400, "Could not extract text from any supplied document.")

    indices = build_index(table_text, full_text)

    model = indices["model"]
    answers = []

    for q in req.questions:
        top_chunks = retrieve(q, indices, model, k=10)
        ans = answer(q, top_chunks)
        # Fallback: if answer is vague, retry with full context
        if "not specify" in ans.lower() or not ans.strip():
            logger.warning("Low-confidence retrieval for query: %s", q)

            # Retry with more retrieved chunks instead of full document
            expanded_chunks = retrieve(
                q,
                indices,
                model,
                k=15
            )

            # Remove duplicates
            expanded_chunks = list(dict.fromkeys(expanded_chunks))

            ans = answer(q, expanded_chunks)
        answers.append(ans)

    elapsed = time.perf_counter() - t0
    logger.info("sync_query done in %.2fs", elapsed)
    return {
        "answers": answers,
        "documents_with_content": len(req.documents),
        "processing_time": elapsed,
    }


@app.get("/stream-query")
def stream_query(
    question: str = Query(..., description="The question to answer"),
    documents: str = Query(..., description="Comma-separated document URLs"),
):
    """
    SSE streaming Q&A for a single question.
    Emits JSON lines: {message}, {token}, {answer}, or {error}.
    """
    doc_list = [d.strip() for d in documents.split(",") if d.strip()]
    if not doc_list:
        raise HTTPException(400, "No documents provided.")

    def event_stream():
        def send(obj: dict) -> str:
            return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"

        try:
            yield send({"message": "📄 Loading documents…"})
            table_text, full_text = load_documents(doc_list)
            if not table_text and not full_text:
                yield send({"error": "Could not extract text from documents."})
                return

            yield send({"message": "🔢 Building vector index…"})
            indices = build_index(table_text, full_text)

            model = indices["model"]

            yield send({"message": "🤖 Generating answer…"})
            top_chunks = retrieve(
                question,
                indices,
                model,
                k=10
            )

            full_answer = ""
            for token in answer_stream(question, top_chunks):
                full_answer += token
                yield send({"token": token})

            # Fallback
            if "not specify" in full_answer.lower() or not full_answer.strip():
                logger.warning("Retrying retrieval with expanded context")

                expanded_chunks = retrieve(
                    question,
                    indices,
                    model,
                    k=15
                )
                expanded_chunks = list(dict.fromkeys(expanded_chunks))

                full_answer = ""

                for token in answer_stream(question, expanded_chunks):
                    full_answer += token
                    yield send({"token": token})
            yield send({"answer": full_answer})

        except Exception as exc:
            logger.error("stream_query error: %s", exc, exc_info=True)
            yield send({"error": str(exc)})

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.post("/reset")
def reset():
    """No-op kept for UI compatibility."""
    return {"status": "reset"}
