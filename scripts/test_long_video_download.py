"""One-off: download one long YouTube video into pipeline Videos/ folder."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Avoid Chrome cookie DB lock during manual test runs.
os.environ.pop("YTDLP_COOKIES_FROM_BROWSER", None)

from src.scraper_config import ScraperConfig
from src.storage_manager import StorageManager
from src.trend_scraper import (
    _enrich_url_with_ytdlp,
    _fetch_youtube_for_keyword,
    _ydl_search_entries,
)
from src.video_downloader import download_trend_video

# Long = 10–35 min (avoids multi-hour downloads during manual tests).
MIN_DURATION_SEC = int(os.getenv("LONG_TEST_MIN_DURATION", "600"))
MAX_DURATION_SEC = int(os.getenv("LONG_TEST_MAX_DURATION", "2100"))
KEYWORD = os.getenv("LONG_TEST_KEYWORD", "TED talk technology")
# dotenv may re-enable browser cookies — clear again before download.
os.environ["YTDLP_COOKIES_FROM_BROWSER"] = ""


def _watch_url(entry: dict) -> str | None:
    vid = entry.get("id")
    if not vid:
        return None
    return entry.get("webpage_url") or f"https://www.youtube.com/watch?v={vid}"


def _duration_ok(duration: object) -> int | None:
    if not isinstance(duration, (int, float)):
        return None
    sec = int(duration)
    if MIN_DURATION_SEC <= sec <= MAX_DURATION_SEC:
        return sec
    return None


def find_long_watch_url() -> tuple[str, str, int]:
    forced = os.getenv("LONG_TEST_URL", "").strip()
    if forced:
        info = _enrich_url_with_ytdlp(forced) or {}
        duration = _duration_ok(info.get("duration")) or int(info.get("duration") or 0)
        return forced, str(info.get("title") or forced), duration

    config = ScraperConfig(recency_days=365, min_views=0)
    best: tuple[int, str, str] | None = None

    def consider(url: str, title: str, duration: int) -> None:
        nonlocal best
        if best is None or abs(duration - 1200) < abs(best[0] - 1200):
            best = (duration, url, title)

    videos = _fetch_youtube_for_keyword(KEYWORD, config=config, want_format="long")
    for video in videos:
        info = _enrich_url_with_ytdlp(video.url) or {}
        duration = _duration_ok(info.get("duration"))
        if duration:
            consider(video.url, video.title or video.url, duration)

    for query in (f"ytsearch15:{KEYWORD}", f"ytsearch15:{KEYWORD} lecture"):
        for entry in _ydl_search_entries(query, config.recency_days):
            url = _watch_url(entry)
            if not url:
                continue
            info = _enrich_url_with_ytdlp(url) or {}
            duration = _duration_ok(info.get("duration"))
            if duration:
                consider(url, str(info.get("title") or url), duration)

    if best:
        duration, url, title = best
        return url, title, duration

    raise SystemExit(
        f"No YouTube video between {MIN_DURATION_SEC}s and {MAX_DURATION_SEC}s "
        f"for keyword: {KEYWORD!r}"
    )


def main() -> None:
    topic = os.getenv("LONG_TEST_TOPIC", "long_video_test")
    manager = StorageManager(trend_topic=topic)
    paths = manager.resolve_structure()

    print("=== Pipeline storage ===")
    print(f"trend_root:  {paths['trend_root']}")
    print(f"videos_dir:  {paths['videos_dir']}")
    print()

    url, title, duration = find_long_watch_url()
    print("=== Selected video ===")
    print(f"title:    {title}")
    print(f"duration: {duration}s (~{duration // 60} min)")
    print(f"url:      {url}")
    print()
    print("Downloading (may take several minutes)...")

    video_path = download_trend_video(url, paths["videos_dir"], platform="youtube")
    if not video_path:
        print("ERROR: download failed.", file=sys.stderr)
        sys.exit(1)

    path = __import__("pathlib").Path(video_path)
    print()
    print("=== Download OK ===")
    print(f"file: {path.resolve()}")
    print(f"size: {path.stat().st_size:,} bytes ({path.stat().st_size / (1024 * 1024):.1f} MiB)")


if __name__ == "__main__":
    main()
