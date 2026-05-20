"""
MetaLegal Gemini Engine — Context Builder + Answer Generator

Builds a STRICT, focused prompt containing ONLY retrieved chunks.
Gemini must ONLY answer from provided context.
Zero hallucination policy enforced via system prompt.
"""

import logging
from typing import Optional

import google.generativeai as genai

from src.config import (
    GEMINI_API_KEY,
    GEMINI_MODEL,
    CONFIDENCE_THRESHOLD,
    CONTACT_FALLBACK_MSG,
    CONTACT_EMAIL,
    CONTACT_PHONE,
    CONTACT_URL,
)
from src.retrieval import RetrievedChunk

logger = logging.getLogger("gemini")

# ── System prompt ─────────────────────────────────────────────────────────────
SYSTEM_PROMPT = """You are MetaLegal's official AI assistant for metalegal.in.

STRICT RULES — You MUST follow every rule without exception:

1. ONLY answer using the "RETRIEVED CONTEXT" provided below.
2. NEVER use your own training knowledge or external information.
3. NEVER hallucinate, guess, or fabricate any legal facts, fees, timelines, or procedures.
4. NEVER recommend any external website, competitor, or third-party service.
5. If the context does not contain a clear answer, explicitly say so and recommend contacting MetaLegal directly.
6. Provide highly detailed, comprehensive, and complete answers incorporating all relevant facts, sections, rules, and guidance present in the context. Do not over-summarize or clip important details.
7. Mention MetaLegal services exactly as they appear in the context — do not rename or paraphrase services.
8. For legal questions, always remind the user to consult with a MetaLegal professional for personalized advice.
9. Do NOT repeat the user's question back to them.
10. Do NOT start your answer with "Based on the context" or similar phrases.

RESPONSE FORMAT:
- Provide direct, concise, and highly accurate answers. Do not generate overly lengthy responses; prioritize speed and clarity while ensuring all necessary details from the context are covered completely. Use bullet points where appropriate.
- Keep the response strictly grounded in the retrieved context. Do not invent any solution or guess details not present in the retrieved info.
- End with a brief closing line reminding the user to consult a MetaLegal professional.
"""


def build_context_block(chunks: list[RetrievedChunk]) -> str:
    """
    Build a concise, well-structured context string from retrieved chunks.
    Each chunk is labeled with its source page and section.
    """
    if not chunks:
        return "No relevant content found."

    parts = []
    seen_pages: set[str] = set()

    for i, chunk in enumerate(chunks, 1):
        source_label = f"[Source {i}]"
        page_ref = ""
        if chunk.url:
            page_ref = f" | Page: {chunk.url}"
        if chunk.section:
            page_ref += f" | Section: {chunk.section}"

        parts.append(
            f"{source_label}{page_ref}\n"
            f"{chunk.text.strip()}\n"
        )

        seen_pages.add(chunk.page_id)

    return "\n---\n".join(parts)


def build_prompt(
    user_query: str,
    chunks: list[RetrievedChunk],
    intents: list[str],
) -> str:
    """
    Assemble the full prompt sent to Gemini.
    Context is strictly from retrieved chunks only.
    """
    context = build_context_block(chunks)

    # Source attribution section
    unique_urls = list(dict.fromkeys(c.url for c in chunks if c.url))
    sources_block = ""
    if unique_urls:
        source_lines = "\n".join(f"  - {u}" for u in unique_urls[:5])
        sources_block = f"\nSOURCE PAGES:\n{source_lines}\n"

    intent_hint = f"[Detected topic: {', '.join(intents)}]" if intents else ""

    prompt = f"""RETRIEVED CONTEXT:
{context}
{sources_block}
USER QUERY {intent_hint}:
{user_query}

ANSWER (only from context above):"""

    return prompt


def format_fallback_response(confidence: float) -> str:
    """Return a graceful fallback when confidence is too low."""
    return (
        f"I couldn't find highly reliable information for your specific query "
        f"in our knowledge base.\n\n"
        f"For accurate legal assistance, please contact MetaLegal directly:\n\n"
        f"- 📧 **Email**: [{CONTACT_EMAIL}](mailto:{CONTACT_EMAIL})\n"
        f"- 📞 **Phone**: {CONTACT_PHONE}\n"
        f"- 🌐 **Contact Page**: [{CONTACT_URL}]({CONTACT_URL})\n\n"
        f"Our legal experts will provide accurate, personalized guidance."
    )


