import sys
import os
import dotenv
dotenv.load_dotenv()

sys.path.append(os.path.abspath("."))

from src.query_processor import process_query
from src.retrieval import retrieve
from src.gemini_engine import get_gemini_engine

q = "What did the Bombay High Court rule regarding bank Look Out Circulars (LOCs) and coercive recovery tactics?"
pq = process_query(q)
chunks, conf = retrieve(pq)
print("Confidence:", conf)
print("Chunks retrieved:", len(chunks))

gemini = get_gemini_engine()
result = gemini.generate(
    user_query=q,
    chunks=chunks,
    confidence=conf,
    intents=pq.intents
)

print("\n--- Answer ---")
print(result["answer"])
print("\n--- Raw Answer Length ---", len(result["answer"]))
