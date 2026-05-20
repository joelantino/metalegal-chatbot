"""
MetaLegal Chatbot — Configuration & Settings
Loads from .env file via python-dotenv
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ── Root paths ────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"
PAGES_JSON_DIR = Path(os.getenv("PAGES_JSON_DIR", DATA_DIR / "pages"))
CHUNKS_JSON_DIR = Path(os.getenv("CHUNKS_JSON_DIR", DATA_DIR / "chunks"))
INDEXES_DIR = Path(os.getenv("INDEXES_DIR", DATA_DIR / "indexes"))
DB_PATH = Path(os.getenv("DB_PATH", DATA_DIR / "metalegal.db"))

# Create all dirs
for d in [DATA_DIR, PAGES_JSON_DIR, CHUNKS_JSON_DIR, INDEXES_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# ── Gemini ────────────────────────────────────────────────────────────────────
GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")

# ── Crawler ───────────────────────────────────────────────────────────────────
TARGET_URL: str = os.getenv("TARGET_URL", "https://metalegal.in")
MAX_PAGES: int = int(os.getenv("MAX_PAGES", "500"))
CRAWL_DELAY: float = float(os.getenv("CRAWL_DELAY", "1.5"))
CRAWL_TIMEOUT: int = int(os.getenv("CRAWL_TIMEOUT", "30"))
MAX_DEPTH: int = int(os.getenv("MAX_DEPTH", "8"))
RESPECT_ROBOTS: bool = os.getenv("RESPECT_ROBOTS", "true").lower() == "true"

# ── Retrieval ─────────────────────────────────────────────────────────────────
TOP_K_CHUNKS: int = int(os.getenv("TOP_K_CHUNKS", "5"))
BM25_BOOST_TITLE: float = float(os.getenv("BM25_BOOST_TITLE", "2.5"))
BM25_BOOST_FAQ: float = float(os.getenv("BM25_BOOST_FAQ", "2.0"))
BM25_BOOST_HEADING: float = float(os.getenv("BM25_BOOST_HEADING", "1.5"))
CONFIDENCE_THRESHOLD: float = float(os.getenv("CONFIDENCE_THRESHOLD", "0.25"))
MIN_CHUNK_WORDS: int = int(os.getenv("MIN_CHUNK_WORDS", "50"))
MAX_CHUNK_WORDS: int = int(os.getenv("MAX_CHUNK_WORDS", "700"))

# ── API ───────────────────────────────────────────────────────────────────────
API_HOST: str = os.getenv("API_HOST", "0.0.0.0")
API_PORT: int = int(os.getenv("API_PORT", "8000"))
API_RELOAD: bool = os.getenv("API_RELOAD", "false").lower() == "true"

# ── Contact fallback ──────────────────────────────────────────────────────────
CONTACT_EMAIL: str = os.getenv("CONTACT_EMAIL", "contact@metalegal.in")
CONTACT_PHONE: str = os.getenv("CONTACT_PHONE", "+91-11-46019520 (Delhi) / 022-47784600 (Mumbai)")
CONTACT_URL: str = os.getenv("CONTACT_URL", "https://www.metalegal.in/contact")
CONTACT_FALLBACK_MSG = (
    f"I couldn't find reliable information for this specific query. "
    f"Please contact MetaLegal directly for accurate legal assistance:\n\n"
    f"- 📧 **Email**: {CONTACT_EMAIL}\n"
    f"- 📞 **Phone**: {CONTACT_PHONE}\n"
    f"- 🌐 **Contact Page**: {CONTACT_URL}"
)

# ── Intent taxonomy ───────────────────────────────────────────────────────────
INTENT_MAP: dict[str, list[str]] = {
    "gst": ["gst", "goods and services tax", "gstin", "gst registration", "gstr"],
    "tax": ["tax", "income tax", "itr", "tds", "advance tax", "tax filing"],
    "trademark": ["trademark", "brand", "logo", "ip", "intellectual property", "copyright"],
    "startup": ["startup", "new company", "entrepreneur", "founder", "incubation"],
    "company_incorporation": ["company", "private limited", "llp", "opc", "incorporation", "mca"],
    "compliance": ["compliance", "annual filing", "roc", "statutory", "audit"],
    "licensing": ["license", "fssai", "shop act", "msme", "udyam", "iec", "import export"],
    "advisory": ["advice", "legal advice", "consult", "consultation", "guidance"],
    "litigation": ["litigation", "court", "dispute", "case", "legal action", "arbitration"],
    "contact": ["contact", "reach", "support", "help", "phone", "email", "address"],
}
