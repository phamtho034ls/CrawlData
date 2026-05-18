"""Multi-keyword trend discovery: YouTube shorts/long, TikTok, filters, stealth browse."""

from __future__ import annotations

import logging
import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

import yt_dlp
from yt_dlp.utils import DownloadError
from playwright.sync_api import sync_playwright
from pydantic import BaseModel, Field, ValidationError

from src.browser_stealth import (
    goto_like_human,
    human_scroll,
    launch_stealth_browser,
)
from src.keyword_expansion import expand_keywords
from src.pipeline_progress import PipelineProgress
from src.scraper_config import ScraperConfig

logger = logging.getLogger(__name__)

Platform = Literal["youtube", "tiktok"]
VideoFormat = Literal["short", "long", "tiktok"]

YOUTUBE_SEARCH_BUFFER = 40
TIKTOK_CANDIDATE_PER_KEYWORD = 35
TIKTOK_ENRICH_WORKERS = 6
SHORT_MAX_SECONDS = 60


class _YtdlQuietLogger:
    """Route yt-dlp noise to debug; skip benign 'video not available' errors."""

    def debug(self, msg: str) -> None:
        logger.debug(msg)

    def info(self, msg: str) -> None:
        pass

    def warning(self, msg: str) -> None:
        logger.debug(msg)

    def error(self, msg: str) -> None:
        lowered = msg.lower()
        if "not available" in lowered or "private video" in lowered:
            logger.debug("YouTube skipped entry: %s", msg)
            return
        logger.warning("YouTube: %s", msg)


def _base_ydl_opts(**overrides: Any) -> dict[str, Any]:
    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "ignoreerrors": True,
        "logger": _YtdlQuietLogger(),
    }
    opts.update(overrides)
    return opts


def _is_usable_youtube_entry(entry: dict[str, Any] | None) -> bool:
    if not entry:
        return False
    if entry.get("id") or entry.get("url") or entry.get("webpage_url"):
        return True
    return False


class TrendVideo(BaseModel):
    video_id: str
    title: str
    url: str
    view_count: int | None = None
    platform: Platform
    video_format: VideoFormat = "short"
    upload_date: str | None = None
    source_keyword: str | None = None
    duration: int | None = None


class TrendVideoList(BaseModel):
    videos: list[TrendVideo] = Field(default_factory=list)


def _parse_view_count(raw: object) -> int | None:
    if raw is None:
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str):
        import re

        digits = re.sub(r"[^\d]", "", raw)
        return int(digits) if digits else None
    return None


def _cutoff_datetime(recency_days: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=recency_days)


def _cutoff_yyyymmdd(recency_days: int) -> str:
    return _cutoff_datetime(recency_days).strftime("%Y%m%d")


def _parse_upload_datetime(entry: dict[str, Any]) -> datetime | None:
    ts = entry.get("timestamp") or entry.get("release_timestamp")
    if ts is not None:
        try:
            return datetime.fromtimestamp(float(ts), tz=timezone.utc)
        except (TypeError, ValueError, OSError):
            pass
    upload_date = entry.get("upload_date")
    if upload_date and len(str(upload_date)) == 8:
        try:
            return datetime.strptime(str(upload_date), "%Y%m%d").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            pass
    return None


def _within_recency(published: datetime | None, recency_days: int) -> bool:
    if published is None:
        return False
    return published >= _cutoff_datetime(recency_days)


def _passes_view_filter(view_count: int | None, min_views: int) -> bool:
    return view_count is not None and view_count >= min_views


def _classify_youtube_format(duration: int | None) -> VideoFormat:
    if duration is None:
        return "long"
    return "short" if duration <= SHORT_MAX_SECONDS else "long"


