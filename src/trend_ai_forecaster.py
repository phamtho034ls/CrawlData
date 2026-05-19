"""Free keyword discovery (pytrends + Google Suggest) + LLM refine + yt-dlp scrape."""

from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Any

import yt_dlp
from dotenv import load_dotenv
from yt_dlp.utils import DownloadError

load_dotenv()
logger = logging.getLogger(__name__)

_PLACEHOLDER_RE = re.compile(
    r"your[_-]?(openai|api|key|secret|token|google)|changeme|placeholder|xxx+",
    re.IGNORECASE,
)
_SHORTS_PER_KEYWORD = 3
RAW_KEYWORD_TARGET = 20
REFINED_KEYWORD_COUNT = 10
_TRENDS_GEO = "VN"
_TRENDS_HL = "vi-VN"
_TRENDS_TZ = 420
_TRENDS_TIMEFRAME = "now 7-d"
_SUGGEST_URL = "http://suggestqueries.google.com/complete/search"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
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


def _dedupe_keywords(items: list[str], *, limit: int | None = None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in items:
        kw = raw.strip()
        if not kw or len(kw) < 2:
            continue
        key = kw.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(kw)
        if limit is not None and len(out) >= limit:
            break
    return out


def _http_get_text(url: str, *, timeout: float = 15.0) -> str:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "*/*"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def get_google_trends(topic_keyword: str) -> list[str]:
    """
    Fetch rising/top related queries for `topic_keyword` in Vietnam (last 7 days)
    via pytrends only (no generic VN trending feed).
    """
    topic_keyword = topic_keyword.strip()
    if not topic_keyword:
        return []
    return _pytrends_rising_queries(topic_keyword)


def _topic_tokens(topic: str) -> list[str]:
    stop = {
        "là",
        "gì",
        "the",
        "a",
        "an",
        "and",
        "or",
        "for",
        "to",
        "of",
        "in",
        "on",
    }
    tokens: list[str] = []
    for part in re.split(r"[\s\-_/]+", topic.lower()):
        part = part.strip()
        if len(part) >= 2 and part not in stop:
            tokens.append(part)
    return tokens or [topic.lower().strip()]


def is_topic_relevant(keyword: str, topic: str) -> bool:
    """Heuristic: keyword must relate to the seed topic (drops viral VN noise)."""
    kw = keyword.lower().strip()
    topic_lower = topic.lower().strip()
    if not kw or not topic_lower:
        return False
    if topic_lower in kw or kw in topic_lower:
        return True
    tokens = _topic_tokens(topic)
    if not tokens:
        return True
    hits = sum(1 for token in tokens if token in kw)
    required = max(1, (len(tokens) + 1) // 2)
    return hits >= required


def filter_topic_relevant(keywords: list[str], topic: str) -> list[str]:
    return [k for k in keywords if is_topic_relevant(k, topic)]


def _pytrends_rising_queries(topic_keyword: str) -> list[str]:
    try:
        from pytrends.request import TrendReq
    except ImportError as exc:
        raise ImportError("Install pytrends: pip install pytrends") from exc

    pytrends = TrendReq(hl=_TRENDS_HL, tz=_TRENDS_TZ, retries=2, backoff_factor=0.4)
    try:
        pytrends.build_payload(
            kw_list=[topic_keyword],
            timeframe=_TRENDS_TIMEFRAME,
            geo=_TRENDS_GEO,
        )
        time.sleep(0.5)
        related = pytrends.related_queries()
    except Exception as exc:
        logger.warning("pytrends related_queries failed for '%s': %s", topic_keyword, exc)
        return []

    if not related or topic_keyword not in related:
        return []

    block = related[topic_keyword]
    if not isinstance(block, dict):
        return []

    rising_df = block.get("rising")
    top_df = block.get("top")
    merged: list[str] = []
    if rising_df is not None and not rising_df.empty and "query" in rising_df.columns:
        merged.extend(rising_df["query"].astype(str).tolist())
    if top_df is not None and not top_df.empty and "query" in top_df.columns:
        merged.extend(top_df["query"].astype(str).tolist())
    if merged:
        return _dedupe_keywords(merged, limit=RAW_KEYWORD_TARGET + 5)

    return []


def get_google_autocomplete_expansion(seed_keyword: str) -> list[str]:
    """
    Fetch Google search suggestions from the public Suggest API (no API key).
    """
    seed_keyword = seed_keyword.strip()
    if not seed_keyword:
        return []

    year = datetime.now().year
    queries = [
        seed_keyword,
        f"{seed_keyword} ",
        f"{seed_keyword} viral",
        f"{seed_keyword} trending",
        f"{seed_keyword} tiktok",
        f"{seed_keyword} {year}",
    ]
    suggestions: list[str] = []

    for query in queries:
        params = urllib.parse.urlencode(
            {
                "client": "firefox",
                "q": query,
                "hl": "vi",
                "gl": "vn",
            }
        )
        url = f"{_SUGGEST_URL}?{params}"
        try:
            raw = _http_get_text(url)
            if raw.startswith(")]}'"):
                raw = raw.split("\n", 1)[-1]
            data = json.loads(raw)
            if isinstance(data, list) and len(data) >= 2 and isinstance(data[1], list):
                for item in data[1]:
                    if isinstance(item, str) and item.strip():
                        suggestions.append(item.strip())
        except (urllib.error.URLError, json.JSONDecodeError, ValueError) as exc:
            logger.debug("Suggest API failed for %r: %s", query, exc)

    return _dedupe_keywords(suggestions, limit=RAW_KEYWORD_TARGET + 5)


def _pad_raw_keywords(keywords: list[str], seed_topic: str, target: int) -> list[str]:
    """Fill up to `target` related phrases when free sources return too few."""
    year = datetime.now().year
    templates = [
        "{t}",
        "{t} viral",
        "{t} trending",
        "{t} tiktok",
        "{t} youtube shorts",
        "{t} hôm nay",
        "{t} mới nhất",
        "{t} hot {y}",
        "review {t}",
        "{t} là gì",
        "cách dùng {t}",
        "{t} news",
        "{t} update {y}",
        "best {t} {y}",
        "{t} giá",
        "{t} so sánh",
        "{t} tutorial",
        "{t} tips",
        "{t} breakdown",
        "{t} drama",
    ]
    out = list(keywords)
    seen = {k.lower() for k in out}
    for tpl in templates:
        kw = tpl.format(t=seed_topic, y=year).strip()
        key = kw.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(kw)
        if len(out) >= target:
            break
    return out[:target]


def collect_raw_keywords(
    seed_topic: str,
    *,
    target: int = RAW_KEYWORD_TARGET,
) -> list[str]:
    """Merge pytrends + Google Suggest; normalize to ~`target` related keywords."""
    seed_topic = seed_topic.strip()
    if not seed_topic:
        raise ValueError("seed_topic must be non-empty")

    rising = filter_topic_relevant(get_google_trends(seed_topic), seed_topic)
    suggest = filter_topic_relevant(
        get_google_autocomplete_expansion(seed_topic), seed_topic
    )
    combined = _dedupe_keywords([seed_topic, *rising, *suggest])
    combined = filter_topic_relevant(combined, seed_topic)
    if len(combined) < target:
        combined = _pad_raw_keywords(combined, seed_topic, target)
    combined = combined[:target]
    logger.info(
        "Raw keywords for '%s': %d (rising=%d, suggest=%d, target=%d)",
        seed_topic,
        len(combined),
        len(rising),
        len(suggest),
        target,
    )
    return combined


def _invoke_llm_json(prompt: str) -> list[str]:
    """Call OpenAI or Gemini and parse a strict JSON string list."""
    text: str | None = None

    api_key = _valid_openai_key()
    if api_key:
        try:
            from langchain_openai import ChatOpenAI

            llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.3, api_key=api_key)
            response = llm.invoke(prompt)
            text = response.content if hasattr(response, "content") else str(response)
        except Exception as exc:
            logger.warning("OpenAI keyword filter failed: %s", exc)

    if text is None:
        gemini_key = _valid_gemini_key()
        if gemini_key:
            try:
                from langchain_google_genai import ChatGoogleGenerativeAI

                llm = ChatGoogleGenerativeAI(
                    model="gemini-1.5-flash",
                    temperature=0.3,
                    google_api_key=gemini_key,
                )
                response = llm.invoke(prompt)
                text = response.content if hasattr(response, "content") else str(response)
            except Exception as exc:
                logger.warning("Gemini keyword filter failed: %s", exc)

    if not text:
        raise RuntimeError(
            "No LLM available. Set OPENAI_API_KEY or GOOGLE_API_KEY/GEMINI_API_KEY in .env"
        )

    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    data = json.loads(text)
    if not isinstance(data, list):
        raise ValueError("LLM response must be a JSON list")
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
    return keywords


def _filter_keywords_with_llm(
    raw_keywords: list[str],
    topic: str,
    *,
    keep: int = REFINED_KEYWORD_COUNT,
) -> list[str]:
    topic = topic.strip()
    relevant_raw = filter_topic_relevant(raw_keywords, topic) or [topic]
    raw_block = json.dumps(relevant_raw[:RAW_KEYWORD_TARGET], ensure_ascii=False)
    topic_tokens = ", ".join(_topic_tokens(topic))

    prompt = (
        "You are a TikTok/YouTube SEO strategist.\n"
        f"Main topic (must stay on-theme): {topic}\n"
        f"Required topic tokens (at least one per keyword): {topic_tokens}\n\n"
        f"From the raw keyword list below, select exactly {keep} search queries that:\n"
        "1. Are directly about the main topic (same niche, product, or intent).\n"
        "2. Would work as high-traffic searches on TikTok and YouTube Shorts.\n"
        "3. Are specific (2–10 words), not single random names or unrelated news.\n\n"
        "REJECT keywords about: sports, weather, celebrities unrelated to the topic, "
        "generic national trending news, travel, or politics unless the topic is that subject.\n\n"
        f"Raw keywords JSON:\n{raw_block}\n\n"
        f"Return ONLY a strict JSON array of exactly {keep} strings. "
        "No markdown, no commentary."
    )
    picked = _invoke_llm_json(prompt)[:keep]
    return filter_topic_relevant(picked, topic)[:keep]


def _heuristic_top_keywords(
    raw_keywords: list[str],
    topic: str,
    *,
    limit: int = REFINED_KEYWORD_COUNT,
) -> list[str]:
    """Fallback when LLM is unavailable."""
    relevant = filter_topic_relevant(raw_keywords, topic)
    topic_lower = topic.lower()
    ranked = sorted(
        relevant,
        key=lambda k: (
            topic_lower in k.lower(),
            sum(1 for t in _topic_tokens(topic) if t in k.lower()),
            len(k),
        ),
        reverse=True,
    )
    if topic not in ranked:
        ranked.insert(0, topic)
    return _dedupe_keywords(ranked, limit=limit)


def refine_keywords_for_pipeline(
    seed_topic: str,
    raw_keywords: list[str] | None = None,
) -> list[str]:
    """LLM-select top N keywords from raw list for the multi-platform pipeline."""
    seed_topic = seed_topic.strip()
    if not seed_topic:
        raise ValueError("seed_topic must be non-empty")

    raw = raw_keywords or collect_raw_keywords(seed_topic)
    if not raw:
        raw = [seed_topic]

    try:
        keywords = _filter_keywords_with_llm(raw, seed_topic, keep=REFINED_KEYWORD_COUNT)
    except Exception as exc:
        logger.warning("LLM filter failed (%s); using heuristic.", exc)
        keywords = _heuristic_top_keywords(raw, seed_topic)

    if len(keywords) < REFINED_KEYWORD_COUNT:
        keywords = _dedupe_keywords(
            keywords + _heuristic_top_keywords(raw, seed_topic),
            limit=REFINED_KEYWORD_COUNT,
        )
    return keywords[:REFINED_KEYWORD_COUNT]


def discover_keywords_for_pipeline(
    seed_topic: str,
) -> tuple[list[str], list[str]]:
    """
    Collect ~20 raw keywords, refine to top 10 for the classic pipeline.
    Returns (raw_keywords, pipeline_keywords).
    """
    raw = collect_raw_keywords(seed_topic)
    refined = refine_keywords_for_pipeline(seed_topic, raw)
    return raw, refined


def generate_social_keywords(seed_topic: str, trend_list: list[str] | None = None) -> list[str]:
    """
    Discover keywords via pytrends + Google Suggest, refine with LLM to top 10.
    `trend_list` is ignored (kept for backward compatibility).
    """
    _ = trend_list
    _, refined = discover_keywords_for_pipeline(seed_topic)
    return refined


def discover_and_scrape_shorts(
    seed_topic: str,
    *,
    keyword_list: list[str] | None = None,
) -> tuple[list[str], list[str], dict[str, list[dict[str, Any]]]]:
    """
    End-to-end: discover → LLM top 5 → yt-dlp YouTube Shorts per keyword.
    Returns (raw_keywords, refined_keywords, scrape_results).
    """
    raw = collect_raw_keywords(seed_topic)
    refined = keyword_list or generate_social_keywords(seed_topic)
    scraped = scrape_by_ai_keywords(refined)
    return raw, refined, scraped


# --- Backward-compatible aliases (legacy dashboard imports) ---


def get_google_trends_with_source(topic_keyword: str = "") -> tuple[list[str], str]:
    """Legacy helper: returns rising queries and source label."""
    topic = topic_keyword.strip() or "trending"
    trends = get_google_trends(topic)
    source = "pytrends" if trends else "none"
    return trends, source


def _base_ydl_opts(**overrides: Any) -> dict[str, Any]:
    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "ignoreerrors": True,
    }
    opts.update(overrides)
    return opts


