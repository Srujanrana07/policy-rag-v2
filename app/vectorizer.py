
"""
Vectorizer: chunk → embed → FAISS index.
Model is loaded once at startup and reused.
"""

import logging
import re
from typing import List
import hashlib
import pickle
import os

import faiss
from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)

# ── Cache Settings ──────────────────────────────────────────────
MAX_CACHE_FILES = 20

CACHE_DIR = "./index_cache"

os.makedirs(CACHE_DIR, exist_ok=True)

# ── Embedding Model ─────────────────────────────────────────────
_MODEL: SentenceTransformer | None = None

MODEL_NAME = "all-MiniLM-L6-v2"

# ── Chunking Version ────────────────────────────────────────────
CHUNKING_VERSION = "v2"


def get_doc_hash(text: str) -> str:

    payload = (
        CHUNKING_VERSION
        + text
    )

    return hashlib.md5(
        payload.encode("utf-8")
    ).hexdigest()


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

        logger.info("Model loaded.")

    return _MODEL


# ── Smart Chunking ──────────────────────────────────────────────
def chunk_text(
    text: str,
    size: int = 500,
    overlap: int = 100
) -> List[str]:

    """
    Smart chunking for insurance PDFs.

    Strategy:
    - Preserve table rows
    - Preserve section blocks
    - Preserve bullet groups
    - Avoid splitting clauses
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
                chunks.append(current.strip())

            chunks.append(block)

            current = ""

            continue

        # ── Paragraph accumulation ──────────────
        if len(current) + len(block) < size:

            current += "\n\n" + block

        else:

            if current.strip():
                chunks.append(current.strip())

            # ── Overlap retention ───────────────
            tail = current[-overlap:]

            if " " in tail:
                tail = tail.split(" ", 1)[-1]

            current = tail + "\n\n" + block

    # ── Final chunk ────────────────────────────
    if current.strip():
        chunks.append(current.strip())

    # ── Cleanup ────────────────────────────────
    cleaned = []

    seen = set()

    for c in chunks:

        c = c.strip()

        if len(c) < 50:
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

    doc_hash = get_doc_hash(text)

    faiss_path = os.path.join(
        CACHE_DIR,
        f"{cache_prefix}_{doc_hash}.faiss"
    )

    chunks_path = os.path.join(
        CACHE_DIR,
        f"{cache_prefix}_{doc_hash}.pkl"
    )

    # ── CACHE HIT ─────────────────────────────
    if (
        os.path.exists(faiss_path)
        and os.path.exists(chunks_path)
    ):

        logger.info(
            "Loading cached %s index",
            cache_prefix
        )

        # ── Cache cleanup ──────────────────────
        cache_files = sorted(
            [
                os.path.join(CACHE_DIR, f)
                for f in os.listdir(CACHE_DIR)
            ],
            key=os.path.getmtime
        )

        cache_pairs = len(cache_files) // 2

        if cache_pairs > MAX_CACHE_FILES:

            files_to_remove = cache_files[:(
                (cache_pairs - MAX_CACHE_FILES) * 2
            )]

            for old_file in files_to_remove:

                try:
                    os.remove(old_file)

                except Exception:
                    pass

        index = faiss.read_index(faiss_path)

        with open(chunks_path, "rb") as f:
            chunks = pickle.load(f)

        return index, chunks

    # ── CACHE MISS ────────────────────────────
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

    # ── Save cache ────────────────────────────
    faiss.write_index(
        index,
        faiss_path
    )

    with open(chunks_path, "wb") as f:
        pickle.dump(chunks, f)

    logger.info(
        "Cached %s index with %d vectors",
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