def _entry_to_trend_video(
    entry: dict[str, Any],
    *,
    platform: Platform,
    video_format: VideoFormat,
    source_keyword: str,
) -> TrendVideo:
    video_id = str(entry.get("id") or entry.get("video_id") or "")
    title = entry.get("title") or "Untitled"
    url = (
        entry.get("webpage_url")
        or entry.get("url")
        or entry.get("original_url")
        or ""
    )
    if not url and video_id and platform == "youtube":
        url = f"https://www.youtube.com/watch?v={video_id}"
    duration = entry.get("duration")
    if isinstance(duration, (int, float)):
        duration = int(duration)
    else:
        duration = None
    upload_date = entry.get("upload_date")
    return TrendVideo(
        video_id=video_id,
        title=title,
        url=url,
        view_count=_parse_view_count(entry.get("view_count")),
        platform=platform,
        video_format=video_format if platform == "youtube" else "tiktok",
        upload_date=str(upload_date) if upload_date else None,
        source_keyword=source_keyword,
        duration=duration,
    )


def _ydl_search_entries(search_term: str, recency_days: int) -> list[dict[str, Any]]:
    """Flat search — avoids failing whole batch when one result is unavailable."""
    ydl_opts = _base_ydl_opts(
        extract_flat=True,
        dateafter=_cutoff_yyyymmdd(recency_days),
    )
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(search_term, download=False)
    except DownloadError as exc:
        logger.debug("YouTube search partial skip (%s): %s", search_term, exc)
        return []
    except Exception as exc:
        logger.warning("YouTube search failed (%s): %s", search_term, exc)
        return []
    if not info:
        return []
    return [e for e in (info.get("entries") or []) if _is_usable_youtube_entry(e)]


def _enrich_youtube_entry(
    entry: dict[str, Any], ydl: yt_dlp.YoutubeDL
) -> dict[str, Any] | None:
    views = _parse_view_count(entry.get("view_count"))
    published = _parse_upload_datetime(entry)
    if views is not None and published is not None:
        return entry
    vid = entry.get("id")
    if not vid:
        return None
    url = entry.get("webpage_url") or f"https://www.youtube.com/watch?v={vid}"
    try:
        full = ydl.extract_info(url, download=False)
        return full if _is_usable_youtube_entry(full) else None
    except DownloadError as exc:
        logger.debug("YouTube unavailable %s: %s", vid, exc)
        return None
    except Exception as exc:
        logger.debug("YouTube enrich skip %s: %s", vid, exc)
        return None


def _process_youtube_entry(
    entry: dict[str, Any],
    *,
    source_keyword: str,
    config: ScraperConfig,
    want_format: VideoFormat | None,
) -> TrendVideo | None:
    published = _parse_upload_datetime(entry)
    views = _parse_view_count(entry.get("view_count"))
    if published and not _within_recency(published, config.recency_days):
        return None
    if not _passes_view_filter(views, config.min_views):
        return None
    duration = entry.get("duration")
    if isinstance(duration, (int, float)):
        duration = int(duration)
    else:
        duration = None
    fmt = _classify_youtube_format(duration)
    if want_format and fmt != want_format:
        return None
    video = _entry_to_trend_video(
        entry, platform="youtube", video_format=fmt, source_keyword=source_keyword
    )
    video.view_count = views
    if published:
        video.upload_date = published.strftime("%Y%m%d")
    video.duration = duration
    return video


def _fetch_youtube_for_keyword(
    keyword: str,
    *,
    config: ScraperConfig,
    want_format: VideoFormat,
) -> list[TrendVideo]:
    buffer = max(YOUTUBE_SEARCH_BUFFER, config.videos_per_keyword_search * 2)
    if want_format == "short":
        queries = [
            f"ytsearch{buffer}:{keyword} shorts",
            f"ytsearch{buffer}:{keyword} #shorts",
        ]
    else:
        queries = [
            f"ytsearch{buffer}:{keyword}",
            f"ytsearch{buffer}:{keyword} full video",
        ]

    seen: set[str] = set()
    raw: list[dict[str, Any]] = []
    for q in queries:
        for entry in _ydl_search_entries(q, config.recency_days):
            vid = entry.get("id")
            if not vid or vid in seen:
                continue
            seen.add(vid)
            raw.append(entry)

    results: list[TrendVideo] = []
    with yt_dlp.YoutubeDL(_base_ydl_opts()) as ydl:
        for entry in raw:
            enriched = _enrich_youtube_entry(entry, ydl)
            if not enriched:
                continue
            video = _process_youtube_entry(
                enriched,
                source_keyword=keyword,
                config=config,
                want_format=want_format,
            )
            if video:
                results.append(video)

    results.sort(key=lambda v: v.view_count or 0, reverse=True)
    return results


