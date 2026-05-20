"""
MetaLegal Retrieval Engine — Vectorless BM25 + FTS5

Retrieval strategy:
  1. Page Routing     → narrow candidate pages via index
  2. FTS5 Search      → SQLite BM25 scoring over chunks
  3. Re-ranking       → priority_score + section-type boosts + keyword overlap
  4. Confidence Gate  → filter out low-confidence results
  5. Deduplication    → remove near-duplicate chunks

All retrieval is from LOCAL SQLite DB. Zero internet.
"""

import json
import logging
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Optional

from src.config import (
    TOP_K_CHUNKS,
    BM25_BOOST_TITLE,
    BM25_BOOST_FAQ,
    BM25_BOOST_HEADING,
    CONFIDENCE_THRESHOLD,
)
from src.database import get_connection
from src.indexer import get_index_engine
from src.query_processor import ProcessedQuery

logger = logging.getLogger("retrieval")


@dataclass
class RetrievedChunk:
    chunk_id:     str
    page_id:      str
    page_title:   str
    url:          str
    section:      str
    section_type: str
    text:         str
    keywords:     list[str]
    entities:     list[str]
    priority_score: float
    bm25_score:   float
    final_score:  float
    source_type:  str = "fts"  # "fts" | "keyword" | "faq"


def _parse_chunk_row(row: sqlite3.Row, bm25_score: float, final_score: float, source: str = "fts") -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=row["chunk_id"],
        page_id=row["page_id"],
        page_title=row["page_title"] or "",
        url=row["url"] or "",
        section=row["section"] or "",
        section_type=row["section_type"] or "general",
        text=row["text"] or "",
        keywords=json.loads(row["keywords"] or "[]"),
        entities=json.loads(row["important_entities"] or "[]"),
        priority_score=row["priority_score"] or 0.5,
        bm25_score=bm25_score,
        final_score=final_score,
        source_type=source,
    )


def route_to_pages(pq: ProcessedQuery, conn: sqlite3.Connection) -> list[str]:
    """
    Use index engine to find candidate page IDs before FTS search.
    Returns list of page_ids (may be empty → search all chunks).
    """
    engine = get_index_engine()
    candidate_pages: set[str] = set()

    # Intent-based routing
    for intent in pq.intents:
        if intent != "general":
            candidate_pages.update(engine.pages_for_intent(intent))

    # Keyword-based routing
    for kw in pq.routing_keywords[:5]:
        candidate_pages.update(engine.pages_for_slug(kw))

    # Keyword index routing
    for kw in pq.keywords[:8]:
        chunk_ids = engine.chunks_for_keyword(kw)
        # Get page IDs from chunks
        if chunk_ids:
            placeholders = ",".join("?" * min(len(chunk_ids), 50))
            rows = conn.execute(
                f"SELECT DISTINCT page_id FROM chunks WHERE chunk_id IN ({placeholders})",
                chunk_ids[:50],
            ).fetchall()
            for r in rows:
                candidate_pages.add(r["page_id"])

    logger.debug(f"Page routing found {len(candidate_pages)} candidate pages")
    return list(candidate_pages)


def fts5_search(
    pq: ProcessedQuery,
    conn: sqlite3.Connection,
    page_ids: Optional[list[str]] = None,
    limit: int = 20,
) -> list[RetrievedChunk]:
    """
    Run FTS5 BM25 search. Optionally restrict to specific page_ids.
    SQLite FTS5 bm25() returns negative scores (lower = better match).
    """
    results = []

    # Build FTS match expression
    clean_terms = [re.sub(r"[^\w\s]", "", t) for t in pq.keywords[:8] if len(t) > 2]
    if not clean_terms:
        clean_terms = [re.sub(r"[^\w\s]", "", t) for t in pq.original_tokens[:5] if len(t) > 2]

    if not clean_terms:
        return []

    # FTS5 match string: term1 OR term2 ...
    match_str = " OR ".join(clean_terms)

    try:
        if page_ids:
            # Restrict to candidate pages
            placeholders = ",".join("?" * min(len(page_ids), 800))
            query = f"""
                SELECT
                    c.chunk_id, c.page_id, c.page_title, c.url, c.section,
                    c.section_type, c.text, c.keywords, c.important_entities,
                    c.priority_score,
                    bm25(chunks_fts) AS bm25_score
                FROM chunks_fts
                JOIN chunks c ON chunks_fts.rowid = c.rowid
                WHERE chunks_fts MATCH ? AND c.page_id IN ({placeholders})
                ORDER BY bm25_score
                LIMIT ?
            """
            rows = conn.execute(query, [match_str] + page_ids[:800] + [limit]).fetchall()
        else:
            query = """
                SELECT
                    c.chunk_id, c.page_id, c.page_title, c.url, c.section,
                    c.section_type, c.text, c.keywords, c.important_entities,
                    c.priority_score,
                    bm25(chunks_fts) AS bm25_score
                FROM chunks_fts
                JOIN chunks c ON chunks_fts.rowid = c.rowid
                WHERE chunks_fts MATCH ?
                ORDER BY bm25_score
                LIMIT ?
            """
            rows = conn.execute(query, [match_str, limit]).fetchall()

        for row in rows:
            # Normalize BM25 score: FTS5 returns negative, lower = better.
            # Convert negative BM25 score to positive (higher is better) so section/keyword boosts scale correctly.
            raw_bm25 = row["bm25_score"] or 0.0
            bm25_norm = max(0.0, -raw_bm25)
            final = bm25_norm
            results.append(_parse_chunk_row(row, bm25_norm, final, "fts"))

    except sqlite3.OperationalError as e:
        logger.warning(f"FTS5 search error for '{match_str}': {e}")
        # Fallback: LIKE search
        results = _fallback_like_search(pq, conn, page_ids, limit)

    return results


