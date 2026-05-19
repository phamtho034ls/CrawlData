"""DuckDuckGo text search for trend-related articles."""

from __future__ import annotations

import logging
from typing import Any

from src.trend_content import is_video_platform_url

logger = logging.getLogger(__name__)


def search_trend_articles(keyword: str, max_results: int = 3) -> list[dict[str, Any]]:
    """
    Search for articles related to `keyword` via DuckDuckGo.

    Returns a list of dicts: title, href, body (snippet).
    Returns [] if the search fails or is blocked.
    """
    keyword = keyword.strip()
    if not keyword:
        return []

    try:
        from duckduckgo_search import DDGS
    except ImportError as exc:
        logger.error("duckduckgo-search not installed: %s", exc)
        return []

    try:
        with DDGS() as ddgs:
            raw = list(ddgs.text(keyword, max_results=max(max_results * 3, max_results)))
    except Exception as exc:
        logger.warning("DuckDuckGo search blocked or failed for %r: %s", keyword, exc)
        return []

    results: list[dict[str, Any]] = []
    for item in raw:
        href = (item.get("href") or item.get("link") or "").strip()
        if not href or is_video_platform_url(href):
            continue
        results.append(
            {
                "title": (item.get("title") or "").strip(),
                "href": href,
                "body": (item.get("body") or item.get("snippet") or "").strip(),
            }
        )
        if len(results) >= max_results:
            break

    return results
