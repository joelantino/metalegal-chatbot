"""
MetaLegal Query Processor — NLP Pipeline

Query → Clean → Tokens → Intent → Keywords → Routing Signals

Steps:
  1. Clean / normalize query
  2. Remove stopwords
  3. Stem/lemmatize tokens
  4. Detect intent (multi-label)
  5. Extract routing keywords
  6. Score FAQ similarity
"""

import re
import logging
from dataclasses import dataclass, field
from typing import Optional

from src.config import INTENT_MAP

logger = logging.getLogger("query_processor")

# ── Stopwords ─────────────────────────────────────────────────────────────────
STOPWORDS = {
    "a","an","the","is","are","was","were","be","been","being","have","has","had",
    "do","does","did","will","would","should","could","may","might","shall","need",
    "want","get","tell","show","give","find","know","help","please","can","about",
    "at","by","for","with","against","between","into","through","before","after",
    "above","below","to","from","up","down","in","out","on","off","over","under",
    "of","and","or","but","so","if","this","that","these","those","it","its",
    "we","our","your","you","he","she","they","their","i","my","me","us",
    "what","how","when","where","which","who","all","any","some","also","like",
    "just","really","very","quite","much","many","more","most","than","then",
    "hi","hello","hey","thanks","thank","ok","okay","sure","yes","no","not",
}

# ── Synonym expansion ─────────────────────────────────────────────────────────
SYNONYMS: dict[str, str] = {
    "register":     "registration",
    "registering":  "registration",
    "registered":   "registration",
    "incorporate":  "incorporation",
    "incorporating":"incorporation",
    "incorporated": "incorporation",
    "file":         "filing",
    "filing":       "filing",
    "filed":        "filing",
    "form":         "filing",
    "company":      "company",
    "companies":    "company",
    "startup":      "startup",
    "startups":     "startup",
    "tax":          "tax",
    "taxation":     "tax",
    "taxes":        "tax",
    "gst":          "gst",
    "gstin":        "gst",
    "trademark":    "trademark",
    "trademarks":   "trademark",
    "brand":        "trademark",
    "brands":       "trademark",
    "license":      "license",
    "licensing":    "license",
    "licenses":     "license",
    "licensed":     "license",
    "comply":       "compliance",
    "complying":    "compliance",
    "compliant":    "compliance",
    "audit":        "audit",
    "audits":       "audit",
    "litigat":      "litigation",
    "dispute":      "dispute",
    "disputes":     "dispute",
    "court":        "court",
    "arbitration":  "arbitration",
    "document":     "documents",
    "docs":         "documents",
    "cost":         "fees",
    "price":        "fees",
    "charges":      "fees",
    "time":         "timeline",
    "days":         "timeline",
    "duration":     "timeline",
}


def normalize_token(token: str) -> str:
    """Apply synonym mapping and basic normalization."""
    t = token.lower().strip()
    return SYNONYMS.get(t, t)


def clean_query(query: str) -> str:
    """Remove punctuation, collapse whitespace."""
    q = query.strip()
    q = re.sub(r"[^\w\s]", " ", q)
    q = re.sub(r"\s+", " ", q)
    return q.strip()


def tokenize(text: str) -> list[str]:
    """Lowercase word tokenization."""
    return re.findall(r"[a-z]{2,}", text.lower())


def remove_stopwords(tokens: list[str]) -> list[str]:
    return [t for t in tokens if t not in STOPWORDS]


def expand_synonyms(tokens: list[str]) -> list[str]:
    return [normalize_token(t) for t in tokens]


def detect_intents(tokens: list[str], raw_query: str) -> list[str]:
    """
    Multi-label intent detection.
    Returns list of matched intent labels.
    """
    query_lower = raw_query.lower()
    matched = []
    for intent, keywords in INTENT_MAP.items():
        for kw in keywords:
            # Match only on complete words using word boundaries or exact tokens to prevent false substrings (like "roc" in "procedure")
            pattern = r'\b' + re.escape(kw) + r'\b'
            if re.search(pattern, query_lower) or any(kw == t for t in tokens):
                matched.append(intent)
                break
    return matched or ["general"]


def extract_routing_keywords(tokens: list[str], intents: list[str]) -> list[str]:
    """
    Keywords used to route to specific pages.
    Combines normalized tokens + intent keywords.
    """
    routing = list(set(tokens))
    for intent in intents:
        routing.extend(INTENT_MAP.get(intent, [])[:2])
    return list(set(routing))


def score_faq_similarity(query: str, faq_question: str) -> float:
    """
    Simple Jaccard similarity between query tokens and FAQ question tokens.
    Good enough for legal FAQ matching.
    """
    q_tokens = set(remove_stopwords(tokenize(query)))
    faq_tokens = set(remove_stopwords(tokenize(faq_question)))
    if not q_tokens or not faq_tokens:
        return 0.0
    intersection = q_tokens & faq_tokens
    union = q_tokens | faq_tokens
    return len(intersection) / len(union)


@dataclass
class ProcessedQuery:
    raw: str
    clean: str
    tokens: list[str]
    keywords: list[str]             # De-stopped, normalized
    routing_keywords: list[str]     # For page routing
    intents: list[str]              # Detected intents
    is_faq_like: bool               # Starts with how/what/why/when/can
    search_phrase: str              # FTS5 query string
    original_tokens: list[str] = field(default_factory=list)


def process_query(raw_query: str) -> ProcessedQuery:
    """
    Full query processing pipeline.
    Returns a ProcessedQuery with all signals needed for retrieval.
    """
    clean = clean_query(raw_query)
    all_tokens = tokenize(clean)
    original_tokens = list(all_tokens)
    no_stop = remove_stopwords(all_tokens)
    normalized = expand_synonyms(no_stop)
    keywords = list(set(normalized))

    intents = detect_intents(normalized, raw_query)
    routing_kws = extract_routing_keywords(keywords, intents)

    # Is it a question-type query?
    faq_like = bool(re.match(
        r"^\s*(how|what|why|when|where|which|can|do|does|is|are|will|should|who)",
        clean, re.IGNORECASE,
    ))

    # Build FTS5 search phrase (quoted phrase + OR terms)
    fts_terms = " OR ".join(f'"{kw}"' for kw in keywords[:6]) if keywords else clean
    search_phrase = fts_terms

    return ProcessedQuery(
        raw=raw_query,
        clean=clean,
        tokens=normalized,
        keywords=keywords,
        routing_keywords=routing_kws,
        intents=intents,
        is_faq_like=faq_like,
        search_phrase=search_phrase,
        original_tokens=original_tokens,
    )


if __name__ == "__main__":
    # Quick test
    test_queries = [
        "How do I register GST for my startup?",
        "What documents are needed for trademark registration?",
        "Company incorporation process",
        "How much does GST registration cost?",
    ]
    for q in test_queries:
        pq = process_query(q)
        print(f"\nQuery: {q}")
        print(f"  Keywords:  {pq.keywords}")
        print(f"  Intents:   {pq.intents}")
        print(f"  Routing:   {pq.routing_keywords}")
        print(f"  FAQ-like:  {pq.is_faq_like}")
        print(f"  FTS query: {pq.search_phrase}")