class GeminiEngine:
    """
    Manages Gemini API client with lazy initialization.
    Handles prompt building + answer generation.
    """

    def __init__(self):
        self._client = None
        self._model = None

    def _initialize(self):
        if not GEMINI_API_KEY:
            raise ValueError(
                "GEMINI_API_KEY not set. Add it to your .env file."
            )
        genai.configure(api_key=GEMINI_API_KEY)
        self._model = genai.GenerativeModel(
            model_name=GEMINI_MODEL,
            system_instruction=SYSTEM_PROMPT,
            generation_config={
                "temperature": 0.1,          # Very low — deterministic answers
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
        logger.info(f"Gemini initialized: {GEMINI_MODEL}")

    def warmup(self):
        """Warm up/initialize the Gemini model eagerly and perform a lightweight network handshake."""
        if self._model is None:
            self._initialize()
        try:
            # Force actual DNS resolution, SSL handshake, and connection pool warming
            self._model.count_tokens(" ")
            logger.info("Gemini HTTP connection pool successfully warmed up ✓")
        except Exception as e:
            logger.warning(f"Failed to perform Gemini network warmup: {e}")

    def generate(
        self,
        user_query: str,
        chunks: list[RetrievedChunk],
        confidence: float,
        intents: list[str],
    ) -> dict:
        """
        Generate an answer. Returns structured response dict.
        """
        # Confidence gate — don't hallucinate if retrieval is poor
        if confidence < CONFIDENCE_THRESHOLD or not chunks:
            logger.info(f"Confidence {confidence:.2f} below threshold — returning fallback")
            return {
                "answer": format_fallback_response(confidence),
                "confidence": confidence,
                "sources": [],
                "chunks_used": 0,
                "fallback": True,
            }

        # Lazy init
        if self._model is None:
            self._initialize()

        prompt = build_prompt(user_query, chunks, intents)

        try:
            response = self._model.generate_content(prompt)
            answer_text = response.text.strip()

            # Append source attribution
            unique_urls = list(dict.fromkeys(c.url for c in chunks if c.url))
            if unique_urls:
                source_lines = "\n".join(f"  - {u}" for u in unique_urls[:3])
                answer_text += f"\n\n---\n*Sources: MetaLegal Knowledge Base*\n{source_lines}"

            return {
                "answer": answer_text,
                "confidence": confidence,
                "sources": unique_urls,
                "chunks_used": len(chunks),
                "fallback": False,
                "intents": intents,
            }

        except Exception as e:
            logger.error(f"Gemini API error: {e}")
            return {
                "answer": format_fallback_response(0.0),
                "confidence": 0.0,
                "sources": [],
                "chunks_used": 0,
                "fallback": True,
                "error": str(e),
            }

    def generate_stream(
        self,
        user_query: str,
        chunks: list[RetrievedChunk],
        confidence: float,
        intents: list[str],
    ):
        """Yields chunks of text as they are generated by Gemini."""
        if confidence < CONFIDENCE_THRESHOLD or not chunks:
            yield format_fallback_response(confidence)
            return

        if self._model is None:
            self._initialize()

        prompt = build_prompt(user_query, chunks, intents)

        try:
            response = self._model.generate_content(prompt, stream=True)
            for chunk in response:
                yield chunk.text

            # Append sources at the end
            unique_urls = list(dict.fromkeys(c.url for c in chunks if c.url))
            if unique_urls:
                source_lines = "\n".join(f"  - {u}" for u in unique_urls[:3])
                yield f"\n\n---\n*Sources: MetaLegal Knowledge Base*\n{source_lines}"

        except Exception as e:
            logger.error(f"Gemini API error during stream: {e}")
            yield f"\n[Error generating response: {e}]"


# ── Singleton ─────────────────────────────────────────────────────────────────
_gemini_engine: Optional[GeminiEngine] = None


def get_gemini_engine() -> GeminiEngine:
    global _gemini_engine
    if _gemini_engine is None:
        _gemini_engine = GeminiEngine()
    return _gemini_engine
