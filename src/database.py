"""
MetaLegal Database — SQLite FTS5 Schema + Ingestor

Schema:
  pages   — full page records (JSON)
  chunks  — semantic chunks (full-text searchable via FTS5)
  indexes — prebuilt keyword/URL/FAQ index snapshots

The FTS5 virtual table uses BM25 internally.
"""

import json
import logging
import sqlite3
from pathlib import Path

from src.config import DB_PATH, PAGES_JSON_DIR, CHUNKS_JSON_DIR

logger = logging.getLogger("database")


DDL = """
-- ── Pages table ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS pages (
    page_id         TEXT PRIMARY KEY,
    url             TEXT NOT NULL UNIQUE,
    slug            TEXT,
    title           TEXT,
    h1              TEXT,
    meta_description TEXT,
    page_type       TEXT,
    breadcrumbs     TEXT,   -- JSON array
    headings        TEXT,   -- JSON array
    full_text       TEXT,
    faq_count       INTEGER DEFAULT 0,
    keywords        TEXT,   -- JSON array
    contact_info    TEXT,   -- JSON object
    internal_links  TEXT,   -- JSON array
    last_crawled    TEXT,
    content_hash    TEXT,
    raw_json        TEXT    -- full raw page JSON
);

-- ── Chunks table ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS chunks (
    chunk_id        TEXT PRIMARY KEY,
    page_id         TEXT NOT NULL,
    page_title      TEXT,
    url             TEXT,
    section         TEXT,
    section_type    TEXT,
    text            TEXT NOT NULL,
    word_count      INTEGER,
    keywords        TEXT,   -- JSON array
    important_entities TEXT, -- JSON array
    priority_score  REAL DEFAULT 0.5,
    content_hash    TEXT,
    FOREIGN KEY (page_id) REFERENCES pages(page_id)
);

-- ── FTS5 virtual table (BM25 built-in) ───────────────────────────────────────
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    chunk_id UNINDEXED,
    page_id  UNINDEXED,
    url      UNINDEXED,
    section,
    page_title,
    text,
    keywords,
    content=chunks,
    content_rowid=rowid,
    tokenize='porter ascii'
);

-- ── FTS triggers to keep in sync ─────────────────────────────────────────────
CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
    INSERT INTO chunks_fts(rowid, chunk_id, page_id, url, section, page_title, text, keywords)
    VALUES (new.rowid, new.chunk_id, new.page_id, new.url, new.section, new.page_title, new.text, new.keywords);
END;

CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, chunk_id, page_id, url, section, page_title, text, keywords)
    VALUES ('delete', old.rowid, old.chunk_id, old.page_id, old.url, old.section, old.page_title, old.text, old.keywords);
END;

CREATE TRIGGER IF NOT EXISTS chunks_au AFTER UPDATE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, chunk_id, page_id, url, section, page_title, text, keywords)
    VALUES ('delete', old.rowid, old.chunk_id, old.page_id, old.url, old.section, old.page_title, old.text, old.keywords);
    INSERT INTO chunks_fts(rowid, chunk_id, page_id, url, section, page_title, text, keywords)
    VALUES (new.rowid, new.chunk_id, new.page_id, new.url, new.section, new.page_title, new.text, new.keywords);
END;

-- ── Keyword→chunks inverted index ────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS keyword_index (
    keyword     TEXT NOT NULL,
    chunk_id    TEXT NOT NULL,
    page_id     TEXT NOT NULL,
    weight      REAL DEFAULT 1.0,
    PRIMARY KEY (keyword, chunk_id)
);
CREATE INDEX IF NOT EXISTS idx_keyword ON keyword_index(keyword);

-- ── URL→page index ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS url_index (
    slug        TEXT NOT NULL,
    page_id     TEXT NOT NULL,
    url         TEXT NOT NULL,
    PRIMARY KEY (slug, page_id)
);
CREATE INDEX IF NOT EXISTS idx_slug ON url_index(slug);

-- ── FAQ→chunk index ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS faq_index (
    question_hash   TEXT PRIMARY KEY,
    question        TEXT NOT NULL,
    chunk_id        TEXT NOT NULL,
    page_id         TEXT NOT NULL
);

-- ── Service intent→page index ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS intent_index (
    intent      TEXT NOT NULL,
    page_id     TEXT NOT NULL,
    url         TEXT NOT NULL,
    score       REAL DEFAULT 1.0,
    PRIMARY KEY (intent, page_id)
);
CREATE INDEX IF NOT EXISTS idx_intent ON intent_index(intent);
"""


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-64000")  # 64MB
    conn.execute("PRAGMA temp_store=MEMORY")
    return conn


def initialize_db():
    """Create all tables and FTS indexes."""
    logger.info(f"Initializing database at {DB_PATH}")
    conn = get_connection()
    conn.executescript(DDL)
    conn.commit()
    conn.close()
    logger.info("Database initialized ✓")


