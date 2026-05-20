"""
MetaLegal Indexer — Page Index Engine

Builds and manages in-memory precomputed indexes for ultra-fast routing:

  1. URL Index      → slug → [page_id]
  2. Keyword Index  → keyword → [chunk_id]
  3. FAQ Index      → question_hash → chunk_id
  4. Heading Index  → heading_text → [chunk_id]
  5. Intent Index   → intent_label → [page_id]
  6. Service Index  → service_kw → [page_id]

Indexes are built ONCE at startup and cached in memory.
They are also persisted as JSON to ./data/indexes/ for fast reload.
"""

import json
import logging
import re
import sqlite3
from pathlib import Path
from typing import Optional

from src.config import (
    INDEXES_DIR,
    PAGES_JSON_DIR,
    INTENT_MAP,
)
from src.database import get_connection

logger = logging.getLogger("indexer")


class IndexEngine:
    """
    In-memory index store. Loaded once at startup.
    Enables sub-millisecond page routing before FTS5 search.
    """

    def __init__(self):
        self.url_index:     dict[str, list[str]] = {}   # slug → [page_id]
        self.keyword_index: dict[str, list[str]] = {}   # keyword → [chunk_id]
        self.faq_index:     dict[str, str]        = {}   # question_text → chunk_id
        self.heading_index: dict[str, list[str]]  = {}   # heading → [chunk_id]
        self.intent_index:  dict[str, list[str]]  = {}   # intent → [page_id]
        self.page_url_map:  dict[str, str]         = {}   # page_id → url
        self.page_title_map: dict[str, str]        = {}   # page_id → title
        self.loaded = False

    # ── Build ─────────────────────────────────────────────────────────────────

    def build(self):
        """Rebuild all indexes from DB. Call once after ingestion."""
        logger.info("Building indexes from database...")
        conn = get_connection()

        self._build_url_index(conn)
        self._build_keyword_index(conn)
        self._build_faq_index(conn)
        self._build_heading_index(conn)
        self._build_intent_index(conn)
        self._build_page_maps(conn)

        conn.close()
        self._persist_indexes()
        self.loaded = True
        logger.info("Indexes built and cached ✓")

    def _build_url_index(self, conn: sqlite3.Connection):
        rows = conn.execute("SELECT page_id, slug, url FROM pages").fetchall()
        for r in rows:
            slug = r["slug"] or ""
            for part in [slug] + re.split(r"[-/]", slug):
                part = part.strip().lower()
                if part:
                    self.url_index.setdefault(part, [])
                    if r["page_id"] not in self.url_index[part]:
                        self.url_index[part].append(r["page_id"])
            # Also index by URL path segments
            from urllib.parse import urlparse
            path_parts = urlparse(r["url"]).path.strip("/").split("/")
            for part in path_parts:
                part = part.lower().strip()
                if part:
                    self.url_index.setdefault(part, [])
                    if r["page_id"] not in self.url_index[part]:
                        self.url_index[part].append(r["page_id"])

    def _build_keyword_index(self, conn: sqlite3.Connection):
        rows = conn.execute(
            "SELECT keyword, chunk_id, page_id, weight FROM keyword_index"
        ).fetchall()
        for r in rows:
            kw = r["keyword"].lower()
            self.keyword_index.setdefault(kw, [])
            if r["chunk_id"] not in self.keyword_index[kw]:
                self.keyword_index[kw].append(r["chunk_id"])

    def _build_faq_index(self, conn: sqlite3.Connection):
        rows = conn.execute(
            "SELECT question, chunk_id FROM faq_index"
        ).fetchall()
        for r in rows:
            self.faq_index[r["question"].lower()] = r["chunk_id"]

    def _build_heading_index(self, conn: sqlite3.Connection):
        rows = conn.execute(
            "SELECT chunk_id, section, page_id FROM chunks"
        ).fetchall()
        for r in rows:
            section = (r["section"] or "").lower()
            if section:
                self.heading_index.setdefault(section, [])
                if r["chunk_id"] not in self.heading_index[section]:
                    self.heading_index[section].append(r["chunk_id"])

    def _build_intent_index(self, conn: sqlite3.Connection):
        rows = conn.execute(
            "SELECT page_id, slug, title, full_text FROM pages"
        ).fetchall()
        for r in rows:
            combined = f"{r['slug']} {r['title']} {(r['full_text'] or '')[:500]}".lower()
            for intent, keywords in INTENT_MAP.items():
                for kw in keywords:
                    # Match only on complete words using word boundaries to prevent false substrings (like "roc" in "procedure")
                    pattern = r'\b' + re.escape(kw) + r'\b'
                    if re.search(pattern, combined):
                        self.intent_index.setdefault(intent, [])
                        if r["page_id"] not in self.intent_index[intent]:
                            self.intent_index[intent].append(r["page_id"])
                        break

    def _build_page_maps(self, conn: sqlite3.Connection):
        rows = conn.execute("SELECT page_id, url, title FROM pages").fetchall()
        for r in rows:
            self.page_url_map[r["page_id"]] = r["url"]
            self.page_title_map[r["page_id"]] = r["title"] or ""

    # ── Persist ───────────────────────────────────────────────────────────────

    def _persist_indexes(self):
        """Save indexes to JSON for fast startup reload."""
        data = {
            "url_index":     self.url_index,
            "keyword_index": self.keyword_index,
            "faq_index":     self.faq_index,
            "heading_index": self.heading_index,
            "intent_index":  self.intent_index,
            "page_url_map":  self.page_url_map,
            "page_title_map":self.page_title_map,
        }
        for name, index in data.items():
            out = INDEXES_DIR / f"{name}.json"
            with open(out, "w", encoding="utf-8") as f:
                json.dump(index, f, ensure_ascii=False)
        logger.info(f"Indexes persisted to {INDEXES_DIR}")

    def load_from_disk(self) -> bool:
        """Load prebuilt indexes from disk. Returns False if not found."""
        index_names = [
            "url_index", "keyword_index", "faq_index",
            "heading_index", "intent_index", "page_url_map", "page_title_map",
        ]
        for name in index_names:
            path = INDEXES_DIR / f"{name}.json"
            if not path.exists():
                logger.warning(f"Index not found: {path}")
                return False

        self.url_index      = json.loads((INDEXES_DIR / "url_index.json").read_text())
        self.keyword_index  = json.loads((INDEXES_DIR / "keyword_index.json").read_text())
        self.faq_index      = json.loads((INDEXES_DIR / "faq_index.json").read_text())
        self.heading_index  = json.loads((INDEXES_DIR / "heading_index.json").read_text())
        self.intent_index   = json.loads((INDEXES_DIR / "intent_index.json").read_text())
        self.page_url_map   = json.loads((INDEXES_DIR / "page_url_map.json").read_text())
        self.page_title_map = json.loads((INDEXES_DIR / "page_title_map.json").read_text())
        self.loaded = True
        logger.info("Indexes loaded from disk ✓")
        return True

    # ── Lookup ────────────────────────────────────────────────────────────────

    def pages_for_intent(self, intent: str) -> list[str]:
        return self.intent_index.get(intent.lower(), [])

    def pages_for_slug(self, slug: str) -> list[str]:
        return self.url_index.get(slug.lower(), [])

    def chunks_for_keyword(self, keyword: str) -> list[str]:
        return self.keyword_index.get(keyword.lower(), [])

    def chunk_for_faq(self, question: str) -> Optional[str]:
        return self.faq_index.get(question.lower())

    def chunks_for_heading(self, heading: str) -> list[str]:
        return self.heading_index.get(heading.lower(), [])

    def get_url(self, page_id: str) -> str:
        return self.page_url_map.get(page_id, "")

    def get_title(self, page_id: str) -> str:
        return self.page_title_map.get(page_id, "")


# ── Singleton ─────────────────────────────────────────────────────────────────
_index_engine: Optional[IndexEngine] = None


def get_index_engine() -> IndexEngine:
    global _index_engine
    if _index_engine is None:
        _index_engine = IndexEngine()
        if not _index_engine.load_from_disk():
            _index_engine.build()
    return _index_engine


def rebuild_indexes():
    global _index_engine
    _index_engine = IndexEngine()
    _index_engine.build()
    return _index_engine


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s")
    eng = get_index_engine()
    print(f"URL index entries:     {len(eng.url_index)}")
    print(f"Keyword index entries: {len(eng.keyword_index)}")
    print(f"FAQ index entries:     {len(eng.faq_index)}")
    print(f"Intent index entries:  {len(eng.intent_index)}")
