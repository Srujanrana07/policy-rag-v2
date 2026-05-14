"""
Document loading & extraction.
Strategy: Try camelot table extraction first (great for insurance sublimit tables),
fall back to PyMuPDF text extraction.
"""

import os
import re
import time
import logging
from typing import Optional, Tuple
from urllib.parse import urlparse

import fitz  # PyMuPDF
import requests

logger = logging.getLogger(__name__)

PDF_DIR = os.getenv("PDF_STORAGE_DIR", "pdfs")
os.makedirs(PDF_DIR, exist_ok=True)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _extract_urls(text: str) -> list[str]:
    return list(set(re.findall(r"https?://[^\s)\]]+", text)))


def _clean_table_row(row) -> list[str]:
    return [str(c).strip().replace("`", "").replace("\n", " ") for c in row if c is not None]


def _detect_tier(row: list[str]) -> Optional[str]:
    joined = " ".join(row).replace(",", "")
    amounts = list(map(int, re.findall(r"\d{2,7}", joined)))
    if not amounts:
        return None
    if any(a in [25000, 100000, 200000] for a in amounts):
        return "3L/4L/5L"
    if any(a in [50000, 175000, 350000] for a in amounts):
        return "10L/15L/20L"
    if any(a in [75000, 250000, 500000] for a in amounts):
        return ">20L"
    return None


# ── Downloaders ───────────────────────────────────────────────────────────────

def download_pdf(url: str) -> Optional[str]:
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/pdf"}
    try:
        resp = requests.get(url, headers=headers, stream=True, timeout=30)
        resp.raise_for_status()
        ct = resp.headers.get("Content-Type", "").lower()
        if "pdf" not in ct and not url.lower().endswith(".pdf"):
            logger.warning("URL does not appear to be PDF: %s", url)
            return None

        fname = os.path.basename(urlparse(url).path) or "doc.pdf"
        local = os.path.join(PDF_DIR, f"{int(time.time())}_{fname}")
        with open(local, "wb") as f:
            for chunk in resp.iter_content(8192):
                if chunk:
                    f.write(chunk)
        logger.info("Downloaded %s → %s", url, local)
        return local
    except Exception as exc:
        logger.error("Download failed for %s: %s", url, exc)
        return None


# ── Extractors ────────────────────────────────────────────────────────────────

def extract_tables(pdf_path: str) -> Optional[str]:
    """Camelot-based table extraction with tier labelling."""
    try:
        import camelot  # imported lazily so startup is fast if missing
        tables = camelot.read_pdf(
            pdf_path,
            pages="all",
            flavor="lattice",
            line_scale=40
        )
        rows, urls = [], set()
        for tbl in tables:
            for raw_row in tbl.df.values.tolist():
                row = _clean_table_row(raw_row)
                if len(row) < 2:
                    continue
                urls.update(_extract_urls(" ".join(row)))
                tier = _detect_tier(row)
                if tier:
                    rows.append([tier] + row)

        if not rows:
            return None

        out = "\n".join(" | ".join(r) for r in rows)
        if urls:
            out += "\n\n🔗 URLs:\n" + "\n".join(sorted(urls))
        logger.info("Table extraction: %d rows from %s", len(rows), pdf_path)
        return out
    except Exception as exc:
        logger.warning("Camelot failed: %s", exc)

        try:
            import pdfplumber

            rows = []

            with pdfplumber.open(pdf_path) as pdf:
                for page in pdf.pages:
                    tables = page.extract_tables()

                    for table in tables:
                        for row in table:
                            cleaned = _clean_table_row(row)

                            if len(cleaned) >= 2:
                                rows.append(" | ".join(cleaned))

            if rows:
                logger.info("pdfplumber extracted %d rows", len(rows))
                return "\n".join(rows)

        except Exception as fallback_exc:
            logger.warning("pdfplumber fallback failed: %s", fallback_exc)

        return None


def extract_text(pdf_path: str) -> Optional[str]:
    """PyMuPDF full-text extraction."""
    try:
        parts, urls = [], set()
        with fitz.open(pdf_path) as doc:
            for page in doc:
                t = page.get_text()
                if t:
                    parts.append(t)
                    urls.update(_extract_urls(t))
        if not parts:
            return None
        out = "".join(parts).strip()
        if urls:
            out += "\n\n🔗 URLs:\n" + "\n".join(sorted(urls))
        logger.info("Text extraction: %d chars from %s", len(out), pdf_path)
        return out
    except Exception as exc:
        logger.error("Text extraction failed for %s: %s", pdf_path, exc)
        return None


# ── Public API ────────────────────────────────────────────────────────────────

def load_document(path_or_url: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Returns (table_text, full_text) for a single document.
    Handles both URLs and local paths.
    """
    if path_or_url.startswith("http"):
        local = download_pdf(path_or_url)
        if not local:
            return None, None
    else:
        local = path_or_url
        if not os.path.isfile(local):
            logger.error("Local file not found: %s", local)
            return None, None

    return extract_tables(local), extract_text(local)


def load_documents(paths: list[str]) -> Tuple[str, str]:
    """
    Load multiple documents. Returns combined (table_text, full_text).
    """
    all_tables, all_text = [], []
    for p in paths:
        t, tx = load_document(p)
        label = f"--- Document: {os.path.basename(p)} ---"
        if t:
            all_tables.append(f"{label}\n{t}")
        if tx:
            all_text.append(f"{label}\n{tx}")

    return "\n\n".join(all_tables), "\n\n".join(all_text)
