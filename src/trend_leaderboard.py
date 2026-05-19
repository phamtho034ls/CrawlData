"""Native platform leaderboards: YouTube trending feed and TikTok discover."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Literal

import yt_dlp
from yt_dlp.utils import DownloadError

logger = logging.getLogger(__name__)

YouTubeCategory = Literal["now", "music", "gaming"]

YOUTUBE_TRENDING_URLS: dict[YouTubeCategory, str] = {
    "now": "https://www.youtube.com/feed/trending",
    "music": "https://www.youtube.com/feed/trending?bp=4gIhEN0JABoGMgUzA1AA",
    "gaming": "https://www.youtube.com/feed/trending?bp=4gIhEN0JABoGMgU0AkAB",
}

TIKTOK_DISCOVER_URL = "https://www.tiktok.com/explore"

ANTI_BOT_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,vi;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)


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


def _parse_like_count(raw: object) -> int | None:
    return _parse_view_count(raw)


def _entry_to_youtube_record(entry: dict[str, Any], *, category: str) -> dict[str, Any]:
    video_id = str(entry.get("id") or "")
    duration = entry.get("duration")
    if isinstance(duration, (int, float)):
        duration = int(duration)
    else:
        duration = None
    fmt = "short" if duration is not None and duration <= 60 else "long"
    return {
        "video_id": video_id,
        "title": entry.get("title") or "Untitled",
        "url": entry.get("webpage_url")
        or entry.get("url")
        or f"https://www.youtube.com/watch?v={video_id}",
        "view_count": _parse_view_count(entry.get("view_count")),
        "platform": "youtube",
        "video_format": fmt,
        "upload_date": str(entry.get("upload_date") or ""),
        "source_keyword": f"youtube_trending_{category}",
        "duration": duration,
        "leaderboard_category": category,
    }


def scrape_youtube_trending(
    category: YouTubeCategory = "now",
    *,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """
    Extract metadata for top videos from YouTube's trending feed.
    Supports categories: now (general), music, gaming.
    """
    url = YOUTUBE_TRENDING_URLS.get(category, YOUTUBE_TRENDING_URLS["now"])
    flat_opts = _base_ydl_opts(extract_flat=True, playlistend=limit * 2)

    try:
        with yt_dlp.YoutubeDL(flat_opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except DownloadError as exc:
        logger.warning("YouTube trending feed failed (%s): %s", category, exc)
        return []
    except Exception as exc:
        logger.warning("YouTube trending error: %s", exc)
        return []

    if not info:
        return []

    entries = [e for e in (info.get("entries") or []) if e and e.get("id")]
    results: list[dict[str, Any]] = []

    with yt_dlp.YoutubeDL(_base_ydl_opts()) as ydl_full:
        for entry in entries:
            if len(results) >= limit:
                break
            vid = entry.get("id")
            if not vid:
                continue
            watch = entry.get("webpage_url") or f"https://www.youtube.com/watch?v={vid}"
            try:
                full = ydl_full.extract_info(watch, download=False)
            except Exception:
                full = entry
            if full:
                results.append(_entry_to_youtube_record(full, category=category))

    return results[:limit]


def _extract_tiktok_items_from_payload(data: Any) -> list[dict[str, Any]]:
    """Walk JSON API responses and pull video-like objects."""
    found: list[dict[str, Any]] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            stats = node.get("stats") or node.get("statistics") or {}
            video_id = (
                node.get("id")
                or node.get("aweme_id")
                or (node.get("video") or {}).get("id")
            )
            desc = node.get("desc") or node.get("title") or node.get("description")
            author = node.get("author") or node.get("authorInfo") or {}
            unique_id = author.get("uniqueId") or author.get("unique_id")
            if video_id and (desc or unique_id):
                views = (
                    stats.get("playCount")
                    or stats.get("play_count")
                    or node.get("playCount")
                )
                likes = stats.get("diggCount") or stats.get("digg_count")
                url = None
                if unique_id:
                    url = f"https://www.tiktok.com/@{unique_id}/video/{video_id}"
                found.append(
                    {
                        "video_id": str(video_id),
                        "title": str(desc or f"TikTok {video_id}")[:200],
                        "url": url or f"https://www.tiktok.com/video/{video_id}",
                        "view_count": _parse_view_count(views),
                        "like_count": _parse_like_count(likes),
                        "platform": "tiktok",
                        "video_format": "tiktok",
                        "source_keyword": "tiktok_discover",
                    }
                )
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(data)
    return found


async def _scrape_tiktok_discover_async(
    *,
    scroll_seconds: float = 3.0,
    top_n: int = 5,
) -> list[dict[str, Any]]:
    from playwright.async_api import async_playwright

    captured: list[dict[str, Any]] = []

    async def on_response(response) -> None:
        try:
            if "tiktok" not in response.url.lower():
                return
            content_type = (response.headers.get("content-type") or "").lower()
            if "json" not in content_type:
                return
            body = await response.body()
            if not body:
                return
            data = json.loads(body.decode("utf-8", errors="replace"))
            captured.extend(_extract_tiktok_items_from_payload(data))
        except Exception:
            return

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
        )
        context = await browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1366, "height": 768},
            locale="en-US",
            timezone_id="Asia/Ho_Chi_Minh",
            extra_http_headers=ANTI_BOT_HEADERS,
        )
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
        )
        page = await context.new_page()
        page.on("response", on_response)

        try:
            await page.goto(TIKTOK_DISCOVER_URL, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(int(scroll_seconds * 1000))
            await page.mouse.wheel(0, 1200)
            await page.wait_for_timeout(500)
        finally:
            await context.close()
            await browser.close()

    deduped: dict[str, dict[str, Any]] = {}
    for item in captured:
        vid = item.get("video_id") or item.get("url")
        if not vid:
            continue
        key = str(vid)
        existing = deduped.get(key)
        if not existing or (item.get("view_count") or 0) > (existing.get("view_count") or 0):
            deduped[key] = item

    ranked = sorted(
        deduped.values(),
        key=lambda x: x.get("view_count") or 0,
        reverse=True,
    )
    return ranked[:top_n]


def scrape_tiktok_discover(
    *,
    scroll_seconds: float = 3.0,
    top_n: int = 5,
) -> list[dict[str, Any]]:
    """
    Navigate TikTok discover/explore, scroll briefly, intercept API JSON,
    and return top videos by view count.
    """
    try:
        return asyncio.run(
            _scrape_tiktok_discover_async(scroll_seconds=scroll_seconds, top_n=top_n)
        )
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(
                _scrape_tiktok_discover_async(scroll_seconds=scroll_seconds, top_n=top_n)
            )
        finally:
            loop.close()
