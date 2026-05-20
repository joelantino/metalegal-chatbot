"""
MetaLegal FastAPI — Production REST API

Endpoints:
  POST /chat          — Main chat endpoint
  POST /admin/crawl   — Trigger website crawl
  POST /admin/index   — Re-index database
  GET  /health        — Health check
  GET  /stats         — Knowledge base stats

All chat responses are from LOCAL KB only.
Zero internet access during chat.
"""

import asyncio
import logging
import re
import time
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, BackgroundTasks, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from src.config import API_HOST, API_PORT, API_RELOAD
from src.database import get_connection, initialize_db
from src.gemini_engine import get_gemini_engine
from src.indexer import get_index_engine, rebuild_indexes
from src.query_processor import process_query
from src.retrieval import retrieve

logger = logging.getLogger("api")

# ── Pydantic models ───────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000, description="User question")
    session_id: Optional[str] = Field(None, description="Optional session identifier")

class ChatResponse(BaseModel):
    answer: str
    confidence: float
    sources: list[str]
    chunks_used: int
    intents: list[str]
    fallback: bool
    latency_ms: float
    session_id: Optional[str] = None

class HealthResponse(BaseModel):
    status: str
    db_pages: int
    db_chunks: int
    indexes_loaded: bool
    gemini_configured: bool

class StatsResponse(BaseModel):
    total_pages: int
    total_chunks: int
    faq_count: int
    keyword_count: int
    index_intent_count: int
    db_size_mb: float


# ── Startup / Shutdown ────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize DB and indexes on startup."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    )
    logger.info("MetaLegal Chatbot API starting up...")
    
    # Ensure DB schema exists
    initialize_db()
    
    # Pre-load indexes into memory
    engine = get_index_engine()
    if not engine.loaded:
        logger.warning("Indexes not found — run admin/index endpoint to build them")
    
    # Warm up Gemini engine eagerly
    gemini = get_gemini_engine()
    try:
        gemini.warmup()
        logger.info("Gemini Engine eagerly warmed up ✓")
    except Exception as e:
        logger.warning(f"Failed to eagerly warm up Gemini: {e}")
    
    logger.info("API ready ✓")
    yield
    logger.info("API shutting down...")


# ── FastAPI App ───────────────────────────────────────────────────────────────