def _parse_view_count(raw: object) -> int | None:
    if raw is None:
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str):
        digits = re.sub(r"[^\d]", "", raw)
        return int(digits) if digits else None
    return None


def _enrich_entry(entry: dict[str, Any], ydl: yt_dlp.YoutubeDL) -> dict[str, Any] | None:
    vid = entry.get("id")
    if not vid:
        return None
    url = entry.get("webpage_url") or f"https://www.youtube.com/watch?v={vid}"
    try:
        return ydl.extract_info(url, download=False)
    except (DownloadError, Exception) as exc:
        logger.debug("Enrich skip %s: %s", vid, exc)
        return None


def _entry_to_record(entry: dict[str, Any], keyword: str) -> dict[str, Any]:
    video_id = str(entry.get("id") or "")
    duration = entry.get("duration")
    if isinstance(duration, (int, float)):
        duration = int(duration)
    else:
        duration = None
    return {
        "video_id": video_id,
        "title": entry.get("title") or "Untitled",
        "url": entry.get("webpage_url")
        or entry.get("url")
        or f"https://www.youtube.com/watch?v={video_id}",
        "view_count": _parse_view_count(entry.get("view_count")),
        "platform": "youtube",
        "video_format": "short",
        "upload_date": str(entry.get("upload_date") or ""),
        "source_keyword": keyword,
        "duration": duration,
    }


