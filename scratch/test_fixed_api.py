import sys
import os
import time
import dotenv
dotenv.load_dotenv()

sys.path.append(os.path.abspath("."))

from src.query_processor import process_query
from src.retrieval import retrieve
from src.gemini_engine import get_gemini_engine

q = "What did the Bombay High Court rule regarding bank Look Out Circulars (LOCs) and coercive recovery tactics?"

# 1. Warm up model with network handshake
print("Eagerly warming up Gemini engine and establishing SSL/HTTP connections...")
start_warmup = time.perf_counter()
gemini = get_gemini_engine()
gemini.warmup()
elapsed_warmup = round((time.perf_counter() - start_warmup) * 1000, 1)
print(f"Warm up finished in {elapsed_warmup}ms (this absorbed the network handshake latency!)\n")

# 2. Run query first time
print(f"--- QUERY 1 (First query on pre-warmed connection) ---")
start_query = time.perf_counter()
pq = process_query(q)
chunks, conf = retrieve(pq)
elapsed_retrieve = round((time.perf_counter() - start_query) * 1000, 1)

start_gen = time.perf_counter()
result = gemini.generate(
    user_query=q,
    chunks=chunks,
    confidence=conf,
    intents=pq.intents
)
elapsed_gen = round((time.perf_counter() - start_gen) * 1000, 1)
elapsed_total_1 = round((time.perf_counter() - start_query) * 1000, 1)

print(f"Retrieval: {elapsed_retrieve}ms")
print(f"Generation: {elapsed_gen}ms")
print(f"Total Query 1 Latency: {elapsed_total_1}ms\n")

# 3. Run query second time (fully hot connection)
print(f"--- QUERY 2 (Subsequent query on hot connection) ---")
start_query = time.perf_counter()
pq = process_query(q)
chunks, conf = retrieve(pq)
elapsed_retrieve = round((time.perf_counter() - start_query) * 1000, 1)

start_gen = time.perf_counter()
result = gemini.generate(
    user_query=q,
    chunks=chunks,
    confidence=conf,
    intents=pq.intents
)
elapsed_gen = round((time.perf_counter() - start_gen) * 1000, 1)
elapsed_total_2 = round((time.perf_counter() - start_query) * 1000, 1)

print(f"Retrieval: {elapsed_retrieve}ms")
print(f"Generation: {elapsed_gen}ms")
print(f"Total Query 2 Latency: {elapsed_total_2}ms")

print("\n--- Generated Answer ---")
print(result["answer"])