app = FastAPI(
    title="MetaLegal AI Chatbot API",
    description=(
        "Production-grade offline AI chatbot for metalegal.in. "
        "Answers ONLY from local knowledge base. Zero internet access."
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Restrict in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Chat Endpoint (Primary) ───────────────────────────────────────────────────

@app.post("/chat", response_model=ChatResponse, tags=["Chat"])
async def chat(request: ChatRequest):
    """
    Main chat endpoint.
    
    - Processes user query through NLP pipeline
    - Retrieves relevant chunks from local SQLite FTS5 DB
    - Generates answer via Gemini using ONLY local context
    - Returns structured response with sources and confidence
    """
    start = time.perf_counter()
    query = request.query.strip()
    
    if not query:
        raise HTTPException(status_code=400, detail="Query cannot be empty")

    logger.info(f"Query: {query!r}")

    # ── 0. Fast-path for greetings ────────────────────────────────────────────
    normalized_q = re.sub(r"[^\w\s]", "", query.lower()).strip()
    if normalized_q in ["hi", "hello", "hey", "greetings", "good morning", "good afternoon", "hi there", "hello there", "sup"]:
        elapsed_ms = round((time.perf_counter() - start) * 1000, 1)
        return ChatResponse(
            answer="Hello! 👋 I'm the MetaLegal AI assistant. I can quickly answer your questions about GST, company registration, trademark filing, and other legal topics. How can I help you today?",
            confidence=1.0,
            sources=[],
            chunks_used=0,
            intents=["greeting"],
            fallback=False,
            latency_ms=elapsed_ms,
            session_id=request.session_id,
        )

    # ── 1. Process query ──────────────────────────────────────────────────────
    pq = process_query(query)
    logger.debug(f"Intents: {pq.intents} | Keywords: {pq.keywords[:5]}")

    # ── 2. Retrieve chunks ────────────────────────────────────────────────────
    chunks, confidence = retrieve(pq)

    # ── 3. Generate answer ────────────────────────────────────────────────────
    gemini = get_gemini_engine()
    result = gemini.generate(
        user_query=query,
        chunks=chunks,
        confidence=confidence,
        intents=pq.intents,
    )

    elapsed_ms = round((time.perf_counter() - start) * 1000, 1)
    logger.info(f"Response generated in {elapsed_ms}ms | confidence={confidence:.2f}")

    return ChatResponse(
        answer=result["answer"],
        confidence=result.get("confidence", confidence),
        sources=result.get("sources", []),
        chunks_used=result.get("chunks_used", 0),
        intents=result.get("intents", pq.intents),
        fallback=result.get("fallback", False),
        latency_ms=elapsed_ms,
        session_id=request.session_id,
    )


# ── Health Check ──────────────────────────────────────────────────────────────

@app.get("/health", response_model=HealthResponse, tags=["System"])
async def health():
    """Health check endpoint."""
    conn = get_connection()
    pages = conn.execute("SELECT COUNT(*) FROM pages").fetchone()[0]
    chunks = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    conn.close()

    engine = get_index_engine()
    from src.config import GEMINI_API_KEY

    return HealthResponse(
        status="healthy" if pages > 0 else "empty_kb",
        db_pages=pages,
        db_chunks=chunks,
        indexes_loaded=engine.loaded,
        gemini_configured=bool(GEMINI_API_KEY),
    )


# ── Stats ─────────────────────────────────────────────────────────────────────

@app.get("/stats", response_model=StatsResponse, tags=["System"])
async def stats():
    """Knowledge base statistics."""
    conn = get_connection()
    pages    = conn.execute("SELECT COUNT(*) FROM pages").fetchone()[0]
    chunks   = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    faqs     = conn.execute("SELECT COUNT(*) FROM faq_index").fetchone()[0]
    keywords = conn.execute("SELECT COUNT(DISTINCT keyword) FROM keyword_index").fetchone()[0]
    conn.close()

    from src.config import DB_PATH
    import os
    db_mb = round(os.path.getsize(DB_PATH) / 1024 / 1024, 2) if DB_PATH.exists() else 0

    engine = get_index_engine()

    return StatsResponse(
        total_pages=pages,
        total_chunks=chunks,
        faq_count=faqs,
        keyword_count=keywords,
        index_intent_count=len(engine.intent_index),
        db_size_mb=db_mb,
    )


# ── Admin: Trigger Crawl ──────────────────────────────────────────────────────

@app.post("/admin/crawl", tags=["Admin"])
async def trigger_crawl(background_tasks: BackgroundTasks):
    """
    Trigger a full website crawl in the background.
    This may take several minutes.
    """
    async def _crawl():
        from src.crawler import MetaLegalCrawler
        crawler = MetaLegalCrawler()
        await crawler.crawl()

    background_tasks.add_task(_crawl)
    return {"status": "Crawl started in background", "message": "Monitor server logs for progress"}


# ── Admin: Re-Index ───────────────────────────────────────────────────────────

@app.post("/admin/index", tags=["Admin"])
async def trigger_index(background_tasks: BackgroundTasks):
    """
    Run chunker + DB ingestion + index rebuild.
    Call this after a crawl completes.
    """
    def _index():
        from src.chunker import run_chunker
        from src.database import run_ingest
        run_chunker()
        run_ingest()
        rebuild_indexes()

    background_tasks.add_task(_index)
    return {"status": "Indexing started in background", "message": "Monitor server logs for progress"}


# ── Admin: Search test ────────────────────────────────────────────────────────

@app.get("/admin/search", tags=["Admin"])
async def test_search(q: str, k: int = 5):
    """
    Test the retrieval engine directly.
    Returns raw chunks without Gemini generation.
    """
    pq = process_query(q)
    chunks, confidence = retrieve(pq)
    return {
        "query": q,
        "processed_keywords": pq.keywords,
        "intents": pq.intents,
        "confidence": confidence,
        "chunks": [
            {
                "chunk_id": c.chunk_id,
                "section": c.section,
                "section_type": c.section_type,
                "url": c.url,
                "text_preview": c.text[:200] + "...",
                "score": round(c.final_score, 4),
                "priority": c.priority_score,
                "source": c.source_type,
            }
            for c in chunks[:k]
        ],
    }


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "src.api:app",
        host=API_HOST,
        port=API_PORT,
        reload=API_RELOAD,
        log_level="info",
    )
