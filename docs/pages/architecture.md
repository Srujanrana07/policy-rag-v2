# Architecture

A complete walk-through of every layer in the system — from PDF URL to streamed answer — based on the actual source code.

---

## Full Pipeline

```
User question + PDF URL(s)
         │
         ▼
┌─────────────────────────────────────────────────┐
│  FastAPI  (main.py)                             │
│  POST /query  or  GET /stream-query             │
│  LRU cache check (MD5 key of sorted URLs)       │
└──────────┬──────────────────────────────────────┘
           │ cache miss only
           ▼
┌─────────────────────────────────────────────────┐
│  document_loader.py                             │
│  download_pdf()  →  tempfile.NamedTemporaryFile │
│  extract_tables()  Camelot lattice + tier label │
│     └─ fallback: pdfplumber                     │
│  extract_text()  PyMuPDF page.get_text()        │
│  finally: os.remove(temp_file)                  │
└──────────┬──────────────────────────────────────┘
           │ (table_text, full_text)
           ▼
┌─────────────────────────────────────────────────┐
│  vectorizer.py                                  │
│  chunk_text()  semantic paragraph grouping      │
│     table rows preserved, large tables split    │
│     headings isolated, bullet lists grouped     │
│     20-word tail overlap between paragraphs     │
│  model.encode()  all-MiniLM-L6-v2 (ONNX)       │
│  faiss.IndexFlatIP  ×2  (table + text)          │
│  Stored in DOCUMENT_CACHE[md5_key]              │
└──────────┬──────────────────────────────────────┘
           │ indices dict {"table":…, "text":…, "model":…}
           ▼
┌─────────────────────────────────────────────────┐
│  retriever.py                                   │
│  expand_query()  abbreviation synonyms          │
│  _search_index()  FAISS cosine ×2  score≥0.15  │
│  _bm25_search()  BM25Okapi ×2                   │
│  merge + deduplicate                            │
│  _rerank_chunks()  overlap + exact + numeric    │
└──────────┬──────────────────────────────────────┘
           │ top-k chunks (default k=10)
           ▼
┌─────────────────────────────────────────────────┐
│  llm_client.py                                  │
│  _build_prompt()  context limit 12,000 chars    │
│  Gemini 2.5 Flash  generate_content()           │
│  Exponential backoff  3 attempts  2→4→8s        │
│  Streaming: yield chunk.text tokens via SSE     │
└──────────┬──────────────────────────────────────┘
           │ answer (streamed or sync)
           ▼
      User sees answer
```

---

## document_loader.py

### PDF download

`download_pdf()` uses `tempfile.NamedTemporaryFile(delete=False)` — the file is written, closed, then passed to extractors. A `finally` block in `load_document()` calls `os.remove()` unconditionally, so temp files are never left on disk regardless of extraction errors.

```python
temp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
# ... write chunks ...
return temp.name   # caller is responsible for cleanup
```

### Table extraction hierarchy

```
camelot.read_pdf(flavor="lattice", line_scale=40)
    └── success → tier-label each row → return formatted text
    └── exception → pdfplumber.open()
                        └── success → pipe-delimited rows
                        └── exception → return None
```

Camelot's `lattice` mode reconstructs table structure from the PDF's ruling lines — far more accurate for insurance sublimit tables than stream-mode. `line_scale=40` increases sensitivity for thin grid lines.

### Tier detection

Each Camelot row is scanned for known rupee amounts and pre-labelled:

```python
if any(a in [25000, 100000, 200000] for a in amounts):
    return "3L/4L/5L"
if any(a in [50000, 175000, 350000] for a in amounts):
    return "10L/15L/20L"
if any(a in [75000, 250000, 500000] for a in amounts):
    return ">20L"
```

This tier label is prepended to the row before indexing, so FAISS and BM25 both surface tier-correct figures without relying on the LLM to match amounts to coverage bands.

---

## vectorizer.py

### Dual FAISS indexes

The system builds **two separate FAISS indexes**:

| Index | Source | Purpose |
|---|---|---|
| `table` | Camelot/pdfplumber output | Coverage amounts, sublimits, tiers |
| `text` | PyMuPDF prose | Definitions, exclusions, waiting periods |

This prevents dense prose from diluting short, number-heavy table rows during retrieval.

### Semantic chunking

`chunk_text(size=500, overlap=100)` processes text in this order:

1. Normalise line endings, collapse 3+ blank lines to 2
2. Split on blank lines into semantic blocks
3. For each block, classify it:
   - **Large table** (`|` present, len > 500) → split row-by-row into sub-chunks
   - **Table** (`|` present or 3 adjacent numbers) → standalone chunk
   - **Bullets** (lines starting with `•`, `-`, `*`, digits) → standalone chunk
   - **Heading** (< 12 words, < 120 chars, all-caps / title-case / ends with `:`) → standalone chunk
   - **Paragraph** → accumulate into `current` until size exceeded
4. On paragraph overflow, retain a 20-word tail from the previous chunk as overlap context
5. Drop chunks shorter than 30 characters and exact duplicates

