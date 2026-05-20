# MetaLegal Vectorless RAG Chatbot
## Production Setup Guide

---

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt
playwright install chromium

# 2. Copy and configure .env
copy .env.example .env
# Edit .env — set GEMINI_API_KEY

# 3. Run full pipeline
python main.py pipeline

# 4. Start API
python main.py serve

# 5. Open chat UI
# Open widget/index.html in browser
```

---

## Project Structure

```
metalegal-chatbot/
├── main.py                  ← CLI entry point (all commands)
├── requirements.txt
├── .env.example
│
├── src/
│   ├── config.py            ← All settings from .env
│   ├── crawler.py           ← Playwright + BS4 + Trafilatura crawler
│   ├── chunker.py           ← Semantic chunking engine
│   ├── database.py          ← SQLite FTS5 schema + ingestor
│   ├── indexer.py           ← In-memory index engine
│   ├── query_processor.py   ← NLP pipeline (intent, keywords)
│   ├── retrieval.py         ← BM25 + FTS5 + re-ranking
│   ├── gemini_engine.py     ← Gemini context builder + generator
│   └── api.py               ← FastAPI REST server
│
├── widget/
│   └── index.html           ← Chat UI (open in browser)
│
└── data/                    ← Auto-created
    ├── pages/               ← Raw page JSONs
    ├── chunks/              ← Semantic chunk JSONs
    ├── indexes/             ← Prebuilt index JSONs
    └── metalegal.db         ← SQLite FTS5 database
```

---

## Pipeline Commands

| Command | Description |
|---|---|
| `python main.py crawl` | Deep crawl metalegal.in |
| `python main.py chunk` | Semantic chunk all pages |
| `python main.py ingest` | Build SQLite DB + all indexes |
| `python main.py build` | chunk + ingest (skip crawl) |
| `python main.py pipeline` | Full: crawl + chunk + ingest |
| `python main.py serve` | Start FastAPI on port 8000 |
| `python main.py chat` | Interactive CLI chat |
| `python main.py stats` | Show KB statistics |

---

## API Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/chat` | Main chat endpoint |
| `GET` | `/health` | Health + DB status |
| `GET` | `/stats` | KB statistics |
| `GET` | `/admin/search?q=...` | Test retrieval (no Gemini) |
| `POST` | `/admin/crawl` | Trigger crawl (background) |
| `POST` | `/admin/index` | Re-build indexes (background) |

### Chat Request
```json
POST /chat
{
  "query": "How do I register GST for my startup?",
  "session_id": "optional-session-id"
}
```

### Chat Response
```json
{
  "answer": "MetaLegal provides GST registration support...",
  "confidence": 0.84,
  "sources": ["https://metalegal.in/services/gst-registration"],
  "chunks_used": 5,
  "intents": ["gst", "startup"],
  "fallback": false,
  "latency_ms": 412.3
}
```

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `GEMINI_API_KEY` | — | **Required** — Gemini API key |
| `GEMINI_MODEL` | `gemini-1.5-flash` | Model to use |
| `TARGET_URL` | `https://metalegal.in` | Site to crawl |
| `MAX_PAGES` | `500` | Crawl limit |
| `CRAWL_DELAY` | `1.5` | Seconds between requests |
| `TOP_K_CHUNKS` | `8` | Chunks sent to Gemini |
| `CONFIDENCE_THRESHOLD` | `0.25` | Min confidence (else fallback) |
| `API_PORT` | `8000` | FastAPI port |

---

## Performance Targets

| Metric | Target |
|---|---|
| Page routing | < 5ms |
| FTS5 retrieval | < 100ms |
| Re-ranking | < 20ms |
| Gemini generation | < 1500ms |
| **Total E2E** | **< 2 seconds** |

---

## Embedding in metalegal.in

Add the chat widget to any page via iframe:

```html
<iframe
  src="https://your-deploy-url/widget/index.html"
  style="border:none; width:420px; height:620px; border-radius:16px;"
></iframe>
```

Or configure the widget to point to your API:
```javascript
// In widget/index.html — update API_BASE
const API_BASE = 'https://your-api-domain.com';
```

---

## Re-crawling (Updates)

When metalegal.in content changes:

```bash
python main.py pipeline   # Full re-crawl + re-index
# OR
python main.py crawl      # Just crawl new pages
python main.py build      # Re-chunk + re-ingest
```

Or via API:
```bash
curl -X POST http://localhost:8000/admin/crawl
curl -X POST http://localhost:8000/admin/index
```
