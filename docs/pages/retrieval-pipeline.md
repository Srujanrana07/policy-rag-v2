# Retrieval Pipeline

A deep-dive into how the system finds the right chunks from a document before sending them to Gemini. The retrieval layer is the most engineered part of the codebase — it combines three strategies that compensate for each other's blind spots.

---

## Why Not Just FAISS?

Pure semantic search misses things that matter in insurance documents:

- **Exact terms**: A query for "AYUSH" might not semantically match "alternative medicine" if the document uses its full name inconsistently
- **Numbers**: `"5L sum insured"` embedding is close to `"10L sum insured"` because the sentences are structurally similar — but ₹25,000 and ₹50,000 are very different answers
- **Abbreviations**: `"PED"` and `"pre-existing disease"` sit far apart in embedding space despite being identical concepts

The hybrid approach combines semantic, lexical, and numeric signal to handle all three cases.

---

## Step 1 — Query Expansion

Before any search, `expand_query()` scans the query for known abbreviations and appends their full forms:

```python
QUERY_SYNONYMS = {
    "si":  ["sum insured"],
    "ped": ["pre existing disease", "pre-existing disease"],
    "icu": ["intensive care unit"],
    "ncb": ["no claim bonus"],
    "idv": ["insured declared value"],
    "gst": ["goods and services tax"],
    "emi": ["equated monthly installment"],
    "tp":  ["third party"],
    "od":  ["own damage"],
}
```

**Example:**

| Original query | Expanded query |
|---|---|
| `"is ped covered"` | `"is ped covered pre existing disease pre-existing disease"` |
| `"what is si for icu"` | `"what is si for icu sum insured intensive care unit"` |

The expanded string is used for all downstream retrieval — both FAISS embedding and BM25 tokenisation. This single step meaningfully improves recall for abbreviated queries without any model fine-tuning.

---

## Step 2 — Dual FAISS Search

The system maintains **two separate FAISS indexes**, built separately in `vectorizer.py`:

| Index | Content | Why separate? |
|---|---|---|
| `table_index` | Camelot/pdfplumber rows (pipe-delimited, tier-labelled) | Table rows are short and number-dense; mixing them with prose dilutes results |
| `text_index` | PyMuPDF prose paragraphs | Longer text for definitions, exclusions, waiting periods |

Both indexes use `IndexFlatIP` (flat inner-product) with L2-normalised vectors — exact cosine similarity, no approximation.

```python
def _search_index(query, index, chunks, model, k):
    vec = model.encode([query], convert_to_numpy=True)
    faiss.normalize_L2(vec)
    distances, indices = index.search(vec.astype("float32"), k)

    results = []
    for i, score in zip(indices[0], distances[0]):
        if score < 0.15:          # drop very weak matches
            continue
        chunk = chunks[i].strip()
        if chunk not in seen:
            results.append(chunk)
```

The `score < 0.15` threshold eliminates chunks that are semantically unrelated — they would waste context window space and could mislead the LLM.

FAISS is called **four times** in total: semantic search on both table and text indexes.

---

## Step 3 — BM25 Search

`_bm25_search()` runs BM25Okapi (from `rank-bm25`) on the same two chunk lists:

```python
tokenized_chunks = [tokenize(c) for c in chunks]
bm25 = BM25Okapi(tokenized_chunks)
scores = bm25.get_scores(tokenize(query))
```

`tokenize()` lowercases and extracts word tokens with `re.findall(r"\b\w+\b", text)`.

BM25 excels where FAISS struggles:

- **Exact coverage terms**: `"maternity"`, `"cataract"`, `"AYUSH"` — BM25 gives high scores when these exact words appear
- **Numeric matching**: `"25000"` in a query scores high against chunks containing `"25,000"` after tokenisation removes commas
- **Rare domain words**: Terms that appear few times in the corpus get high IDF weight

BM25 is also called **twice** — once on table chunks and once on text chunks.

---

## Step 4 — Merge and Deduplicate

All four result lists are concatenated and deduped using a `seen` set (preserving the order of first appearance):

```python
combined = table_semantic + text_semantic + table_bm25 + text_bm25

final = []
seen = set()
for chunk in combined:
    chunk = chunk.strip()
    if chunk not in seen:
        seen.add(chunk)
        final.append(chunk)
```

The ordering matters: table semantic results come first, so if the same chunk appears in both FAISS and BM25 results, the FAISS position is preserved (it tends to be more relevance-ordered).

---

## Step 5 — Custom Reranker

`_rerank_chunks()` scores each merged chunk with a three-signal composite:

```python
score = lexical_overlap      # len(query_terms & chunk_terms)
      + exact_boost          # +5 if full query string found in chunk
      + numeric_boost        # +2 per matching digit sequence
```

### Lexical overlap

Set intersection of word tokens between the query and the chunk. Captures general topical relevance beyond what the embedding already captured.

### Exact phrase boost

```python
if query.lower() in chunk.lower():
    exact_boost += 5
```

A +5 bonus when the entire query appears verbatim inside the chunk. This strongly promotes chunks that contain the exact phrasing the user typed — highly useful when the user quotes a clause or procedure name directly.

### Numeric boost

```python
query_nums  = re.findall(r"\d+", query)
chunk_nums  = re.findall(r"\d+", chunk)
numeric_boost += len(set(query_nums) & set(chunk_nums)) * 2
```

+2 per matching number. A query for `"5L sum insured"` extracts `["5"]`; the correct table row containing `"5,00,000"` or `"₹25,000 for 5L"` also contains `"5"` and gets the boost. This is the single most impactful signal for tier-correct answers in insurance documents.

---

## Full Retrieval Flow (with `k=10`)

```
expand_query("is ped covered for 5l?")
→ "is ped covered for 5l? pre existing disease pre-existing disease"

FAISS table_index  → up to 10 chunks (score ≥ 0.15)
FAISS text_index   → up to 10 chunks (score ≥ 0.15)
BM25  table_chunks → up to 10 chunks
BM25  text_chunks  → up to 10 chunks

merge + deduplicate → up to ~30 unique chunks

rerank by (overlap + exact + numeric)
→ return top 10

pass top 10 to llm_client.py
```

---

## Fallback: Widened Retrieval

If the LLM returns a low-confidence answer ("not specify" or empty string), `main.py` widens retrieval to `k=15` and deduplicates with `dict.fromkeys()` before a second LLM call:

```python
if "not specify" in ans.lower() or not ans.strip():
    expanded_chunks = list(dict.fromkeys(retrieve(q, indices, model, k=15)))
    ans = answer(q, expanded_chunks)
```

This catches edge cases where the relevant text sits just outside the top-10 window.

---

## Retrieval by the Numbers

| Component | Calls per query | Output |
|---|---|---|
| Query expansion | 1 | Expanded query string |
| FAISS search | 2 (table + text) | Up to 2k chunks |
| BM25 search | 2 (table + text) | Up to 2k chunks |
| Deduplication | 1 | Unique merged list |
| Reranker | 1 | Top-k sorted chunks |
| LLM call | 1 (+ 1 if fallback) | Answer string |