### FAISS index type

`IndexFlatIP` (flat inner-product) with L2-normalised vectors gives exact cosine similarity. No approximation — appropriate for index sizes of 500–5000 vectors. Built fresh per document set, then stored in the LRU cache.

---

## retriever.py

### Query expansion

Before any search, abbreviations in the query are expanded:

| Abbreviation | Expands to |
|---|---|
| `si` | sum insured |
| `ped` | pre existing disease, pre-existing disease |
| `icu` | intensive care unit |
| `ncb` | no claim bonus |
| `idv` | insured declared value |
| `tp` | third party |
| `od` | own damage |

The original query and all expansions are concatenated, so `"is ped covered"` becomes `"is ped covered pre existing disease pre-existing disease"` — improving both FAISS and BM25 recall.

### Hybrid retrieval flow

```
expanded_query
    ├── FAISS search on table_index  (k chunks, score ≥ 0.15)
    ├── FAISS search on text_index   (k chunks, score ≥ 0.15)
    ├── BM25 search on table_chunks  (k chunks)
    └── BM25 search on text_chunks   (k chunks)
                    │
              merge all 4 lists
              deduplicate (seen set)
                    │
             _rerank_chunks()
```

### Reranker scoring

Each merged chunk receives a composite score:

```
score = lexical_overlap           # query terms ∩ chunk terms
      + exact_boost (5 pts)       # full query string found in chunk
      + numeric_boost (×2 pts)    # matching digit sequences
```

Numeric overlap gets a 2× multiplier because insurance queries almost always contain a specific sum insured (e.g. `5L`, `25000`) and the correct answer must contain that number.

### Score filter

FAISS results with cosine similarity below `0.15` are dropped before merging. This eliminates truly unrelated chunks that would otherwise waste context window space and confuse the LLM.

---

## llm_client.py

### Context limiting

`_build_prompt()` iterates through retrieved chunks and stops when cumulative character count exceeds `MAX_CONTEXT_CHARS = 12000`:

```python
for chunk in context_chunks:
    if total_chars + len(chunk) > 12000:
        break
    selected_chunks.append(chunk)
    total_chars += len(chunk)
```

This keeps the prompt within Gemini's practical input window while still fitting the most relevant chunks.

### System prompt design

The prompt instructs Gemini to:
- Answer **only** from provided context — no knowledge beyond the document
- Always mention: treatment/procedure, coverage amount, waiting period, applicable tier
- Report partial information if full details are absent
- Only say "does not specify" if **absolutely nothing** relevant exists

This structured output makes answers consistent and machine-parseable.

### Retry logic

Both `answer()` and `answer_stream()` retry up to `MAX_RETRIES = 3` times with exponential backoff:

```
attempt 1: immediate
attempt 2: sleep 2s
attempt 3: sleep 4s
final failure: return ❌ error message
```

---

## main.py — LRU Document Cache

### Cache key

```python
def _cache_key(documents: List[str]) -> str:
    joined = "|".join(sorted(documents))   # order-independent
    return hashlib.md5(joined.encode()).hexdigest()
```

Sorting before hashing means `[a.pdf, b.pdf]` and `[b.pdf, a.pdf]` produce the same key.

### Cache lifecycle

```
Request arrives
    │
    ├── key in DOCUMENT_CACHE?
    │       YES → increment hits, update last_used, return cached indices
    │       NO  → run full pipeline
    │               │
    │               ├── len(cache) ≥ 10?
    │               │       YES → evict min(last_used)
    │               └── store new entry with created_at, last_used, hits=0
    └── return (indices, cache_hit)
```

Cache capacity: 10 document sets. Eviction strategy: least-recently-used (`last_used` timestamp). Cache does not survive container restarts — intentional for stateless Cloud Run deployments.

### Low-confidence fallback

In both `/query` and `/stream-query`, if the answer contains `"not specify"` or is blank:

```python
expanded_chunks = list(dict.fromkeys(retrieve(q, indices, model, k=15)))
ans = answer(q, expanded_chunks)
```

`dict.fromkeys()` deduplicates while preserving order, then the LLM is called again with 5 additional chunks.

---

## Deployment — Cloud Run

### Resource settings (both scripts)

| Setting | Value | Rationale |
|---|---|---|
| Memory | 2 GiB | Two FAISS indexes + embedding model fit comfortably |
| CPU | 2 vCPU | Parallelise embedding encode batch |
| Timeout | 300 s | Large PDFs (100+ pages) can take time |
| Concurrency | 4 | CPU-bound; 4 requests share 2 vCPU |
| Min instances | 0 | Scale to zero when idle |
| Max instances | 5 | Cap spend; LRU cache effective per-instance |
| Workers | 1 | Cache lives in process memory; multiple workers can't share it |

### Why workers=1?

The `DOCUMENT_CACHE` dict lives in the FastAPI process. Multiple Uvicorn workers would each maintain their own independent cache — a cache miss in worker 2 would rebuild indexes that worker 1 already has. Cloud Run scales *horizontally* (more container instances) instead, which is cheaper and still stateless.
