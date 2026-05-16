# Introduction

**RAG Policy Agent** is a production-ready Retrieval-Augmented Generation system for querying insurance policy PDFs using natural language.

Point it at any policy PDF — paste a URL, ask a question in plain English — and get a grounded answer sourced directly from the document, streamed token-by-token.

> **RAG = Retrieve then Generate.** The system finds the most relevant sections of the document first, then passes only those to the LLM — answers are always traceable back to the source text, never invented.

---

## What It Does

Given a PDF like a health insurance policy and a question like:

> *"Is cataract surgery covered for a 5 lakh sum insured?"*

The agent runs this pipeline:

1. **Downloads** the PDF to a temporary file (deleted after extraction)
2. **Extracts** structured tables with Camelot (lattice mode) + falls back to pdfplumber, then extracts full prose with PyMuPDF
3. **Chunks** the text using semantic paragraph grouping, table preservation, and heading detection
4. **Embeds** chunks into two separate FAISS indexes — one for table text, one for prose
5. **Retrieves** top-k chunks using hybrid search: FAISS cosine + BM25 + custom reranker
6. **Generates** a grounded answer with Gemini 2.5 Flash, streamed via SSE
7. **Caches** the FAISS indexes in memory — repeat queries on the same document skip all the above

The answer looks like:

```
✅ Cataract surgery is covered up to ₹25,000 for a 5L sum insured.
```

---

## Why Hybrid Retrieval?

A single retrieval method misses things the other catches:

| Method | Strength | Weakness |
|---|---|---|
| Semantic (FAISS) | Finds conceptually related text | Can miss exact terms, numbers |
| Lexical (BM25) | Exact keyword and number matches | No semantic understanding |
| Reranker | Combines both with numeric boost | Adds slight latency |

The system runs all three and merges results, deduplicating before passing to the LLM.

---

## Stack

| Layer | Technology |
|---|---|
| API framework | FastAPI 0.115 (async, SSE native) |
| PDF text | PyMuPDF 1.24 |
| PDF tables | Camelot-py 1.0 (lattice) → pdfplumber fallback |
| Embedding model | `all-MiniLM-L6-v2` via sentence-transformers |
| Vector store | FAISS `IndexFlatIP` (dual index: tables + text) |
| Lexical search | rank-bm25 |
| LLM | Google Gemini 2.5 Flash |
| Deployment | Google Cloud Run (containerised, serverless) |
| UI | Single-file HTML, no framework |

---

## Key Features

- **Dual FAISS indexes** — table text and prose are indexed separately so table rows are never diluted by surrounding prose
- **Tier-aware extraction** — insurance sublimit rows are automatically labelled `3L/4L/5L`, `10L/15L/20L`, `>20L` during Camelot extraction
- **Query expansion** — abbreviations like `PED`, `ICU`, `SI`, `NCB` are automatically expanded to their full forms before retrieval
- **In-memory LRU cache** — up to 10 document sets cached; repeat queries on the same PDF are near-instant
- **Streaming answers** — SSE token-by-token, with progress messages (`📄 Loading…`, `🔢 Index ready`, `🤖 Generating…`)
- **Low-confidence fallback** — if the first answer contains "not specify", retrieval widens from k=10 to k=15 and retries
- **Temp-file cleanup** — downloaded PDFs are deleted in a `finally` block regardless of errors
- **Exponential backoff** — Gemini calls retry up to 3 times with 2s → 4s → 8s delays

---

## Project Layout

```
rag-policy-agent/
├── app/
│   ├── main.py             # FastAPI app, LRU cache, all routes
│   ├── document_loader.py  # PDF download, Camelot + pdfplumber + PyMuPDF
│   ├── vectorizer.py       # Semantic chunking + FAISS dual-index builder
│   ├── retriever.py        # Hybrid search: FAISS + BM25 + reranker + query expansion
│   └── llm_client.py       # Gemini 2.5 Flash wrapper, sync + SSE, retries
├── static/
│   └── index.html          # Single-file UI
├── Dockerfile
├── cloudbuild.yaml          # Cloud Build CI/CD
├── deploy-cloudrun.sh       # One-shot deploy script
├── requirements.txt
└── .env.example
```
