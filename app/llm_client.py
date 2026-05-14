"""
LLM client wrapping Google Gemini.
Provides both sync and streaming interfaces.
"""

import logging
import os
import re
from typing import Generator
import time

import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

MAX_CONTEXT_CHARS = 12000

_api_key = os.getenv("GEMINI_API_KEY", "")
if _api_key:
    genai.configure(api_key=_api_key)

_MODEL = genai.GenerativeModel("gemini-2.5-flash")
MAX_RETRIES = 3

RETRY_DELAY = 2


SYSTEM_PROMPT = """
You are an expert insurance policy analyst.

Answer ONLY from the provided context.

Rules:
- Be concise and accurate.
- If coverage exists, clearly mention:
  - treatment/procedure
  - coverage amount
  - waiting period if available
  - applicable tier
- If partial information exists, mention what is available.
- Only say "The document does not specify"
  if absolutely no relevant information exists.

Do not invent information.
"""


def _build_prompt(question: str, context_chunks: list[str]) -> str:

    sum_match = re.search(r"(\d+(?:\.\d+)?)\s*[Ll]", question)

    sum_insured = (
        f"{sum_match.group(1)}L"
        if sum_match else "unknown"
    )

    # ── SMART CONTEXT LIMITING ─────────────────────
    selected_chunks = []

    total_chars = 0

    for chunk in context_chunks:

        chunk = chunk.strip()

        if not chunk:
            continue

        chunk_len = len(chunk)

        if total_chars + chunk_len > MAX_CONTEXT_CHARS:
            break

        selected_chunks.append(chunk)

        total_chars += chunk_len

    context = "\n---\n".join(selected_chunks)

    logger.info(
        "Prompt context: %d chunks | %d chars",
        len(selected_chunks),
        total_chars
    )

    return f"""{SYSTEM_PROMPT}

### Sum Insured:
{sum_insured}

### Question:
{question}

### Document Excerpts:
{context}
"""


def answer(
    question: str,
    context_chunks: list[str]
) -> str:

    prompt = _build_prompt(
        question,
        context_chunks
    )

    for attempt in range(MAX_RETRIES):

        try:

            logger.info(
                "Gemini request attempt %d",
                attempt + 1
            )

            resp = _MODEL.generate_content(
                prompt
            )

            if not resp.text:
                raise ValueError(
                    "Empty Gemini response"
                )

            return resp.text.strip()

        except Exception as exc:

            logger.warning(
                "Gemini error on attempt %d: %s",
                attempt + 1,
                exc
            )

            # ── Final failure ──────────────────
            if attempt == MAX_RETRIES - 1:

                logger.error(
                    "Gemini failed after %d attempts",
                    MAX_RETRIES
                )

                return (
                    "❌ LLM temporarily unavailable. "
                    "Please retry in a moment."
                )

            # ── Exponential backoff ────────────
            delay = RETRY_DELAY * (2 ** attempt)

            logger.info(
                "Retrying Gemini in %ds",
                delay
            )

            time.sleep(delay)

    return (
        "❌ Unexpected LLM failure."
    )

def answer_stream(
    question: str,
    context_chunks: list[str]
) -> Generator[str, None, None]:

    prompt = _build_prompt(
        question,
        context_chunks
    )

    for attempt in range(MAX_RETRIES):

        try:

            logger.info(
                "Gemini stream attempt %d",
                attempt + 1
            )

            stream = _MODEL.generate_content(
                prompt,
                stream=True
            )

            for chunk in stream:

                if chunk.text:
                    yield chunk.text

            return

        except Exception as exc:

            logger.warning(
                "Gemini stream error on attempt %d: %s",
                attempt + 1,
                exc
            )

            # ── Final failure ──────────────────
            if attempt == MAX_RETRIES - 1:

                logger.error(
                    "Gemini stream failed after %d attempts",
                    MAX_RETRIES
                )

                yield (
                    "❌ LLM temporarily unavailable."
                )

                return

            # ── Exponential backoff ────────────
            delay = RETRY_DELAY * (2 ** attempt)

            logger.info(
                "Retrying Gemini stream in %ds",
                delay
            )

            time.sleep(delay)
