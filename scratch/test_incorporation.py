import sys
import os
import dotenv
dotenv.load_dotenv()

sys.path.append(os.path.abspath("."))

from src.query_processor import process_query
from src.retrieval import retrieve
from src.gemini_engine import get_gemini_engine

q = "I want to start a new private limited company in India for my tech startup. What is the process and what documents do I need to prepare?"
pq = process_query(q)
chunks, conf = retrieve(pq)
print("Confidence:", conf)
print("Chunks retrieved:", len(chunks))

import google.generativeai as genai
from src.config import GEMINI_API_KEY, GEMINI_MODEL
from src.gemini_engine import SYSTEM_PROMPT, build_prompt

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel(
    model_name=GEMINI_MODEL,
    system_instruction=SYSTEM_PROMPT,
    generation_config={
        "temperature": 0.1,
        "top_p": 0.85,
        "top_k": 20,
        "max_output_tokens": 2048,
    },
    safety_settings=[
        {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
    ],
)

prompt = build_prompt(q, chunks, pq.intents)
print("Sending prompt to Gemini...")
response = model.generate_content(prompt)

print("\n--- Response candidates ---")
for idx, candidate in enumerate(response.candidates):
    print(f"Candidate #{idx}:")
    print(f"  Finish Reason: {candidate.finish_reason}")
    print(f"  Safety Ratings:")
    for rating in candidate.safety_ratings:
        print(f"    {rating.category}: {rating.probability}")
    print("  Content Parts:")
    for part in candidate.content.parts:
        print(part.text)
