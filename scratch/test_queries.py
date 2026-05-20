import sys
import os

sys.path.append(os.path.abspath("."))

from src.query_processor import process_query
from src.retrieval import retrieve

q = "Can you recommend a recipe for baking pepperoni pizza at home?"
pq = process_query(q)
chunks, conf = retrieve(pq)
print(f"Query: {q}")
print(f"Confidence: {conf:.2f}")
for idx, c in enumerate(chunks, 1):
    print(f"Chunk #{idx}: Title={c.page_title} | Section={c.section} | Keywords={c.keywords}")
