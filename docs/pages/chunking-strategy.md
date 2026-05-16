# Chunking Strategy

How raw PDF text is split into chunks before embedding. Chunking quality directly determines retrieval quality — bad chunks mean the right answer is never surfaced regardless of how good the retriever is.

---

## Why Custom Chunking?

Standard fixed-size chunking (split every N characters) breaks insurance documents badly:

- **Table rows get split mid-row** — a sublimit entry like `"Cataract | ₹25,000 | ₹50,000 | ₹75,000"` becomes two fragments, neither of which contains the full coverage picture
- **Headings get merged into prose** — `"EXCLUSIONS"` followed by a list gets chunked together with whatever came before, contaminating retrieval
- **Short clauses get dropped** — a 40-character exclusion clause below the minimum size threshold would be silently discarded with naive chunking

The custom chunker in `vectorizer.py` treats each structural element differently.

---

## Input Normalisation

Before chunking, the text is normalised:

```python
text = re.sub(r"\r", "\n", text)          # normalise Windows line endings
text = re.sub(r"\n{3,}", "\n\n", text)    # collapse 3+ blank lines to 2
```

This ensures the semantic block splitter (which splits on `\n\s*\n`) sees consistent paragraph boundaries regardless of the PDF extractor used.

---

## Semantic Block Split

The normalised text is split into blocks on blank lines:

```python
blocks = re.split(r"\n\s*\n", text)
```

Each block is then classified into one of five categories and handled accordingly.

---

## Block Classification

### 1. Large Table (split into sub-chunks)

**Condition:** block contains `|` AND `len(block) > 500`

Insurance sublimit tables can span hundreds of lines. A naive approach would embed the entire table as one chunk, making it impossible to retrieve individual rows. The large-table handler splits row-by-row:

```python
if "|" in block and len(block) > size:
    rows = block.splitlines()
    table_chunk = []
    current_size = 0
    for row in rows:
        if current_size + len(row) > size:
            chunks.append("\n".join(table_chunk))   # flush current batch
            table_chunk = [row]
            current_size = len(row)
        else:
            table_chunk.append(row)
            current_size += len(row)
    if table_chunk:
        chunks.append("\n".join(table_chunk))       # flush remainder
```

Result: each chunk contains a few table rows — small enough to embed meaningfully, large enough to preserve column context.

### 2. Table (standalone chunk)

**Condition:** block contains `|` OR matches `\b\d+\s+\d+\s+\d+\b` (three adjacent numbers — common in premium tables)

The entire block becomes a single chunk without being accumulated with surrounding prose. This keeps table rows isolated so that retrieval of `"₹25,000 cataract 5L"` doesn't require the embedding to also represent the exclusion paragraph that happened to follow it in the PDF.

### 3. Bullet List (standalone chunk)

**Condition:** any line starts with `•`, `-`, `*`, or a digit followed by a space

```python
is_bullets = bool(re.search(r"^[•\-\*\d]+\s", block, re.MULTILINE))
```

Bullet lists often enumerate exclusions, sub-conditions, or waiting period triggers. Keeping them together preserves the list semantics — embedding a fragment like `"- maternity complications"` in isolation loses the context that this was an exclusion list.

### 4. Heading (standalone chunk)

**Condition:** less than 12 words AND less than 120 characters AND (all-caps OR title-case OR ends with `:`)

```python
is_heading = (
    len(block.split()) < 12
    and len(block) < 120
    and (block.isupper() or block.istitle() or block.endswith(":"))
)
```

Headings like `"SECTION 3 — EXCLUSIONS"` or `"Pre-Existing Diseases:"` are flushed as their own chunk. This achieves two things:
- The heading itself can be retrieved independently (useful if the user asks what sections exist)
- The heading doesn't bleed into the following prose chunk, keeping that chunk's embedding focused on its actual content

### 5. Paragraph (accumulated)

Everything else is accumulated into a running `current` string until the size limit is exceeded:

```python
if len(current) + len(block) < size:      # fits: keep accumulating
    current += "\n\n" + block
else:
    chunks.append(current.strip())         # flush
    # semantic overlap: carry last 20 words into next chunk
    tail = " ".join(current.split()[-20:])
    current = tail + "\n\n" + block
```

---

## Overlap Strategy

When a paragraph block overflows the size limit, the last 20 words of the current chunk are carried forward into the next one:

```python
words = current.split()
tail  = " ".join(words[-20:])
current = tail + "\n\n" + block
```

**Why 20 words?** Insurance clauses typically run 15–30 words. A 20-word tail ensures that a clause split across a chunk boundary is fully present in at least one chunk.

**Why not a fixed character overlap?** Character overlap can split mid-word or mid-number. Word-based overlap guarantees the carried context is semantically complete.

---

## Cleanup Pass

After all blocks are processed, a final cleanup pass removes:

- Chunks shorter than 30 characters (headers-only fragments, page numbers, artefacts)
- Exact duplicate chunks (same text appearing twice — common in PDFs with repeated footers)

```python
for c in chunks:
    c = c.strip()
    if len(c) < 30:
        continue
    if c in seen:
        continue
    seen.add(c)
    cleaned.append(c)
```

---

## Chunk Size Parameters

| Parameter | Default | Effect |
|---|---|---|
| `size` | 500 chars | Target maximum size per paragraph chunk |
| `overlap` | 100 chars | Not used directly — overlap is 20-word tail |

500 characters (~80–100 words) fits comfortably within the `all-MiniLM-L6-v2` input limit of 256 tokens while being large enough to contain a full insurance clause.

---

## Example: How a Sublimit Table Is Chunked

Raw extracted text from Camelot (already tier-labelled):

```
3L/4L/5L | Cataract | ₹25,000 | ₹1,00,000 | ₹2,00,000
3L/4L/5L | Knee Replacement | ₹50,000 | ₹1,00,000 | ₹2,00,000
10L/15L/20L | Cataract | ₹50,000 | ₹1,75,000 | ₹3,50,000
10L/15L/20L | Knee Replacement | ₹75,000 | ₹2,50,000 | ₹5,00,000
...
```

This block contains `|` and likely exceeds 500 characters → classified as **large table** → split 2 rows at a time into sub-chunks:

```
Chunk A: "3L/4L/5L | Cataract | ₹25,000 | ...\n3L/4L/5L | Knee Replacement | ₹50,000 | ..."
Chunk B: "10L/15L/20L | Cataract | ₹50,000 | ...\n10L/15L/20L | Knee Replacement | ₹75,000 | ..."
```

Each chunk is independently embedded and indexed. A query for `"cataract 5L"` retrieves Chunk A (high tier label + procedure name overlap); a query for `"cataract 10L"` retrieves Chunk B.

---

## Dual Indexing

`build_index()` calls `chunk_text()` twice — once for table text and once for prose:

```python
table_index, table_chunks = _build_single_index(table_text, "table", model)
text_index,  text_chunks  = _build_single_index(full_text,  "text",  model)
```

The resulting dict `{"table": (...), "text": (...), "model": model}` is stored in the LRU cache keyed by the document set MD5.
