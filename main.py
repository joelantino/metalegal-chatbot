"""
MetaLegal CLI Pipeline — Run full pipeline from command line

Commands:
  python main.py crawl     → Crawl metalegal.in
  python main.py chunk     → Chunk pages
  python main.py ingest    → Build DB + indexes
  python main.py build     → chunk + ingest (combined)
  python main.py pipeline  → Full: crawl + chunk + ingest
  python main.py serve     → Start API server
  python main.py chat      → Interactive CLI chat
  python main.py stats     → Show KB stats
"""

import asyncio
import logging
import sys

# Configure UTF-8 encoding for standard output/error to prevent Unicode errors on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)


def print_banner():
    print("""
=============================================================
           MetaLegal AI Chatbot - Vectorless RAG
        Offline * Deterministic * Legal-Grade AI
=============================================================
""")


def cmd_crawl():
    """Run the website crawler."""
    from src.crawler import run_crawler
    print("🌐 Starting deep website crawl...")
    asyncio.run(run_crawler())


def cmd_chunk():
    """Run the semantic chunker."""
    from src.chunker import run_chunker
    print("✂️  Running semantic chunker...")
    total = run_chunker()
    print(f"✅ {total} chunks created")


def cmd_ingest():
    """Build SQLite DB + all indexes."""
    from src.database import run_ingest
    from src.indexer import rebuild_indexes
    print("🗄️  Ingesting into SQLite FTS5 database...")
    run_ingest()
    print("🔍 Building page indexes...")
    rebuild_indexes()
    print("✅ Database and indexes ready")


def cmd_build():
    """Chunk + Ingest (skip crawl)."""
    cmd_chunk()
    cmd_ingest()


def cmd_pipeline():
    """Full pipeline: Crawl → Chunk → Ingest."""
    cmd_crawl()
    cmd_chunk()
    cmd_ingest()


def cmd_serve():
    """Start the FastAPI API server."""
    import uvicorn
    from src.config import API_HOST, API_PORT, API_RELOAD
    print(f"🚀 Starting API server at http://{API_HOST}:{API_PORT}")
    print(f"📖 Docs: http://localhost:{API_PORT}/docs")
    uvicorn.run(
        "src.api:app",
        host=API_HOST,
        port=API_PORT,
        reload=API_RELOAD,
        log_level="info",
    )


def cmd_chat():
    """Interactive CLI chatbot."""
    from src.query_processor import process_query
    from src.retrieval import retrieve
    from src.gemini_engine import get_gemini_engine

    print_banner()
    print("💬 MetaLegal AI Chat — type 'exit' to quit\n")

    gemini = get_gemini_engine()

    while True:
        try:
            query = input("You: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n👋 Goodbye!")
            break

        if not query:
            continue
        if query.lower() in ("exit", "quit", "bye"):
            print("👋 Goodbye!")
            break

        pq = process_query(query)
        chunks, confidence = retrieve(pq)
        
        print(f"\n🤖 MetaLegal AI:\n", end="", flush=True)
        for text_chunk in gemini.generate_stream(query, chunks, confidence, pq.intents):
            print(text_chunk, end="", flush=True)
            
        print(f"\n\n[Confidence: {confidence:.0%} | Chunks: {len(chunks)} | Intents: {', '.join(pq.intents)}]\n")


def cmd_stats():
    """Show knowledge base stats."""
    from src.database import get_connection
    from src.indexer import get_index_engine
    from src.config import DB_PATH
    import os

    conn = get_connection()
    pages  = conn.execute("SELECT COUNT(*) FROM pages").fetchone()[0]
    chunks = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    faqs   = conn.execute("SELECT COUNT(*) FROM faq_index").fetchone()[0]
    kws    = conn.execute("SELECT COUNT(DISTINCT keyword) FROM keyword_index").fetchone()[0]
    conn.close()

    engine = get_index_engine()
    db_mb = round(os.path.getsize(DB_PATH) / 1024 / 1024, 2) if DB_PATH.exists() else 0

    print_banner()
    print("📊 Knowledge Base Statistics")
    print("─" * 40)
    print(f"  Pages crawled:      {pages:,}")
    print(f"  Semantic chunks:    {chunks:,}")
    print(f"  FAQ entries:        {faqs:,}")
    print(f"  Unique keywords:    {kws:,}")
    print(f"  Intent categories:  {len(engine.intent_index)}")
    print(f"  URL index entries:  {len(engine.url_index):,}")
    print(f"  Database size:      {db_mb} MB")
    print("─" * 40)


COMMANDS = {
    "crawl":    cmd_crawl,
    "chunk":    cmd_chunk,
    "ingest":   cmd_ingest,
    "build":    cmd_build,
    "pipeline": cmd_pipeline,
    "serve":    cmd_serve,
    "chat":     cmd_chat,
    "stats":    cmd_stats,
}

USAGE = """
Usage: python main.py <command>

Commands:
  crawl      Crawl metalegal.in website (deep)
  chunk      Semantically chunk crawled pages
  ingest     Ingest chunks into SQLite FTS5 + build indexes
  build      Run chunk + ingest (skip crawl)
  pipeline   Full pipeline: crawl + chunk + ingest
  serve      Start FastAPI REST API server
  chat       Interactive CLI chatbot
  stats      Show knowledge base statistics
"""

if __name__ == "__main__":
    print_banner()

    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(USAGE)
        sys.exit(1)

    command = sys.argv[1]
    COMMANDS[command]()
# Viewed
