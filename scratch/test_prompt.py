import sys
import os
import dotenv
dotenv.load_dotenv()

sys.path.append(os.path.abspath("."))

from src.query_processor import process_query
from src.retrieval import retrieve

q = "What did the Bombay High Court rule regarding bank Look Out Circulars (LOCs) and coercive recovery tactics?"
pq = process_query(q)
chunks, conf = retrieve(pq)

import google.generativeai as genai
from src.config import GEMINI_API_KEY, GEMINI_MODEL
from src.gemini_engine import SYSTEM_PROMPT, build_prompt

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel(
    model_name=GEMINI_MODEL,
    system_instruction=SYSTEM_PROMPT
)

prompt = build_prompt(q, chunks, pq.intents)
print(f"Prompt length in chars: {len(prompt)}")
print(f"Prompt token count: {model.count_tokens(prompt)}")

# Let's test with no max_output_tokens or high max_output_tokens
configs = [
    {},
    {"max_output_tokens": 2048},
    {"max_output_tokens": 8192},
]

for idx, config in enumerate(configs):
    print(f"\n--- Testing with Generation Config: {config} ---")
    try:
        response = model.generate_content(
            prompt,
            generation_config=config
        )
        print(f"Status: SUCCESS")
        print(f"Finish Reason: {response.candidates[0].finish_reason}")
        print(f"Response Text:\n{response.text}")
    except Exception as e:
        print(f"Status: ERROR: {e}")
