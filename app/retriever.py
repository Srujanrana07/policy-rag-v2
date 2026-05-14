"""FAISS retrieval."""

import logging

import faiss
from sentence_transformers import SentenceTransformer
from rank_bm25 import BM25Okapi
import re

logger = logging.getLogger(__name__)

QUERY_SYNONYMS = {

    # ── Universal Insurance Terms ────────────
    "si": [
        "sum insured"
    ],

    "idv": [
        "insured declared value"
    ],

    "ncb": [
        "no claim bonus"
    ],

    "gst": [
        "goods and services tax"
    ],

    "emi": [
        "equated monthly installment"
    ],

    # ── Health Insurance ─────────────────────
    "ped": [
        "pre existing disease",
        "pre-existing disease"
    ],

    "icu": [
        "intensive care unit"
    ],

    # ── Common Claim Terminology ─────────────
    "tp": [
        "third party"
    ],

    "od": [
        "own damage"
    ]
}


def tokenize(text: str):

    text = text.lower()

    return re.findall(
        r"\b\w+\b",
        text
    )


def expand_query(
    query: str
) -> str:

    expanded = [query]

    q_lower = query.lower()

    for key, synonyms in QUERY_SYNONYMS.items():

        if key in q_lower:

            expanded.extend(
                synonyms
            )

    expanded_query = " ".join(expanded)

    logger.info(
        "Expanded query: %s",
        expanded_query
    )

    return expanded_query



def _search_index(
    query: str,
    index,
    chunks,
    model,
    k
):

    if index is None or not chunks:
        return []

    vec = model.encode(
        [query],
        convert_to_numpy=True
    )

    faiss.normalize_L2(vec)

    distances, indices = index.search(
        vec.astype("float32"),
        k
    )

    results = []

    seen = set()

    for i, score in zip(indices[0], distances[0]):

        if i >= len(chunks):
            continue

        # Ignore very weak matches
        if score < 0.15:
            continue

        chunk = chunks[i].strip()

        if chunk in seen:
            continue

        seen.add(chunk)

        results.append(chunk)

    return results

def _bm25_search(
    query: str,
    chunks,
    k
):

    if not chunks:
        return []

    tokenized_chunks = [
        tokenize(c)
        for c in chunks
    ]

    bm25 = BM25Okapi(
        tokenized_chunks
    )

    scores = bm25.get_scores(
        tokenize(query)
    )

    ranked = sorted(
        zip(chunks, scores),
        key=lambda x: x[1],
        reverse=True
    )

    results = []

    seen = set()

    for chunk, score in ranked[:k]:

        if chunk in seen:
            continue

        seen.add(chunk)

        results.append(chunk)

    return results


def _rerank_chunks(
    query: str,
    chunks,
    k
):

    query_terms = set(
        tokenize(query)
    )

    scored = []

    for chunk in chunks:

        chunk_terms = set(
            tokenize(chunk)
        )

        # ── Lexical overlap ────────────────────
        overlap = len(
            query_terms & chunk_terms
        )

        # ── Exact phrase boost ─────────────────
        exact_boost = 0

        if query.lower() in chunk.lower():
            exact_boost += 5

        # ── Numeric boost ──────────────────────
        numeric_boost = 0

        query_nums = re.findall(
            r"\d+",
            query
        )

        chunk_nums = re.findall(
            r"\d+",
            chunk
        )

        numeric_overlap = len(
            set(query_nums) & set(chunk_nums)
        )

        numeric_boost += numeric_overlap * 2

        score = (
            overlap
            + exact_boost
            + numeric_boost
        )

        scored.append(
            (chunk, score)
        )

    ranked = sorted(
        scored,
        key=lambda x: x[1],
        reverse=True
    )

    final = []

    seen = set()

    for chunk, score in ranked:

        if chunk in seen:
            continue

        seen.add(chunk)

        final.append(chunk)

    logger.info(
        "Reranked %d chunks",
        len(final)
    )

    return final[:k]


def retrieve(
    query: str,
    indices,
    model: SentenceTransformer,
    k: int = 10,
):

    query = query.lower().strip()

    expanded_query = expand_query(query)

    table_index, table_chunks = indices["table"]

    text_index, text_chunks = indices["text"]

    # ── Semantic Retrieval ────────────────────
    table_semantic = _search_index(
        expanded_query,
        table_index,    
        table_chunks,
        model,
        k
    )

    text_semantic = _search_index(
        expanded_query,
        text_index,
        text_chunks,
        model,
        k
    )

    # ── BM25 Retrieval ────────────────────────
    table_bm25 = _bm25_search(
        expanded_query,
        table_chunks,
        k
    )

    text_bm25 = _bm25_search(
        expanded_query,
        text_chunks,
        k
    )

    # ── Merge Results ─────────────────────────
    combined = (
        table_semantic
        + text_semantic
        + table_bm25
        + text_bm25
    )

    # ── Deduplicate ───────────────────────────
    final = []

    seen = set()

    for chunk in combined:

        chunk = chunk.strip()

        if chunk in seen:
            continue

        seen.add(chunk)

        final.append(chunk)

    logger.info(
        "Retrieved %d hybrid chunks",
        len(final)
    )

    reranked = _rerank_chunks(
        expanded_query,
        final,
        k
    )

    return reranked