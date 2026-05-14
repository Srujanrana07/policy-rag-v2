
"""
Vectorizer: chunk → embed → FAISS index.
Production-safe version:
- No persistent disk cache
- RAM/session-cache compatible
- Semantic chunking for insurance PDFs
"""

import logging
import re
from typing import List

import faiss
from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)

# ── Embedding Model ─────────────────────────────────────────────

_MODEL: SentenceTransformer | None = None

MODEL_NAME = "all-MiniLM-L6-v2"


def get_model() -> SentenceTransformer:

    global _MODEL

    if _MODEL is None:

        logger.info(
            "Loading embedding model: %s",
            MODEL_NAME
        )

        _MODEL = SentenceTransformer(
            MODEL_NAME,
            device="cpu",
            cache_folder="./model_cache"
        )

        logger.info("Embedding model loaded.")

    return _MODEL


# ── Smart Chunking ──────────────────────────────────────────────

def chunk_text(
    text: str,
    size: int = 500,
    overlap: int = 100
) -> List[str]:

    """
    Smart chunking for insurance PDFs.

    Features:
    - Semantic paragraph grouping
    - Table preservation
    - Large-table splitting
    - Heading preservation
    - Bullet grouping
    - Clause-safe overlap
    """

    if not text.strip():
        return []

    # ── Normalize text ─────────────────────────
    text = re.sub(r"\r", "\n", text)

    text = re.sub(
        r"\n{3,}",
        "\n\n",
        text
    )

    # ── Semantic block split ───────────────────
    blocks = re.split(
        r"\n\s*\n",
        text
    )

    chunks = []

    current = ""

    for block in blocks:

        block = block.strip()

        if not block:
            continue

        # ── Large table splitting ───────────────
        if "|" in block and len(block) > size:

            rows = block.splitlines()

            table_chunk = []

            current_size = 0

            for row in rows:

                row_len = len(row)

                if current_size + row_len > size:

                    if table_chunk:

                        chunks.append(
                            "\n".join(table_chunk)
                        )

                    table_chunk = [row]

                    current_size = row_len

                else:

                    table_chunk.append(row)

                    current_size += row_len

            if table_chunk:

                chunks.append(
                    "\n".join(table_chunk)
                )

            continue

        # ── Table detection ─────────────────────
        is_table = (
            "|" in block
            or bool(
                re.search(
                    r"\b\d+\s+\d+\s+\d+\b",
                    block
                )
            )
        )

        # ── Bullet detection ────────────────────
        is_bullets = bool(
            re.search(
                r"^[•\-\*\d]+\s",
                block,
                re.MULTILINE
            )
        )

        # ── Heading detection ───────────────────
        is_heading = (
            len(block.split()) < 12
            and len(block) < 120
            and (
                block.isupper()
                or block.istitle()
                or block.endswith(":")
            )
        )

        # ── Standalone semantic chunks ──────────
        if is_table or is_bullets or is_heading:

            if current.strip():

                chunks.append(
                    current.strip()
                )

            chunks.append(block)

            current = ""

            continue

        # ── Paragraph accumulation ──────────────
        if len(current) + len(block) < size:

            current += "\n\n" + block

        else:

            if current.strip():

                chunks.append(
                    current.strip()
                )

            # ── Semantic overlap retention ──────
            words = current.split()

            tail_words = words[-20:]

            tail = " ".join(tail_words)

            current = tail + "\n\n" + block

    # ── Final chunk ────────────────────────────
    if current.strip():

        chunks.append(
            current.strip()
        )

    # ── Cleanup ────────────────────────────────
    cleaned = []

    seen = set()

    for c in chunks:

        c = c.strip()

        if len(c.strip()) < 30:
            continue

        if c in seen:
            continue

        seen.add(c)

        cleaned.append(c)

    logger.info(
        "Produced %d semantic chunks",
        len(cleaned)
    )

    return cleaned


# ── Internal Index Builder ──────────────────────────────────────

def _build_single_index(
    text: str,
    cache_prefix: str,
    model: SentenceTransformer
):

    if not text.strip():
        return None, []

    logger.info(
        "Building %s index",
        cache_prefix
    )

    chunks = chunk_text(text)

    if not chunks:
        return None, []

    embeddings = model.encode(
        chunks,
        batch_size=32,
        show_progress_bar=False,
        convert_to_numpy=True
    )

    faiss.normalize_L2(embeddings)

    if len(embeddings.shape) != 2:

        raise ValueError(
            "Invalid embedding shape"
        )

    dim = embeddings.shape[1]

    index = faiss.IndexFlatIP(dim)

    index.add(
        embeddings.astype("float32")
    )

    logger.info(
        "Built %s index with %d vectors",
        cache_prefix,
        index.ntotal
    )

    return index, chunks


# ── Public API ──────────────────────────────────────────────────

def build_index(
    table_text: str,
    full_text: str
):

    model = get_model()

    table_index, table_chunks = _build_single_index(
        table_text or "",
        "table",
        model
    )

    text_index, text_chunks = _build_single_index(
        full_text or "",
        "text",
        model
    )

    return {
        "table": (
            table_index,
            table_chunks
        ),
        "text": (
            text_index,
            text_chunks
        ),
        "model": model
    }