"""Channel view-velocity tracker for early trend detection."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

import pandas as pd
import yt_dlp
from yt_dlp.utils import DownloadError

logger = logging.getLogger(__name__)

DEFAULT_MAX_AGE_HOURS = 48
RECENT_VIDEOS_PER_CHANNEL = 3


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
            return datetime.strptime(str(upload_date), "%Y%m%d").replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


def _detect_platform(url: str) -> str:
    lower = url.lower()
    if "tiktok.com" in lower:
        return "tiktok"
    if "youtube.com" in lower or "youtu.be" in lower:
        return "youtube"
    return "unknown"


def _channel_videos_url(channel_url: str) -> str:
    url = channel_url.strip().rstrip("/")
    if "/video/" in url:
        url = url.split("/video/")[0]
    if _detect_platform(url) == "youtube":
        if "/videos" not in url and "/shorts" not in url and "/streams" not in url:
            url = f"{url}/videos"
    return url


def _fetch_recent_channel_entries(channel_url: str, limit: int = RECENT_VIDEOS_PER_CHANNEL) -> list[dict[str, Any]]:
    target = _channel_videos_url(channel_url)
    opts = _base_ydl_opts(extract_flat=True, playlistend=limit)

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(target, download=False)
    except DownloadError as exc:
        logger.warning("Channel extract failed %s: %s", channel_url, exc)
        return []
    except Exception as exc:
        logger.warning("Channel error %s: %s", channel_url, exc)
        return []

    if not info:
        return []

    entries = [e for e in (info.get("entries") or []) if e and e.get("id")]
    enriched: list[dict[str, Any]] = []

    with yt_dlp.YoutubeDL(_base_ydl_opts()) as ydl_full:
        for entry in entries[:limit]:
            vid = entry.get("id")
            if not vid:
                continue
            watch = entry.get("url") or entry.get("webpage_url")
            if not watch:
                platform = _detect_platform(channel_url)
                if platform == "youtube":
                    watch = f"https://www.youtube.com/watch?v={vid}"
                else:
                    watch = entry.get("webpage_url") or target
            try:
                full = ydl_full.extract_info(watch, download=False)
            except Exception:
                full = entry
            if full:
                enriched.append(full)

    return enriched


def calculate_channel_velocity(
    channel_urls: list[str],
    *,
    max_age_hours: float = DEFAULT_MAX_AGE_HOURS,
) -> pd.DataFrame:
    """
    For each channel URL, fetch the 3 most recent videos and compute
    View Velocity = view_count / hours_since_upload.

    Drops videos older than `max_age_hours`. Returns a DataFrame sorted by
    velocity descending.
    """
    now = datetime.now(timezone.utc)
    rows: list[dict[str, Any]] = []

    for channel_url in channel_urls:
        channel_url = channel_url.strip()
        if not channel_url:
            continue
        platform = _detect_platform(channel_url)

        for entry in _fetch_recent_channel_entries(channel_url):
            published = _parse_upload_datetime(entry)
            if published is None:
                continue

            hours_since = (now - published).total_seconds() / 3600.0
            if hours_since > max_age_hours or hours_since < 0:
                continue

            views = _parse_view_count(entry.get("view_count"))
            if views is None:
                continue

            hours_safe = max(hours_since, 0.1)
            velocity = views / hours_safe
            video_id = str(entry.get("id") or "")
            url = (
                entry.get("webpage_url")
                or entry.get("url")
                or (
                    f"https://www.youtube.com/watch?v={video_id}"
                    if platform == "youtube"
                    else channel_url
                )
            )

            rows.append(
                {
                    "channel_url": channel_url,
                    "platform": platform,
                    "video_id": video_id,
                    "title": entry.get("title") or "Untitled",
                    "url": url,
                    "view_count": views,
                    "upload_date": published.strftime("%Y-%m-%d %H:%M UTC"),
                    "hours_since_upload": round(hours_since, 2),
                    "view_velocity": round(velocity, 2),
                }
            )

    if not rows:
        return pd.DataFrame(
            columns=[
                "channel_url",
                "platform",
                "video_id",
                "title",
                "url",
                "view_count",
                "upload_date",
                "hours_since_upload",
                "view_velocity",
            ]
        )

    df = pd.DataFrame(rows)
    return df.sort_values("view_velocity", ascending=False).reset_index(drop=True)


def velocity_to_records(df: pd.DataFrame) -> list[dict[str, Any]]:
    """Convert velocity DataFrame rows to pipeline-compatible video dicts."""
    if df.empty:
        return []
    records: list[dict[str, Any]] = []
    for row in df.to_dict(orient="records"):
        records.append(
            {
                "video_id": row.get("video_id") or "",
                "title": row.get("title") or "Untitled",
                "url": row.get("url") or "",
                "view_count": row.get("view_count"),
                "platform": row.get("platform") or "youtube",
                "video_format": "tiktok" if row.get("platform") == "tiktok" else "short",
                "upload_date": None,
                "source_keyword": f"velocity:{row.get('channel_url', '')}",
            }
        )
    return records
