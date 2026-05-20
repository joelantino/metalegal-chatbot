"""
MetaLegal Chunker — Semantic Chunking Pipeline

Takes raw page JSONs from ./data/pages/
Outputs semantic chunk JSONs to ./data/chunks/

Chunking strategy:
  - Split by headings (h2, h3)
  - Merge short paragraphs
  - Cap at MAX_CHUNK_WORDS words
  - Each chunk retains full provenance metadata
"""

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Generator

from src.config import (
    PAGES_JSON_DIR,
    CHUNKS_JSON_DIR,
    MIN_CHUNK_WORDS,
    MAX_CHUNK_WORDS,
)

logger = logging.getLogger("chunker")


# ── Semantic section types ────────────────────────────────────────────────────
SECTION_TYPE_MAP: dict[str, str] = {
    "introduction": "intro",
    "overview": "intro",
    "about":        "intro",
    "document":     "requirements",
    "required":     "requirements",
    "eligibility":  "requirements",
    "process":      "process",
    "step":         "process",
    "procedure":    "process",
    "benefit":      "benefits",
    "advantage":    "benefits",
    "feature":      "benefits",
    "faq":          "faq",
    "question":     "faq",
    "contact":      "contact",
    "reach":        "contact",
    "price":        "pricing",
    "fee":          "pricing",
    "cost":         "pricing",
    "penalty":      "legal",
    "compliance":   "legal",
    "law":          "legal",
    "regulation":   "legal",
}


def classify_section(section_name: str) -> str:
    name_lower = section_name.lower()
    for kw, stype in SECTION_TYPE_MAP.items():
        if kw in name_lower:
            return stype
    return "general"


def extract_keywords_from_chunk(text: str) -> list[str]:
    stop = {
        "a","an","the","is","are","was","were","be","been","have","has","had",
        "do","does","will","would","should","could","may","might","in","of",
        "to","for","with","on","at","by","from","that","this","and","or","but",
        "it","its","as","if","then","so","no","not","also","can","all","any",
    }
    words = re.findall(r"[a-z]{3,}", text.lower())
    freq: dict[str, int] = {}
    for w in words:
        if w not in stop:
            freq[w] = freq.get(w, 0) + 1
    return sorted(freq, key=lambda x: -freq[x])[:15]


def extract_entities(text: str) -> list[str]:
    """Heuristic entity extraction — capitalized multi-word phrases."""
    entities = re.findall(r"\b[A-Z][a-z]+(?:\s[A-Z][a-z]+)*\b", text)
    # Also catch acronyms
    acronyms = re.findall(r"\b[A-Z]{2,6}\b", text)
    combined = list(set(entities + acronyms))
    return combined[:20]


def compute_priority(section_type: str, word_count: int, has_faq: bool) -> float:
    """Score 0–1. Higher = more important chunk."""
    base = 0.5
    type_boost = {
        "requirements": 0.2,
        "faq":          0.25,
        "process":      0.15,
        "pricing":      0.2,
        "legal":        0.1,
        "intro":        0.05,
    }.get(section_type, 0.0)
    length_score = min(word_count / MAX_CHUNK_WORDS, 1.0) * 0.15
    faq_bonus = 0.1 if has_faq else 0.0
    return round(min(base + type_boost + length_score + faq_bonus, 1.0), 2)


def word_count(text: str) -> int:
    return len(text.split())