def _enrich_url_with_ytdlp(url: str) -> dict[str, Any] | None:
    try:
        with yt_dlp.YoutubeDL(_base_ydl_opts()) as ydl:
            info = ydl.extract_info(url, download=False)
            return info if _is_usable_youtube_entry(info) else None
    except DownloadError as exc:
        logger.debug("yt-dlp unavailable %s: %s", url, exc)
        return None
    except Exception as exc:
        logger.debug("yt-dlp enrich failed %s: %s", url, exc)
        return None


def _discover_tiktok_urls_stealth(keyword: str, max_urls: int) -> list[str]:
    encoded = keyword.replace(" ", "%20")
    search_url = f"https://www.tiktok.com/search/video?q={encoded}"
    collected: list[str] = []
    seen: set[str] = set()

    try:
        with sync_playwright() as playwright:
            browser, context = launch_stealth_browser(playwright)
            page = context.new_page()
            try:
                goto_like_human(page, search_url)
                human_scroll(page, rounds=10)
                links = page.eval_on_selector_all(
                    'a[href*="/video/"]',
                    "els => els.map(e => e.href)",
                )
                for href in links or []:
                    if not href or "/video/" not in href:
                        continue
                    clean = href.split("?")[0]
                    if clean in seen:
                        continue
                    seen.add(clean)
                    collected.append(clean)
                    if len(collected) >= max_urls:
                        break
            finally:
                context.close()
                browser.close()
    except Exception as exc:
        logger.warning("TikTok stealth browse failed: %s", exc)

    return collected


def _enrich_tiktok_url(url: str, source_keyword: str, config: ScraperConfig) -> TrendVideo | None:
    info = _enrich_url_with_ytdlp(url)
    if not info:
        return None
    import re

    match = re.search(r"/video/(\d+)", url)
    video_id = str(info.get("id") or (match.group(1) if match else ""))
    published = _parse_upload_datetime(info)
    views = _parse_view_count(info.get("view_count"))
    if published and not _within_recency(published, config.recency_days):
        return None
    if not _passes_view_filter(views, config.min_views):
        return None
    upload_date = published.strftime("%Y%m%d") if published else None
    return TrendVideo(
        video_id=video_id,
        title=info.get("title") or f"TikTok video {video_id}",
        url=info.get("webpage_url") or url,
        view_count=views,
        platform="tiktok",
        video_format="tiktok",
        upload_date=upload_date,
        source_keyword=source_keyword,
    )


def _fetch_tiktok_for_keyword(keyword: str, *, config: ScraperConfig) -> list[TrendVideo]:
    urls = _discover_tiktok_urls_stealth(keyword, TIKTOK_CANDIDATE_PER_KEYWORD)
    if not urls:
        return []

    candidates: list[TrendVideo] = []
    workers = max(1, min(TIKTOK_ENRICH_WORKERS, len(urls)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_enrich_tiktok_url, url, keyword, config): url for url in urls
        }
        for future in as_completed(futures):
            try:
                video = future.result()
            except Exception:
                continue
            if video:
                candidates.append(video)

    candidates.sort(key=lambda v: v.view_count or 0, reverse=True)
    return candidates


