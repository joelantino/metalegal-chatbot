import sys
import time
from src.query_processor import process_query
from src.retrieval import retrieve
from src.gemini_engine import get_gemini_engine
import os
from dotenv import load_dotenv

load_dotenv()

def test_speed():
    gemini = get_gemini_engine()
    gemini.warmup()  # Ensure network pool is hot
    
    query = "What are the rules regarding input tax credit under GST?"
    
    t0 = time.time()
    pq = process_query(query)
    chunks, confidence = retrieve(pq)
    t1 = time.time()
    
    result = gemini.generate(query, chunks, confidence, pq.intents)
    t2 = time.time()
    
    print(f"Retrieval time: {t1 - t0:.3f}s")
    print(f"Generation time: {t2 - t1:.3f}s")
    print(f"Total time: {t2 - t0:.3f}s")
    print(f"Chunks used: {result['chunks_used']}")
    print(f"Confidence: {result['confidence']:.2f}")
    print(f"Answer snippet: {result['answer'][:200].encode('utf-8', 'ignore').decode('utf-8')}...")

if __name__ == "__main__":
    test_speed()