def scrape_by_ai_keywords(keyword_list: list[str]) -> dict[str, list[dict[str, Any]]]:
    """
    Search each keyword on YouTube Shorts via yt-dlp.
    Returns top 3 video records (URL + metadata) per keyword.
    """
    if not keyword_list:
        return {}

    result: dict[str, list[dict[str, Any]]] = {}
    ydl_opts = _base_ydl_opts(extract_flat=True)

    with yt_dlp.YoutubeDL(ydl_opts) as ydl_flat:
        with yt_dlp.YoutubeDL(_base_ydl_opts()) as ydl_full:
            for keyword in keyword_list:
                keyword = keyword.strip()
                if not keyword:
                    continue
                query = f"ytsearch15:{keyword} #shorts"
                try:
                    info = ydl_flat.extract_info(query, download=False)
                except Exception as exc:
                    logger.warning("Shorts search failed for '%s': %s", keyword, exc)
                    result[keyword] = []
                    continue

                entries = [e for e in (info.get("entries") or []) if e and e.get("id")]
                videos: list[dict[str, Any]] = []
                seen: set[str] = set()

                for entry in entries:
                    vid = str(entry.get("id") or "")
                    if not vid or vid in seen:
                        continue
                    seen.add(vid)
                    full = _enrich_entry(entry, ydl_full)
                    if not full:
                        continue
                    duration = full.get("duration")
                    if isinstance(duration, (int, float)) and int(duration) > 60:
                        continue
                    videos.append(_entry_to_record(full, keyword))
                    if len(videos) >= _SHORTS_PER_KEYWORD:
                        break

                videos.sort(key=lambda v: v.get("view_count") or 0, reverse=True)
                result[keyword] = videos[:_SHORTS_PER_KEYWORD]

    return result


def flatten_keyword_scrape(scraped: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Merge per-keyword scrape results into a deduplicated video list."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for _kw, items in scraped.items():
        for item in items:
            key = (item.get("url") or item.get("video_id") or "").lower()
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(item)
    out.sort(key=lambda v: v.get("view_count") or 0, reverse=True)
    return out
