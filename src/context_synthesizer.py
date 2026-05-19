"""Synthesize trend context summaries from scraped articles via LLM."""

from __future__ import annotations

import logging
import os
import re

from dotenv import load_dotenv
from langchain_core.prompts import ChatPromptTemplate

load_dotenv()
logger = logging.getLogger(__name__)

_MAX_CONTEXT_CHARS = 10_000

_PLACEHOLDER_RE = re.compile(
    r"your[_-]?(openai|api|key|secret|token|google)|changeme|placeholder|xxx+",
    re.IGNORECASE,
)


def _valid_openai_key() -> str | None:
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key or len(key) < 20 or _PLACEHOLDER_RE.search(key):
        return None
    return key


def _valid_gemini_key() -> str | None:
    key = (
        os.getenv("GOOGLE_API_KEY", "").strip()
        or os.getenv("GEMINI_API_KEY", "").strip()
    )
    if not key or len(key) < 20 or _PLACEHOLDER_RE.search(key):
        return None
    return key


def _get_llm():
    api_key = _valid_openai_key()
    if api_key:
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(model="gpt-4o-mini", temperature=0.4, api_key=api_key)

    gemini_key = _valid_gemini_key()
    if gemini_key:
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(
            model="gemini-1.5-flash",
            temperature=0.4,
            google_api_key=gemini_key,
        )

    raise RuntimeError(
        "No LLM configured. Set OPENAI_API_KEY or GOOGLE_API_KEY/GEMINI_API_KEY in .env"
    )


def _combine_article_text(scraped_articles: list[dict]) -> str:
    parts: list[str] = []
    for index, article in enumerate(scraped_articles, start=1):
        title = article.get("title") or article.get("url") or f"Article {index}"
        body = (article.get("main_text") or article.get("body") or "").strip()
        if not body:
            continue
        parts.append(f"### Source {index}: {title}\n{body}")

    combined = "\n\n".join(parts).strip()
    if len(combined) > _MAX_CONTEXT_CHARS:
        combined = combined[:_MAX_CONTEXT_CHARS] + "\n\n[... truncated for token limit ...]"
    return combined


def generate_trend_context(keyword: str, scraped_articles: list[dict]) -> str:
    """
    Generate a 3–4 paragraph trend summary from scraped articles.
    If no articles, asks the LLM for a cautious general overview.
    """
    keyword = keyword.strip()
    if not keyword:
        raise ValueError("keyword must be non-empty")

    combined_text = _combine_article_text(scraped_articles)
    llm = _get_llm()

    if combined_text:
        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are an expert trend analyst. Use only the provided context. "
                    "Do not invent specific facts, statistics, or quotes not supported by the text.",
                ),
                (
                    "human",
                    'You are an expert trend analyst. Based on the following scraped articles '
                    'about the keyword "{keyword}", write a comprehensive summary (3-4 paragraphs) '
                    "explaining what this trend is, why it is popular right now, and key takeaways. "
                    "Do not invent information.\n\n"
                    "Context:\n{combined_text}",
                ),
            ]
        )
        chain = prompt | llm
        response = chain.invoke({"keyword": keyword, "combined_text": combined_text})
    else:
        logger.info("No scraped articles for %r; using LLM general overview.", keyword)
        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "human",
                    'You are an expert trend analyst. No web articles were available for the keyword '
                    '"{keyword}". Write a cautious general overview (3-4 paragraphs) of what this '
                    "topic typically involves and why it might be trending on social video platforms. "
                    "Clearly state that this is general background, not verified news. "
                    "Do not invent specific recent events or statistics.",
                ),
            ]
        )
        chain = prompt | llm
        response = chain.invoke({"keyword": keyword})

    text = response.content if hasattr(response, "content") else str(response)
    return text.strip()