def scrape_keyword_pool(keyword: str, config: ScraperConfig) -> list[TrendVideo]:
    """Gather up to `videos_per_keyword_search` videos across YT short/long + TikTok."""
    pool: list[TrendVideo] = []
    pool.extend(_fetch_youtube_for_keyword(keyword, config=config, want_format="short"))
    pool.extend(_fetch_youtube_for_keyword(keyword, config=config, want_format="long"))
    pool.extend(_fetch_tiktok_for_keyword(keyword, config=config))

    pool = _dedupe_videos(pool)
    pool.sort(key=lambda v: v.view_count or 0, reverse=True)
    return pool[: config.videos_per_keyword_search]


def _dedupe_videos(videos: list[TrendVideo]) -> list[TrendVideo]:
    seen: set[str] = set()
    out: list[TrendVideo] = []
    for video in videos:
        key = (video.url or video.video_id).lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(video)
    return out


def collect_videos_for_topic(
    topic: str,
    config: ScraperConfig | None = None,
    progress: PipelineProgress | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """
    Expand topic → keywords; per keyword keep top N by views.
    Returns (video dicts, keyword list).
    """
    config = config or ScraperConfig.from_env()
    topic = topic.strip()
    if not topic:
        raise ValueError("topic must be non-empty")

    if progress:
        progress.step("Đang mở rộng từ khóa (LLM)…")

    keywords = expand_keywords(topic, config.keyword_count)
    logger.info("Expanded '%s' → %d keywords: %s", topic, len(keywords), keywords)

    if progress:
        progress.set_keywords(keywords)

    all_videos: list[TrendVideo] = []

    for index, kw in enumerate(keywords, start=1):
        if progress:
            progress.begin_keyword_search(kw, index, len(keywords), step=2 + index)
        try:
            pool = scrape_keyword_pool(kw, config)
            top = pool[: config.top_videos_per_keyword]
        except Exception as exc:
            logger.warning("Keyword scrape failed '%s': %s", kw, exc)
            if progress:
                progress.fail_keyword_search(kw)
            continue

        if progress:
            progress.complete_keyword_search(kw, len(top))

        logger.info(
            "Keyword '%s': pool=%d → top %d (views %s)",
            kw,
            len(pool),
            len(top),
            [v.view_count for v in top[:3]],
        )
        all_videos.extend(top)
        if index < len(keywords):
            time.sleep(random.uniform(1.0, 2.5))

    all_videos = _dedupe_videos(all_videos)
    all_videos.sort(key=lambda v: v.view_count or 0, reverse=True)

    if not all_videos:
        raise ValueError(
            f"No videos matched (views>={config.min_views}, last {config.recency_days} days) "
            f"for topic: {topic}"
        )

    validated = TrendVideoList(videos=all_videos)
    try:
        return [v.model_dump() for v in validated.videos], keywords
    except ValidationError as exc:
        raise ValueError(f"Trend validation failed: {exc}") from exc


def get_trending_videos(
    keyword: str,
    *,
    limit: int | None = None,
    recency_days: int | None = None,
    min_views: int | None = None,
    progress: PipelineProgress | None = None,
    keyword_count: int | None = None,
    videos_per_keyword_search: int | None = None,
    top_videos_per_keyword: int | None = None,
) -> list[dict]:
    """Backward-compatible entry: runs full multi-keyword collection."""
    overrides: dict[str, int] = {}
    if recency_days is not None:
        overrides["recency_days"] = recency_days
    if min_views is not None:
        overrides["min_views"] = min_views
    if keyword_count is not None:
        overrides["keyword_count"] = keyword_count
    if videos_per_keyword_search is not None:
        overrides["videos_per_keyword_search"] = videos_per_keyword_search
    if top_videos_per_keyword is not None:
        overrides["top_videos_per_keyword"] = top_videos_per_keyword

    config = ScraperConfig.from_env(**overrides)
    videos, _keywords = collect_videos_for_topic(keyword, config, progress)
    if limit is not None:
        return videos[:limit]
    return videos[: config.max_total_videos]
