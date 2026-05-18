"""Trend discovery via YouTube (yt-dlp) and TikTok (Playwright)."""

from __future__ import annotations

import logging
import re
from typing import Literal

import yt_dlp
from playwright.sync_api import sync_playwright
from pydantic import BaseModel, Field, ValidationError

logger = logging.getLogger(__name__)

Platform = Literal["youtube", "tiktok"]


class TrendVideo(BaseModel):
    video_id: str
    title: str
    url: str
    view_count: int | None = None
    platform: Platform


class TrendVideoList(BaseModel):
    videos: list[TrendVideo] = Field(default_factory=list)


def _parse_view_count(raw: object) -> int | None:
    if raw is None:
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str):
        digits = re.sub(r"[^\d]", "", raw)
        return int(digits) if digits else None
    return None


def _fetch_youtube_trends(keyword: str, limit: int) -> list[TrendVideo]:
    search_query = f"ytsearch{limit}:{keyword} shorts"
    ydl_opts: dict = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": False,
        "skip_download": True,
    }

    results: list[TrendVideo] = []
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(search_query, download=False)

    entries = info.get("entries") or [] if info else []
    for entry in entries[:limit]:
        if not entry:
            continue
        video_id = entry.get("id") or ""
        title = entry.get("title") or "Untitled"
        url = entry.get("webpage_url") or entry.get("url") or f"https://www.youtube.com/watch?v={video_id}"
        results.append(
            TrendVideo(
                video_id=video_id,
                title=title,
                url=url,
                view_count=_parse_view_count(entry.get("view_count")),
                platform="youtube",
            )
        )
    return results


def _fetch_tiktok_trends(keyword: str, limit: int = 3) -> list[TrendVideo]:
    """Navigate TikTok search and collect the first video links (mock-style scrape)."""
    encoded_keyword = keyword.replace(" ", "%20")
    search_url = f"https://www.tiktok.com/search?q={encoded_keyword}"
    results: list[TrendVideo] = []

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(search_url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(5000)

            links = page.eval_on_selector_all(
                'a[href*="/video/"]',
                "elements => elements.map(el => el.href)",
            )
            browser.close()

        seen: set[str] = set()
        for href in links:
            if not href or href in seen:
                continue
            seen.add(href)
            match = re.search(r"/video/(\d+)", href)
            video_id = match.group(1) if match else href.rstrip("/").split("/")[-1]
            results.append(
                TrendVideo(
                    video_id=video_id,
                    title=f"TikTok video {video_id}",
                    url=href.split("?")[0],
                    view_count=None,
                    platform="tiktok",
                )
            )
            if len(results) >= limit:
                break
    except Exception as exc:
        logger.warning("TikTok Playwright scrape failed: %s", exc)

    return results


def get_trending_videos(keyword: str, limit: int = 5) -> list[dict]:
    """
    Fetch trending metadata from YouTube and TikTok.

    Returns a validated list of dictionaries with video_id, title, url,
    view_count, and platform.
    """
    if not keyword or not keyword.strip():
        raise ValueError("keyword must be a non-empty string.")

    youtube_videos = _fetch_youtube_trends(keyword.strip(), limit=limit)
    tiktok_videos = _fetch_tiktok_trends(keyword.strip(), limit=3)

    combined = youtube_videos + tiktok_videos
    validated = TrendVideoList(videos=combined)

    try:
        return [video.model_dump() for video in validated.videos]
    except ValidationError as exc:
        raise ValueError(f"Trend data validation failed: {exc}") from exc
