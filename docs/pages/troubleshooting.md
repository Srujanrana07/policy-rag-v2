# Troubleshooting

Real failure modes drawn directly from the codebase, with exact error messages and fixes.

---

## PDF & Extraction Errors

### Camelot fails silently — no table data returned

**Symptom:** Queries about coverage amounts return vague prose answers instead of specific figures. Logs show:

```
WARNING  app.document_loader: Camelot failed: ...
```

**Cause:** Camelot's lattice mode requires Ghostscript. If it is missing or misconfigured, Camelot raises an exception and the code falls back to pdfplumber.

**Fix:**

```bash
# Ubuntu / Debian
sudo apt-get install ghostscript libgl1 poppler-utils

# macOS
brew install ghostscript poppler

# Verify
gs --version    # should print e.g. 10.02.1
```

Then check if pdfplumber also failed (look for the second warning):

```
WARNING  app.document_loader: pdfplumber fallback failed: ...
```

If both fail, the document has no extractable table structure — try a different PDF.

---

### `pdf not in content-type` — URL rejected

**Symptom:**

```
WARNING  app.document_loader: URL does not appear to be PDF: https://...
```

**Cause:** `download_pdf()` checks both the `Content-Type` header and the URL extension. Some servers return `application/octet-stream` for PDFs.

**Fix:** Ensure the URL ends in `.pdf` OR that the server returns `Content-Type: application/pdf`. For GitHub raw URLs, use the `raw.githubusercontent.com` form — it always returns the correct content type.

---

### `Local file not found`

**Symptom:**

```
ERROR    app.document_loader: Local file not found: /path/to/policy.pdf
```

**Cause:** A local file path was passed to `/query` but the file does not exist at that path inside the running process (or container).

**Fix:** When running in Docker, the file must be inside the container. Mount it as a volume:

```bash
docker run -p 8000:8080 \
  -v /your/local/pdfs:/pdfs \
  --env-file .env \
  rag-policy-agent
```

Then pass `/pdfs/policy.pdf` as the document path.

---

### Temp file not deleted after extraction

**Symptom:** Disk space growing on the host or inside the container.

**Cause:** The `finally` block in `load_document()` deletes the temp file, but if the process is killed with `SIGKILL` (not `SIGTERM`), the `finally` block does not run.

**Fix:** Cloud Run sends `SIGTERM` before `SIGKILL` (with a 10 s grace period by default), so this is rarely an issue in production. For long-running local sessions, check `tempfile.gettempdir()` and clear old `.pdf` files manually.

---

## Embedding & FAISS Errors

### `Invalid embedding shape`

**Symptom:**

```
ValueError: Invalid embedding shape
```

**Cause:** `model.encode()` returned a 1D array instead of 2D. This can happen if an empty string or a list containing only whitespace is passed to the encoder.

**Fix:** The chunker already filters chunks shorter than 30 characters, and `_build_single_index()` checks `if not text.strip(): return None, []`. If you see this error, the input text is likely completely empty (extraction produced nothing). Check that the PDF is not password-protected or image-only.

---

### FAISS search returns 0 results

**Symptom:** Answers are always vague or the fallback fires on every query. Logs show:

```
INFO  app.retriever: Retrieved 0 hybrid chunks
```

**Cause:** The FAISS score threshold `score < 0.15` is filtering out all results. This happens when the query is entirely unrelated to the document, or when the document's language is very different from the query language.

**Fix options:**
- Lower the threshold in `retriever.py` from `0.15` to `0.10`
- Ensure the query language matches the document language
- Verify the document extracted text at all (`/cache-status` → check `age_seconds` is recent)

---

### `Could not extract text from any supplied document`

**Symptom:**

```json
{ "detail": "Could not extract text from any supplied document." }
```

**Cause:** Both `table_text` and `full_text` are `None` or empty for every document supplied. Possible reasons:

- PDF is image-only (scanned) — PyMuPDF returns no text from image PDFs
- PDF is password-protected — `fitz.open()` raises or returns empty pages
- Download failed (non-PDF URL, server error, timeout)

**Fix:**
- For scanned PDFs: add an OCR step (see the Roadmap page)
- For password-protected PDFs: decrypt with `pypdf` before passing to the pipeline
- For download failures: check the URL is publicly accessible and returns `Content-Type: application/pdf`

---

## LLM & Gemini Errors

### `❌ LLM temporarily unavailable. Please retry in a moment.`

**Symptom:** This string appears in the answer after all retries are exhausted.

**Cause:** Gemini API returned an error on all 3 attempts. Common causes:
- Invalid or expired `GEMINI_API_KEY`
- Rate limit exceeded (free tier: 15 RPM, 1M TPM)
- Gemini API outage

**Fix:**

```bash
# Test the key directly
curl "https://generativelanguage.googleapis.com/v1beta/models?key=YOUR_KEY"
# Should return a list of models, not a 400/403 error
```

If rate-limited, the exponential backoff (2s → 4s → 8s) usually resolves transient limits. For sustained high traffic, upgrade to a paid Gemini tier.

---

### Answer always says "The document does not specify"

**Symptom:** Every answer is vague, even for clearly answerable questions.

**Cause — checklist:**

1. **Extraction failed** — table text is None, prose text is thin
   - Check logs for Camelot/pdfplumber warnings
   - Try `curl -X POST .../query -d '{"documents":[...], "questions":["list all sections"]}'` — if this also returns vague answers, extraction is the problem

2. **Wrong PDF** — the document doesn't actually contain the answer
   - Verify manually by opening the PDF and searching

3. **Score threshold too high** — all chunks scored below 0.15
   - Temporarily lower to 0.10 in `retriever.py`

4. **Context limit exceeded** — `MAX_CONTEXT_CHARS = 12000` truncated the relevant chunk
   - Increase to 16000 in `llm_client.py` if Gemini's input window allows

5. **Query expansion not covering the term** — try spelling out the full term manually (`"pre-existing disease"` instead of `"PED"`)

---

## Server & Startup Errors

### `ModuleNotFoundError: No module named 'camelot'`

Camelot is imported lazily inside `extract_tables()` — this error only appears during extraction, not at startup. It means `camelot-py` was not installed.

```bash
pip install camelot-py opencv-python-headless ghostscript
```

---

### `Address already in use` on port 8000

```bash
# Find what's using port 8000
lsof -i :8000

# Kill it
kill -9 <PID>

# Or use a different port
python run.py   # edit run.py to use port 8001
```

---

### LRU cache growing unbounded in Docker

**Symptom:** Container memory usage climbs over time.

**Cause:** Each cached document set holds two FAISS indexes in RAM. With `MAX_CACHE_ENTRIES = 10` and large documents (1000+ chunks per index), this can reach 1–2 GB.

**Fix:** Lower `MAX_CACHE_ENTRIES` in `main.py`:

```python
MAX_CACHE_ENTRIES = 5   # reduce from 10
```

Or call `/reset` periodically to clear stale entries. The `/cache-status` endpoint shows `age_seconds` for each entry — useful for identifying old cached sets that are no longer needed.

---

## Docker-Specific Errors

### `libGL.so.1: cannot open shared object file`

**Cause:** `opencv-python-headless` requires `libgl1` at runtime, which is not installed in the slim base image by default.

**Fix:** Ensure the Dockerfile includes:

```dockerfile
RUN apt-get update && apt-get install -y libgl1
```

---

### Docker build hangs at model download step

The ONNX model export step in the builder stage downloads from Hugging Face. If the build machine has no internet access or Hugging Face is slow:

```bash
# Build with increased timeout
docker build --network=host -t rag-policy-agent .

# Or pre-download and COPY manually
python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"
# Then COPY ./model_cache into the Dockerfile
```
