"""
Integration test: YouTube search (yt-dlp) → download (pipeline Module 1 + 3).

Requires network. Run:
  pytest tests/test_youtube_search_download.py -v -s

Skip integration tests in CI:
  pytest -m "not integration"
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from src.scraper_config import ScraperConfig
from src.trend_scraper import (
    _enrich_url_with_ytdlp,
    _fetch_youtube_for_keyword,
    _ydl_search_entries,
)
from src.video_downloader import download_trend_video

pytestmark = pytest.mark.integration

TEST_KEYWORD = os.getenv("PIPELINE_TEST_KEYWORD", "python programming tutorial")
FALLBACK_SHORT_URL = "https://www.youtube.com/watch?v=jNQXAC9IVRw"
MAX_TEST_DURATION_SEC = int(os.getenv("PIPELINE_TEST_MAX_DURATION", "120"))


@pytest.fixture(autouse=True)
def _disable_browser_cookies(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("YTDLP_COOKIES_FROM_BROWSER", raising=False)


def _relaxed_scraper_config() -> ScraperConfig:
    return ScraperConfig(
        recency_days=365,
        min_views=0,
        keyword_count=1,
        videos_per_platform=1,
        videos_per_keyword_search=5,
        top_videos_per_keyword=1,
    )


def _watch_url_from_entry(entry: dict) -> str | None:
    video_id = entry.get("id")
    if not video_id:
        return None
    return (
        entry.get("webpage_url")
        or entry.get("url")
        or f"https://www.youtube.com/watch?v={video_id}"
    )


def _shortest_watch_url_from_flat_search(keyword: str, *, recency_days: int) -> str | None:
    queries = [
        f"ytsearch10:{keyword} #shorts",
        f"ytsearch10:{keyword} shorts",
    ]
    best: tuple[int, str] | None = None
    for query in queries:
        for entry in _ydl_search_entries(query, recency_days):
            url = _watch_url_from_entry(entry)
            if not url:
                continue
            info = _enrich_url_with_ytdlp(url)
            duration = (info or {}).get("duration")
            if not isinstance(duration, (int, float)) or duration <= 0:
                continue
            duration = int(duration)
            if duration > MAX_TEST_DURATION_SEC:
                continue
            if best is None or duration < best[0]:
                best = (duration, url)
            if best and best[0] <= 60:
                return best[1]
    return best[1] if best else None


def resolve_watch_url_for_test(keyword: str, config: ScraperConfig) -> tuple[str, str]:
    url = _shortest_watch_url_from_flat_search(keyword, recency_days=config.recency_days)
    if url:
        return url, "flat_search_shortest"

    videos = _fetch_youtube_for_keyword(keyword, config=config, want_format="short")
    if videos:
        return videos[0].url, "_fetch_youtube_for_keyword(short)"

    return FALLBACK_SHORT_URL, "fallback_short_clip"


@pytest.fixture(scope="module")
def youtube_watch_url() -> tuple[str, str]:
    """Resolve once per run — yt-dlp search/enrich is slow."""
    return resolve_watch_url_for_test(TEST_KEYWORD, _relaxed_scraper_config())


def test_youtube_search_finds_watch_url(youtube_watch_url: tuple[str, str]):
    url, source = youtube_watch_url
    assert "youtube.com" in url or "youtu.be" in url, source
    assert "watch?v=" in url or "youtu.be/" in url


def test_youtube_download_from_search_url(youtube_watch_url: tuple[str, str]):
    url, source = youtube_watch_url

    with tempfile.TemporaryDirectory(prefix="datacrawl_yt_test_") as tmp:
        out_dir = Path(tmp)
        video_path = download_trend_video(url, out_dir, platform="youtube")

        assert video_path is not None, (
            f"download_trend_video returned None for {url} (source={source})"
        )
        path = Path(video_path)
        assert path.is_file(), f"Expected file at {video_path}"
        size = path.stat().st_size
        assert size > 10_000, f"Downloaded file too small ({size} bytes)"
        assert path.suffix.lower() in {".mp4", ".webm", ".mkv", ".mov", ".m4v"}
        assert size < 80 * 1024 * 1024, (
            f"Downloaded file unusually large ({size} bytes); "
            "set PIPELINE_TEST_MAX_DURATION lower or use a shorter keyword."
        )
