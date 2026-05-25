"""One-off: discover TikTok via search → download into pipeline Videos/."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ["YTDLP_COOKIES_FROM_BROWSER"] = ""

import yt_dlp

from src.scraper_config import ScraperConfig
from src.storage_manager import StorageManager
from src.trend_scraper import _discover_tiktok_urls_stealth, _enrich_tiktok_url
from src.video_downloader import download_trend_video


def _ytdlp_profile_url(profile: str = "https://www.tiktok.com/@tiktok") -> str | None:
    opts = {"quiet": True, "no_warnings": True, "extract_flat": True, "playlistend": 3}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(profile, download=False)
    for entry in (info or {}).get("entries") or []:
        url = entry.get("webpage_url") or entry.get("url") or ""
        if "/video/" in url:
            return url.split("?")[0]
    return None

KEYWORD = os.getenv("TIKTOK_TEST_KEYWORD", "python tips")
FORCED_URL = os.getenv("PIPELINE_TEST_TIKTOK_URL", "").strip()


def main() -> None:
    topic = os.getenv("TIKTOK_TEST_TOPIC", "tiktok_video_test")
    manager = StorageManager(trend_topic=topic)
    paths = manager.resolve_structure()
    config = ScraperConfig(recency_days=365, min_views=0)

    print("=== Pipeline storage ===")
    print(f"trend_root:  {paths['trend_root']}")
    print(f"videos_dir:  {paths['videos_dir']}")
    print()

    if FORCED_URL:
        url, title = FORCED_URL, "forced URL"
    else:
        urls = _discover_tiktok_urls_stealth(KEYWORD, 8)
        if urls:
            url = urls[0].split("?")[0]
        else:
            url = _ytdlp_profile_url()
        if not url:
            print("No TikTok URLs from search. Set PIPELINE_TEST_TIKTOK_URL.", file=sys.stderr)
            sys.exit(1)
        video = _enrich_tiktok_url(url, KEYWORD, config)
        title = (video.title if video else url)

    print("=== Selected video ===")
    print(f"title: {title}")
    print(f"url:   {url}")
    print("\nDownloading...")

    video_path = download_trend_video(url, paths["videos_dir"], platform="tiktok")
    if not video_path:
        print("ERROR: download failed.", file=sys.stderr)
        sys.exit(1)

    path = Path(video_path)
    print("\n=== Download OK ===")
    print(f"file: {path.resolve()}")
    print(f"size: {path.stat().st_size:,} bytes ({path.stat().st_size / (1024 * 1024):.1f} MiB)")


if __name__ == "__main__":
    main()
