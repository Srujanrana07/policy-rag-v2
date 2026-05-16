# Quickstart

Get from zero to a working answer in under 5 minutes.

---

## 1. Start the server

```bash
# Clone and set up (if you haven't already)
cp .env.example .env          # add your GEMINI_API_KEY
pip install -r requirements.txt

python run.py
# → INFO: Uvicorn running on http://0.0.0.0:8000
# → INFO: Loading embedding model: all-MiniLM-L6-v2
# → INFO: Ready.
```

The embedding model downloads automatically on the first run (~90 MB to `./model_cache/`). Subsequent starts load from disk in under a second.

---

## 2. Open the Web UI

Navigate to [http://localhost:8000](http://localhost:8000).

In the sidebar:
- Paste a PDF URL into the **Document URL** field
- Type a question
- Click **Ask** (sync) or **Stream** (token-by-token)

### Sample PDF for testing

```
https://raw.githubusercontent.com/SrujanRana/pdf/4f08d9fee570e7a0183ba9300d2b0a2b7cb605d8/BAJHLIP23020V012223.pdf
```

### Sample questions to try

| Question | What it tests |
|---|---|
| `Is cataract surgery covered for 5L sum insured?` | Table extraction + tier matching |
| `What is the PED waiting period?` | Query expansion (PED → pre-existing disease) |
| `Are ICU charges covered?` | Abbreviation expansion (ICU → intensive care unit) |
| `What is the room rent limit for 10L sum insured?` | Numeric boost in reranker |
| `Is AYUSH treatment included?` | Prose extraction + semantic retrieval |

---

## 3. Test via cURL

### Health check

```bash
curl http://localhost:8000/health
```

```json
{ "status": "ok", "version": "2.1.0", "cached_document_sets": 0 }
```

### Synchronous query

```bash
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{
    "documents": [
      "https://raw.githubusercontent.com/SrujanRana/pdf/4f08d9fee570e7a0183ba9300d2b0a2b7cb605d8/BAJHLIP23020V012223.pdf"
    ],
    "questions": [
      "Is cataract surgery covered for 5L sum insured?",
      "What is the PED waiting period?"
    ]
  }'
```

Expected response:

```json
{
  "answers": [
    "Cataract surgery is covered up to ₹25,000 for a 5L sum insured.",
    "Pre-existing diseases have a 48-month waiting period."
  ],
  "documents_with_content": 1,
  "processing_time": 6.42,
  "cache_hit": false
}
```

### Same query again — cache hit

Run the identical curl command a second time:

```json
{
  "answers": ["..."],
  "processing_time": 1.18,
  "cache_hit": true
}
```

Processing time drops by ~80% because the FAISS indexes are served from RAM — PDF download, extraction, chunking, and embedding are all skipped.

### Streaming query

```bash
curl -N "http://localhost:8000/stream-query?\
question=Is+cataract+surgery+covered+for+5L%3F&\
documents=https://raw.githubusercontent.com/SrujanRana/pdf/4f08d9fee570e7a0183ba9300d2b0a2b7cb605d8/BAJHLIP23020V012223.pdf"
```

You'll see events appear in real time:

```
data: {"message": "📄 Loading documents…"}
data: {"message": "🔢 Vector index ready."}
data: {"message": "🤖 Generating answer…"}
data: {"token": "Cataract"}
data: {"token": " surgery"}
data: {"token": " is covered"}
data: {"token": " up to ₹25,000."}
data: {"answer": "Cataract surgery is covered up to ₹25,000.", "cache_hit": false}
```

---

## 4. Test via Python

```python
import requests

BASE = "http://localhost:8000"
PDF  = "https://raw.githubusercontent.com/SrujanRana/pdf/4f08d9fee570e7a0183ba9300d2b0a2b7cb605d8/BAJHLIP23020V012223.pdf"

questions = [
    "Is cataract surgery covered for 5L sum insured?",
    "What is the PED waiting period?",
    "Are ICU charges covered and up to what limit?",
]

resp = requests.post(f"{BASE}/query", json={
    "documents": [PDF],
    "questions": questions,
})

data = resp.json()
print(f"Cache hit : {data['cache_hit']}")
print(f"Time      : {data['processing_time']}s\n")

for q, a in zip(questions, data["answers"]):
    print(f"Q: {q}")
    print(f"A: {a}\n")
```

---

## 5. Check the cache

After running any query, inspect what's cached:

```bash
curl http://localhost:8000/cache-status
```

```json
{
  "cached_document_sets": 1,
  "entries": [
    {
      "key": "a3f9b2c1…",
      "documents": ["https://…/BAJHLIP23020V012223.pdf"],
      "hits": 3,
      "age_seconds": 47
    }
  ]
}
```

---

## 6. Reset the cache

Force the next query to re-process from scratch:

```bash
curl -X POST http://localhost:8000/reset
```

```json
{ "status": "reset", "entries_cleared": 1 }
```

---

## What happens on first vs. subsequent queries

| Step | First query | Subsequent queries |
|---|---|---|
| PDF download | ✅ ~1–3s | ⏭ skipped |
| Table extraction (Camelot) | ✅ ~2–5s | ⏭ skipped |
| Text extraction (PyMuPDF) | ✅ ~0.5s | ⏭ skipped |
| Chunking | ✅ ~0.1s | ⏭ skipped |
| Embedding (ONNX) | ✅ ~1–3s | ⏭ skipped |
| FAISS index build | ✅ ~0.1s | ⏭ skipped |
| Retrieval + reranking | ✅ ~0.1s | ✅ ~0.1s |
| Gemini generation | ✅ ~2–4s | ✅ ~2–4s |

The cache eliminates everything except retrieval and generation on repeat queries.
