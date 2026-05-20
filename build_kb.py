"""
build_kb.py
Reads all crawled JSON files and builds a SQLite FTS5 database for vectorless RAG.
"""

import json
import sqlite3
import logging
from pathlib import Path

DB_PATH = "data/metalegal.db"
PAGES_DIR = Path("data/pages")

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

def init_db(conn):
    cursor = conn.cursor()
    # Main metadata table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS documents (
            url TEXT PRIMARY KEY,
            title TEXT,
            page_type TEXT,
            word_count INTEGER,
            keywords TEXT,
            last_crawled TEXT
        )
    """)
    # FTS5 virtual table for full-text search
    # We store the section key and the text for granular retrieval
    cursor.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS sections_fts USING fts5(
            url UNINDEXED,
            section_key UNINDEXED,
            text
        )
    """)
    conn.commit()

def build_kb():
    if not PAGES_DIR.exists():
        logging.error(f"Directory {PAGES_DIR} not found. Run crawler first.")
        return

    json_files = list(PAGES_DIR.glob("*.json"))
    if not json_files:
        logging.warning("No JSON files found to index.")
        return

    # Delete old DB to rebuild clean
    if Path(DB_PATH).exists():
        Path(DB_PATH).unlink()
    
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    
    conn = sqlite3.connect(DB_PATH)
    init_db(conn)
    cursor = conn.cursor()

    docs_inserted = 0
    sections_inserted = 0

    for file_path in json_files:
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            url = data.get("url")
            if not url:
                continue

            # Insert metadata
            cursor.execute("""
                INSERT OR REPLACE INTO documents (url, title, page_type, word_count, keywords, last_crawled)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                url,
                data.get("title", ""),
                data.get("page_type", ""),
                data.get("word_count", 0),
                ", ".join(data.get("keywords", [])),
                data.get("last_crawled", "")
            ))
            docs_inserted += 1

            # Insert sections into FTS5
            content_sections = data.get("content_sections", {})
            for key, text in content_sections.items():
                if len(text.strip()) < 10:
                    continue  # skip empty or very short noise
                cursor.execute("""
                    INSERT INTO sections_fts (url, section_key, text)
                    VALUES (?, ?, ?)
                """, (url, key, text))
                sections_inserted += 1

        except Exception as e:
            logging.error(f"Failed to process {file_path.name}: {e}")

    conn.commit()
    
    # Optimize FTS index
    cursor.execute("INSERT INTO sections_fts(sections_fts) VALUES('optimize')")
    conn.commit()
    conn.close()

    logging.info("="*50)
    logging.info("KNOWLEDGE BASE BUILD COMPLETE")
    logging.info(f"Database     : {DB_PATH}")
    logging.info(f"Documents    : {docs_inserted}")
    logging.info(f"FTS Sections : {sections_inserted}")
    logging.info("="*50)

if __name__ == "__main__":
    build_kb()