def ingest_pages():
    """Load all page JSON files into pages table."""
    conn = get_connection()
    page_files = list(PAGES_JSON_DIR.glob("*.json"))
    logger.info(f"Ingesting {len(page_files)} pages...")

    for pf in page_files:
        with open(pf, "r", encoding="utf-8") as f:
            page = json.load(f)

        conn.execute("""
            INSERT OR REPLACE INTO pages
            (page_id, url, slug, title, h1, meta_description, page_type,
             breadcrumbs, headings, full_text, faq_count, keywords,
             contact_info, internal_links, last_crawled, content_hash, raw_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            page["page_id"],
            page["url"],
            page.get("slug", ""),
            page.get("title", ""),
            page.get("h1", ""),
            page.get("meta_description", ""),
            page.get("page_type", "general"),
            json.dumps(page.get("breadcrumbs", [])),
            json.dumps(page.get("headings", [])),
            page.get("full_text", ""),
            len(page.get("faq", [])),
            json.dumps(page.get("keywords", [])),
            json.dumps(page.get("contact_info", {})),
            json.dumps(page.get("internal_links", [])),
            page.get("last_crawled", ""),
            page.get("content_hash", ""),
            json.dumps(page),
        ))

    conn.commit()
    conn.close()
    logger.info(f"Pages ingested: {len(page_files)}")


def ingest_chunks():
    """Load all chunk JSON files into chunks table (triggers populate FTS)."""
    conn = get_connection()
    chunk_files = list(CHUNKS_JSON_DIR.glob("*.json"))
    logger.info(f"Ingesting {len(chunk_files)} chunks...")

    for cf in chunk_files:
        with open(cf, "r", encoding="utf-8") as f:
            chunk = json.load(f)

        conn.execute("""
            INSERT OR REPLACE INTO chunks
            (chunk_id, page_id, page_title, url, section, section_type,
             text, word_count, keywords, important_entities, priority_score, content_hash)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            chunk["chunk_id"],
            chunk["page_id"],
            chunk.get("page_title", ""),
            chunk.get("url", ""),
            chunk.get("section", ""),
            chunk.get("section_type", "general"),
            chunk["text"],
            chunk.get("word_count", 0),
            json.dumps(chunk.get("keywords", [])),
            json.dumps(chunk.get("important_entities", [])),
            chunk.get("priority_score", 0.5),
            chunk.get("content_hash", ""),
        ))

        # Populate keyword index
        for kw in chunk.get("keywords", []):
            conn.execute("""
                INSERT OR REPLACE INTO keyword_index (keyword, chunk_id, page_id, weight)
                VALUES (?, ?, ?, ?)
            """, (kw.lower(), chunk["chunk_id"], chunk["page_id"], chunk.get("priority_score", 0.5)))

    conn.commit()
    conn.close()
    logger.info(f"Chunks ingested: {len(chunk_files)}")


def build_secondary_indexes():
    """Build URL, FAQ, and Intent indexes from existing DB data."""
    conn = get_connection()

    # URL index from pages
    rows = conn.execute("SELECT page_id, slug, url FROM pages").fetchall()
    for r in rows:
        parts = r["url"].replace("https://", "").replace("http://", "").split("/")
        for part in parts:
            if part and part != urlparse_netloc(r["url"]):
                conn.execute("""
                    INSERT OR REPLACE INTO url_index (slug, page_id, url)
                    VALUES (?, ?, ?)
                """, (part.lower(), r["page_id"], r["url"]))
        if r["slug"]:
            conn.execute("""
                INSERT OR REPLACE INTO url_index (slug, page_id, url)
                VALUES (?, ?, ?)
            """, (r["slug"].lower(), r["page_id"], r["url"]))

    # FAQ index — from FAQ chunks
    rows = conn.execute("""
        SELECT chunk_id, page_id, text
        FROM chunks WHERE section_type = 'faq'
    """).fetchall()
    import hashlib
    for r in rows:
        lines = r["text"].split("\n")
        q = lines[0].replace("Q:", "").strip() if lines else ""
        if q:
            qhash = hashlib.md5(q.lower().encode()).hexdigest()
            conn.execute("""
                INSERT OR IGNORE INTO faq_index (question_hash, question, chunk_id, page_id)
                VALUES (?, ?, ?, ?)
            """, (qhash, q, r["chunk_id"], r["page_id"]))

    conn.commit()
    conn.close()
    logger.info("Secondary indexes built ✓")


def urlparse_netloc(url: str) -> str:
    from urllib.parse import urlparse
    return urlparse(url).netloc


def run_ingest():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    )
    initialize_db()
    ingest_pages()
    ingest_chunks()
    build_secondary_indexes()
    print(f"\n✅ Database ready at {DB_PATH}")


if __name__ == "__main__":
    run_ingest()