def _fallback_like_search(
    pq: ProcessedQuery,
    conn: sqlite3.Connection,
    page_ids: Optional[list[str]],
    limit: int,
) -> list[RetrievedChunk]:
    """Fallback to LIKE when FTS5 fails (e.g. special characters)."""
    results = []
    terms = pq.keywords[:3]
    if not terms:
        return []

    conditions = " OR ".join(["c.text LIKE ?"] * len(terms))
    params = [f"%{t}%" for t in terms]

    if page_ids:
        ph = ",".join("?" * min(len(page_ids), 800))
        sql = f"""
            SELECT chunk_id, page_id, page_title, url, section,
                   section_type, text, keywords, important_entities, priority_score
            FROM chunks c
            WHERE ({conditions}) AND page_id IN ({ph})
            LIMIT ?
        """
        rows = conn.execute(sql, params + page_ids[:800] + [limit]).fetchall()
    else:
        sql = f"""
            SELECT chunk_id, page_id, page_title, url, section,
                   section_type, text, keywords, important_entities, priority_score
            FROM chunks c
            WHERE {conditions}
            LIMIT ?
        """
        rows = conn.execute(sql, params + [limit]).fetchall()

    for row in rows:
        results.append(_parse_chunk_row(row, 0.3, 0.3, "fallback"))

    return results


def keyword_index_search(pq: ProcessedQuery) -> list[str]:
    """Get chunk IDs from prebuilt keyword index for supplemental retrieval."""
    engine = get_index_engine()
    chunk_ids: list[str] = []
    for kw in pq.keywords[:10]:
        chunk_ids.extend(engine.chunks_for_keyword(kw))
    return list(set(chunk_ids))


def faq_search(pq: ProcessedQuery) -> Optional[str]:
    """Check FAQ index for near-exact question match."""
    from src.query_processor import score_faq_similarity
    engine = get_index_engine()
    best_score = 0.0
    best_chunk_id = None
    for question, chunk_id in engine.faq_index.items():
        score = score_faq_similarity(pq.raw, question)
        if score > best_score:
            best_score = score
            best_chunk_id = chunk_id
    if best_score >= 0.4:
        return best_chunk_id
    return None


def rerank(
    chunks: list[RetrievedChunk],
    pq: ProcessedQuery,
) -> list[RetrievedChunk]:
    """
    Re-rank with section boosts + keyword overlap + priority score.
    """
    query_kw_set = set(pq.keywords)

    for chunk in chunks:
        boost = 1.0

        # Section type boosts
        if chunk.section_type == "faq":
            boost *= BM25_BOOST_FAQ
        if "title" in chunk.section.lower() or "overview" in chunk.section.lower():
            boost *= BM25_BOOST_TITLE
        if chunk.section_type in ("requirements", "process", "pricing"):
            boost *= BM25_BOOST_HEADING

        # Keyword overlap bonus
        chunk_kws = set(chunk.keywords)
        overlap = len(query_kw_set & chunk_kws) / max(len(query_kw_set), 1)
        boost += overlap * 0.5

        # Priority score integration
        chunk.final_score = chunk.bm25_score * boost * (0.5 + chunk.priority_score)

    # Sort descending by final_score
    return sorted(chunks, key=lambda c: c.final_score, reverse=True)


