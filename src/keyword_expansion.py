"""Expand a topic into related search keywords (LLM + fallback)."""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime

from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

_PLACEHOLDER_RE = re.compile(
    r"your[_-]?(openai|api|key|secret|token|tavily)|changeme|placeholder|xxx+",
    re.IGNORECASE,
)


def _valid_openai_key() -> str | None:
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key or len(key) < 20 or _PLACEHOLDER_RE.search(key):
        return None
    return key


def _fallback_keywords(topic: str, count: int) -> list[str]:
    base = topic.strip()
    year = datetime.now().year
    templates = [
        "{t} trending now",
        "{t} viral",
        "{t} hot {year}",
        "{t} trending this week",
        "{t} shorts viral",
        "{t} news today",
        "{t}",
        "latest {t}",
        "{t} breakout",
        "{t} tiktok trend",
        "{t} youtube shorts trend",
        "best {t} {year}",
        "{t} update",
    ]
    out: list[str] = []
    seen: set[str] = set()
    for tpl in templates:
        kw = tpl.format(t=base, year=year).strip()
        key = kw.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(kw)
        if len(out) >= count:
            break
    return out[:count]


def _build_hot_keywords_prompt(topic: str, count: int) -> str:
    now = datetime.now()
    date_iso = now.strftime("%Y-%m-%d")
    month_year = now.strftime("%B %Y")

    return (
        f"You are a real-time trend researcher for YouTube Shorts and TikTok.\n"
        f"Current date and time context: {date_iso} ({month_year}). Use this as 'now'.\n\n"
        f"Main topic: {topic}\n\n"
        f"Generate exactly {count} search keywords that are HOT and trending at this "
        f"exact moment — what viewers are searching and watching today/this week, not "
        f"generic or outdated phrases from past years.\n\n"
        "Requirements:\n"
        "- Prioritize viral, trending, breaking, 'right now', and rising sub-topics.\n"
        "- Each line is a short search query people type on YouTube/TikTok (about 2–8 words).\n"
        "- Cover different angles: viral clips, news, tools/products, drama/controversy, "
        "tutorials tied to current hype.\n"
        f"- Reflect {month_year} / current week where it helps discoverability.\n"
        "- Avoid stale SEO from 2023 or earlier unless still actively trending today.\n"
        "- No duplicate intent; diverse sub-niches under the same topic.\n"
        "- Item 1: strongest hot search phrase for the main topic today.\n\n"
        "Return ONLY a JSON array of strings. No markdown, no commentary."
    )


def expand_keywords(topic: str, count: int = 10) -> list[str]:
    """
    Return `count` distinct search phrases including the original topic.
    Uses OpenAI when configured; otherwise heuristic templates.
    """
    topic = topic.strip()
    if not topic:
        raise ValueError("topic must be non-empty")

    count = max(1, count)
    api_key = _valid_openai_key()
    if not api_key:
        logger.info("OPENAI_API_KEY missing — using template keyword expansion.")
        return _fallback_keywords(topic, count)

    try:
        from langchain_openai import ChatOpenAI

        llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.5, api_key=api_key)
        prompt = _build_hot_keywords_prompt(topic, count)
        logger.info("LLM keyword expansion for '%s' (as of %s)", topic, datetime.now().strftime("%Y-%m-%d"))
        response = llm.invoke(prompt)
        text = response.content if hasattr(response, "content") else str(response)
        text = text.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        data = json.loads(text)
        if not isinstance(data, list):
            raise ValueError("LLM response is not a JSON list")
        keywords: list[str] = []
        seen: set[str] = set()
        for item in data:
            if not isinstance(item, str):
                continue
            kw = item.strip()
            if not kw:
                continue
            key = kw.lower()
            if key in seen:
                continue
            seen.add(key)
            keywords.append(kw)
        if topic.lower() not in seen:
            keywords.insert(0, topic)
        if len(keywords) >= count:
            return keywords[:count]
        for extra in _fallback_keywords(topic, count):
            if extra.lower() not in seen:
                keywords.append(extra)
                seen.add(extra.lower())
            if len(keywords) >= count:
                break
        return keywords[:count]
    except Exception as exc:
        logger.warning("LLM keyword expansion failed (%s); using fallback.", exc)
        return _fallback_keywords(topic, count)