def chunk_content_sections(
    page: dict,
) -> Generator[dict, None, None]:
    """
    Yield chunk dicts from a page's content sections.
    Merges tiny sections, splits huge ones.
    """
    content: dict = page.get("content_sections", {})
    page_id = page["page_id"]
    url = page["url"]
    page_title = page["title"]
    chunk_counter = 0

    buffer_text = ""
    buffer_section = "introduction"

    def emit(text: str, section: str) -> dict:
        nonlocal chunk_counter
        chunk_counter += 1
        wc = word_count(text)
        stype = classify_section(section)
        kws = extract_keywords_from_chunk(text)
        entities = extract_entities(text)
        priority = compute_priority(stype, wc, False)
        cid = f"{page_id}_chunk_{chunk_counter:03d}"
        content_hash = hashlib.sha256(text.encode()).hexdigest()
        return {
            "chunk_id":         cid,
            "page_id":          page_id,
            "page_title":       page_title,
            "url":              url,
            "section":          section.replace("_", " ").title(),
            "section_type":     stype,
            "text":             text.strip(),
            "word_count":       wc,
            "keywords":         kws,
            "important_entities": entities,
            "priority_score":   priority,
            "content_hash":     content_hash,
        }

    for section, text in content.items():
        if not text or word_count(text) < 10:
            continue

        wc = word_count(text)

        if wc < MIN_CHUNK_WORDS:
            # Too small — buffer with next section
            buffer_text += " " + text
            buffer_section = section
            if word_count(buffer_text) >= MIN_CHUNK_WORDS:
                yield emit(buffer_text.strip(), buffer_section)
                buffer_text = ""
                buffer_section = "introduction"
        elif wc <= MAX_CHUNK_WORDS:
            # Flush buffer first if any
            if buffer_text.strip():
                yield emit(buffer_text.strip(), buffer_section)
                buffer_text = ""
            yield emit(text, section)
        else:
            # Too large — split into sub-chunks
            if buffer_text.strip():
                yield emit(buffer_text.strip(), buffer_section)
                buffer_text = ""
            sentences = re.split(r"(?<=[.!?])\s+", text)
            sub_buffer = ""
            for sent in sentences:
                if word_count(sub_buffer + " " + sent) <= MAX_CHUNK_WORDS:
                    sub_buffer += " " + sent
                else:
                    if sub_buffer.strip():
                        yield emit(sub_buffer.strip(), section)
                    sub_buffer = sent
            if sub_buffer.strip():
                yield emit(sub_buffer.strip(), section)

    # Flush remaining buffer
    if buffer_text.strip() and word_count(buffer_text) >= 15:
        yield emit(buffer_text.strip(), buffer_section)


def chunk_faqs(page: dict) -> Generator[dict, None, None]:
    """Create dedicated FAQ chunks — each FAQ pair is its own high-priority chunk."""
    faqs = page.get("faq", [])
    page_id = page["page_id"]
    url = page["url"]
    page_title = page["title"]

    for i, faq in enumerate(faqs):
        q = faq.get("question", "")
        a = faq.get("answer", "")
        if not q or not a:
            continue
        text = f"Q: {q}\nA: {a}"
        cid = f"{page_id}_faq_{i+1:03d}"
        kws = extract_keywords_from_chunk(text)
        entities = extract_entities(text)
        content_hash = hashlib.sha256(text.encode()).hexdigest()
        yield {
            "chunk_id":           cid,
            "page_id":            page_id,
            "page_title":         page_title,
            "url":                url,
            "section":            "FAQ",
            "section_type":       "faq",
            "text":               text,
            "word_count":         word_count(text),
            "keywords":           kws,
            "important_entities": entities,
            "priority_score":     0.95,
            "content_hash":       content_hash,
        }


def chunk_title_intro(page: dict) -> dict | None:
    """Create a special title+meta chunk for fast page routing."""
    title = page.get("title", "")
    meta = page.get("meta_description", "")
    h1 = page.get("h1", "")
    if not (title or meta):
        return None
    text = f"{h1 or title}. {meta}".strip()
    page_id = page["page_id"]
    cid = f"{page_id}_title"
    kws = extract_keywords_from_chunk(text + " " + " ".join(page.get("keywords", [])))
    return {
        "chunk_id":           cid,
        "page_id":            page_id,
        "page_title":         title,
        "url":                page["url"],
        "section":            "Title / Overview",
        "section_type":       "intro",
        "text":               text,
        "word_count":         word_count(text),
        "keywords":           kws,
        "important_entities": extract_entities(text),
        "priority_score":     0.80,
        "content_hash":       hashlib.sha256(text.encode()).hexdigest(),
    }


def process_page(page: dict) -> list[dict]:
    """Convert a single page dict into a list of chunks."""
    chunks = []

    # Title chunk
    tc = chunk_title_intro(page)
    if tc:
        chunks.append(tc)

    # Content chunks
    chunks.extend(chunk_content_sections(page))

    # FAQ chunks
    chunks.extend(chunk_faqs(page))

    return chunks


def run_chunker():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    )
    page_files = list(PAGES_JSON_DIR.glob("*.json"))
    total_chunks = 0
    logger.info(f"Processing {len(page_files)} page files...")

    for pf in page_files:
        with open(pf, "r", encoding="utf-8") as f:
            page = json.load(f)

        chunks = process_page(page)
        for chunk in chunks:
            out = CHUNKS_JSON_DIR / f"{chunk['chunk_id']}.json"
            with open(out, "w", encoding="utf-8") as f:
                json.dump(chunk, f, indent=2, ensure_ascii=False)

        total_chunks += len(chunks)
        logger.info(f"  → {pf.name}: {len(chunks)} chunks")

    logger.info(f"\n✅ Chunking complete. {total_chunks} total chunks in {CHUNKS_JSON_DIR}")
    return total_chunks


if __name__ == "__main__":
    run_chunker()
