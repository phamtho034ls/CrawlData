"""
Integration test: TikTok search (Playwright stealth) → download (Module 1 + 3).

Requires network + Playwright chromium. Run:
  playwright install chromium
  pytest tests/test_tiktok_search_download.py -v -s

Override URL if search is blocked:
  set PIPELINE_TEST_TIKTOK_URL=https://www.tiktok.com/@user/video/123
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

import yt_dlp

from src.scraper_config import ScraperConfig
from src.trend_scraper import _discover_tiktok_urls_stealth, _enrich_tiktok_url
from src.video_downloader import download_trend_video

pytestmark = pytest.mark.integration

TEST_KEYWORD = os.getenv("PIPELINE_TEST_TIKTOK_KEYWORD", "python tips")
FALLBACK_TIKTOK_URL = os.getenv("PIPELINE_TEST_TIKTOK_URL", "").strip()
MAX_CANDIDATES = 8


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


def _discover_tiktok_via_ytdlp(profile: str = "https://www.tiktok.com/@tiktok") -> str | None:
    """Flat-list a public profile when Playwright search is blocked."""
    opts = {"quiet": True, "no_warnings": True, "extract_flat": True, "playlistend": 5}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(profile, download=False)
    except Exception:
        return None
    for entry in (info or {}).get("entries") or []:
        url = entry.get("webpage_url") or entry.get("url") or ""
        if "/video/" in url:
            return url.split("?")[0]
    return None


def resolve_tiktok_watch_url(keyword: str, config: ScraperConfig) -> tuple[str, str]:
    if FALLBACK_TIKTOK_URL and "tiktok.com" in FALLBACK_TIKTOK_URL:
        return FALLBACK_TIKTOK_URL, "env_fallback"

    urls = _discover_tiktok_urls_stealth(keyword, MAX_CANDIDATES)
    for url in urls:
        video = _enrich_tiktok_url(url, keyword, config)
        if video and video.url:
            return video.url, "stealth_search"

    if urls:
        clean = urls[0].split("?")[0]
        return clean, "stealth_search_raw"

    ytdlp_url = _discover_tiktok_via_ytdlp()
    if ytdlp_url:
        return ytdlp_url, "ytdlp_profile"

    pytest.skip(
        "TikTok search returned no URLs (bot block or Playwright missing). "
        "Set PIPELINE_TEST_TIKTOK_URL to a public /video/ link."
    )


@pytest.fixture(scope="module")
def tiktok_watch_url() -> tuple[str, str]:
    return resolve_tiktok_watch_url(TEST_KEYWORD, _relaxed_scraper_config())


def test_tiktok_search_finds_video_url(tiktok_watch_url: tuple[str, str]):
    url, source = tiktok_watch_url
    assert "tiktok.com" in url.lower(), source
    assert "/video/" in url, f"Expected /video/ in URL: {url}"


def test_tiktok_download_from_search_url(tiktok_watch_url: tuple[str, str]):
    url, source = tiktok_watch_url

    with tempfile.TemporaryDirectory(prefix="datacrawl_tt_test_") as tmp:
        out_dir = Path(tmp)
        video_path = download_trend_video(url, out_dir, platform="tiktok")

        assert video_path is not None, (
            f"download_trend_video returned None for {url} (source={source})"
        )
        path = Path(video_path)
        assert path.is_file(), f"Expected file at {video_path}"
        size = path.stat().st_size
        assert size > 10_000, f"Downloaded file too small ({size} bytes)"
        assert path.suffix.lower() in {".mp4", ".webm", ".mkv", ".mov", ".m4v"}
        assert size < 120 * 1024 * 1024, (
            f"Downloaded file unusually large ({size} bytes)"
        )