def deduplicate(chunks: list[RetrievedChunk]) -> list[RetrievedChunk]:
    """Remove chunks with identical content hashes or very similar text prefixes."""
    seen_prefixes: set[str] = set()
    seen_ids: set[str] = set()
    unique = []
    for c in chunks:
        prefix = c.text[:80].lower()
        if c.chunk_id in seen_ids or prefix in seen_prefixes:
            continue
        seen_ids.add(c.chunk_id)
        seen_prefixes.add(prefix)
        unique.append(c)
    return unique


def compute_confidence(chunks: list[RetrievedChunk], pq: ProcessedQuery) -> float:
    """
    Aggregate confidence score 0–1 based on:
    - Number of returned chunks
    - Max BM25 score
    - Keyword overlap
    """
    if not chunks:
        return 0.0
    top = chunks[0]
    count_score = min(len(chunks) / TOP_K_CHUNKS, 1.0) * 0.3
    overlap_score = min(
        len(set(pq.keywords) & set(top.keywords)) / max(len(pq.keywords), 1), 1.0
    ) * 0.4
    priority_score = top.priority_score * 0.3
    return round(count_score + overlap_score + priority_score, 2)


def retrieve(pq: ProcessedQuery) -> tuple[list[RetrievedChunk], float]:
    """
    Main retrieval function.
    Returns (top_chunks, confidence_score).
    """
    conn = get_connection()
    all_chunks: list[RetrievedChunk] = []
    seen_ids: set[str] = set()

    # ── Step 1: Page routing ──────────────────────────────────────────────────
    candidate_pages = route_to_pages(pq, conn)

    # ── Step 2: FTS5 search (primary) ────────────────────────────────────────
    fts_results = fts5_search(pq, conn, candidate_pages or None, limit=25)
    for r in fts_results:
        if r.chunk_id not in seen_ids:
            all_chunks.append(r)
            seen_ids.add(r.chunk_id)

    # ── Step 3: If routed but poor results, broaden search ───────────────────
    if len(all_chunks) < 3 and candidate_pages:
        logger.debug("Broadening search to all chunks")
        broad = fts5_search(pq, conn, page_ids=None, limit=20)
        for r in broad:
            if r.chunk_id not in seen_ids:
                all_chunks.append(r)
                seen_ids.add(r.chunk_id)

    # ── Step 4: Supplement with keyword index ────────────────────────────────
    kw_chunk_ids = keyword_index_search(pq)
    if kw_chunk_ids:
        ph = ",".join("?" * min(len(kw_chunk_ids), 50))
        rows = conn.execute(
            f"SELECT chunk_id, page_id, page_title, url, section, section_type, "
            f"text, keywords, important_entities, priority_score FROM chunks "
            f"WHERE chunk_id IN ({ph})",
            kw_chunk_ids[:50],
        ).fetchall()
        for row in rows:
            if row["chunk_id"] not in seen_ids:
                all_chunks.append(_parse_chunk_row(row, 0.2, 0.2, "keyword"))
                seen_ids.add(row["chunk_id"])

    # ── Step 5: FAQ exact match ───────────────────────────────────────────────
    if pq.is_faq_like:
        faq_chunk_id = faq_search(pq)
        if faq_chunk_id and faq_chunk_id not in seen_ids:
            row = conn.execute(
                "SELECT chunk_id, page_id, page_title, url, section, section_type, "
                "text, keywords, important_entities, priority_score FROM chunks "
                "WHERE chunk_id = ?",
                [faq_chunk_id],
            ).fetchone()
            if row:
                faq_chunk = _parse_chunk_row(row, 1.0, 2.0, "faq")  # high priority
                all_chunks.insert(0, faq_chunk)
                seen_ids.add(faq_chunk_id)

    conn.close()

    # ── Step 6: Re-rank ───────────────────────────────────────────────────────
    ranked = rerank(all_chunks, pq)

    # ── Step 7: Deduplicate ───────────────────────────────────────────────────
    deduped = deduplicate(ranked)

    # ── Step 8: Take top K ───────────────────────────────────────────────────
    top_k = deduped[:TOP_K_CHUNKS]

    # ── Step 9: Confidence ────────────────────────────────────────────────────
    confidence = compute_confidence(top_k, pq)

    logger.info(
        f"Retrieved {len(top_k)} chunks | confidence={confidence:.2f} | "
        f"intents={pq.intents}"
    )

    return top_k, confidence
