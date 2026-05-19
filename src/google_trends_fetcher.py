"""Fetch Vietnam Google Trends via RSS, SerpAPI, or Playwright (no pytrends)."""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Literal

from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

TrendSource = Literal["rss", "serpapi", "playwright", "none"]

DEFAULT_GEO = "VN"
DEFAULT_HL = "vi"
DEFAULT_LIMIT = 25

# Official-style RSS (works; daily/rss endpoint often returns 404)
GOOGLE_TRENDS_RSS_URL = "https://trends.google.com/trending/rss?geo={geo}"

GOOGLE_TRENDS_PAGE_URL = "https://trends.google.com/trending?geo={geo}&hl={hl}"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

_PLACEHOLDER_RE = re.compile(
    r"your[_-]?(serp|api|key|secret|token)|changeme|placeholder|xxx+",
    re.IGNORECASE,
)


def _valid_serpapi_key() -> str | None:
    key = os.getenv("SERPAPI_API_KEY", "").strip()
    if not key or len(key) < 16 or _PLACEHOLDER_RE.search(key):
        return None
    return key


def _dedupe_trends(items: list[str], *, limit: int = DEFAULT_LIMIT) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in items:
        title = raw.strip()
        if not title:
            continue
        key = title.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(title)
        if len(out) >= limit:
            break
    return out


def _http_get(url: str, *, timeout: float = 20.0) -> bytes:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/rss+xml, application/xml, text/xml, */*",
            "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.read()


def fetch_trends_rss(*, geo: str = DEFAULT_GEO, limit: int = DEFAULT_LIMIT) -> list[str]:
    """Parse Google Trends RSS feed for a region (e.g. VN)."""
    url = GOOGLE_TRENDS_RSS_URL.format(geo=geo.upper())
    try:
        payload = _http_get(url)
    except urllib.error.HTTPError as exc:
        logger.warning("Google Trends RSS HTTP %s for geo=%s", exc.code, geo)
        return []
    except Exception as exc:
        logger.warning("Google Trends RSS failed: %s", exc)
        return []

    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        logger.warning("Google Trends RSS parse error: %s", exc)
        return []

    titles: list[str] = []
    for item in root.findall(".//item"):
        title_el = item.find("title")
        if title_el is not None and title_el.text:
            titles.append(title_el.text.strip())
    return _dedupe_trends(titles, limit=limit)


def fetch_trends_serpapi(
    *,
    geo: str = DEFAULT_GEO,
    hl: str = DEFAULT_HL,
    limit: int = DEFAULT_LIMIT,
) -> list[str]:
    """Use SerpAPI google_trends_trending_now when SERPAPI_API_KEY is set."""
    api_key = _valid_serpapi_key()
    if not api_key:
        return []

    params = urllib.parse.urlencode(
        {
            "engine": "google_trends_trending_now",
            "geo": geo.upper(),
            "hl": hl,
            "api_key": api_key,
        }
    )
    url = f"https://serpapi.com/search.json?{params}"

    try:
        raw = _http_get(url, timeout=30.0)
        data = json.loads(raw.decode("utf-8", errors="replace"))
    except Exception as exc:
        logger.warning("SerpAPI Google Trends failed: %s", exc)
        return []

    titles: list[str] = []
    for block in data.get("trending_searches") or []:
        if isinstance(block, dict):
            query = block.get("query") or block.get("title")
            if query:
                titles.append(str(query).strip())
            for item in block.get("trend_breakdown") or []:
                if isinstance(item, str) and item.strip():
                    titles.append(item.strip())
        elif isinstance(block, str) and block.strip():
            titles.append(block.strip())

    for item in data.get("daily_searches") or []:
        if isinstance(item, dict):
            q = item.get("query") or item.get("title")
            if q:
                titles.append(str(q).strip())

    return _dedupe_trends(titles, limit=limit)


def fetch_trends_playwright(
    *,
    geo: str = DEFAULT_GEO,
    hl: str = DEFAULT_HL,
    limit: int = DEFAULT_LIMIT,
) -> list[str]:
    """Scrape titles from the Google Trends trending page."""
    from playwright.sync_api import sync_playwright

    from src.browser_stealth import goto_like_human, launch_stealth_browser

    url = GOOGLE_TRENDS_PAGE_URL.format(geo=geo.upper(), hl=hl)
    titles: list[str] = []

    try:
        with sync_playwright() as playwright:
            browser, context = launch_stealth_browser(playwright)
            page = context.new_page()
            try:
                goto_like_human(page, url)
                page.wait_for_timeout(2000)

                # Trend cards / list items on the trending UI
                scraped = page.eval_on_selector_all(
                    "[class*='title'], [class*='trend'], a[href*='search'], h2, h3, h4",
                    """els => els
                        .map(e => (e.innerText || e.textContent || '').trim())
                        .filter(t => t && t.length >= 2 && t.length <= 120)""",
                )
                titles.extend(scraped or [])

                if len(titles) < 5:
                    body_text = page.inner_text("body")
                    for line in body_text.splitlines():
                        line = line.strip()
                        if 2 <= len(line) <= 80 and not line.startswith("http"):
                            titles.append(line)
            finally:
                context.close()
                browser.close()
    except Exception as exc:
        logger.warning("Playwright Google Trends scrape failed: %s", exc)
        return []

    # Drop obvious UI chrome
    skip = {
        "google",
        "trends",
        "đăng nhập",
        "sign in",
        "home",
        "help",
        "privacy",
        "terms",
        "settings",
        "trending now",
        "xu hướng",
    }
    filtered = [
        t
        for t in titles
        if t.lower() not in skip and not t.lower().startswith("trending")
    ]
    return _dedupe_trends(filtered, limit=limit)


def fetch_vietnam_trends(
    *,
    geo: str | None = None,
    limit: int = DEFAULT_LIMIT,
) -> tuple[list[str], TrendSource]:
    """
    Try RSS → SerpAPI → Playwright. Returns (trend titles, source used).
  """
    geo = (geo or os.getenv("TRENDS_GEO", DEFAULT_GEO)).upper()
    hl = os.getenv("TRENDS_HL", DEFAULT_HL)

    for name, fetcher, kwargs in (
        ("rss", fetch_trends_rss, {"geo": geo, "limit": limit}),
        ("serpapi", fetch_trends_serpapi, {"geo": geo, "hl": hl, "limit": limit}),
        ("playwright", fetch_trends_playwright, {"geo": geo, "hl": hl, "limit": limit}),
    ):
        try:
            trends = fetcher(**kwargs)
        except Exception as exc:
            logger.warning("Trend fetcher %s raised: %s", name, exc)
            trends = []
        if trends:
            logger.info("Google Trends VN: %d items via %s", len(trends), name)
            return trends, name  # type: ignore[return-value]

    logger.warning("All Google Trends fetchers returned empty for geo=%s", geo)
    return [], "none"
