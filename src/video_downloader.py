"""Download YouTube and TikTok videos as MP4 via yt-dlp."""

from __future__ import annotations

import logging
import os
import shutil
import time
from pathlib import Path
from typing import Any

import yt_dlp
from dotenv import load_dotenv
from yt_dlp.utils import DownloadError

load_dotenv()

logger = logging.getLogger(__name__)

_VIDEO_EXTENSIONS = {".mp4", ".webm", ".mkv", ".mov", ".m4v"}

_MOBILE_USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)

_DESKTOP_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

# After Chrome cookie DB lock, skip browser cookies for remaining downloads in this process.
_browser_cookies_disabled = False


def reset_download_session() -> None:
    """Reset per-process download state (e.g. before a new pipeline batch)."""
    global _browser_cookies_disabled
    _browser_cookies_disabled = False


def _ffmpeg_location() -> str | None:
    """Resolve ffmpeg binary (PATH or common WinGet install on Windows)."""
    found = shutil.which("ffmpeg")
    if found:
        return found

    local_app = os.environ.get("LOCALAPPDATA", "")
    if local_app:
        winget_root = Path(local_app) / "Microsoft" / "WinGet" / "Packages"
        if winget_root.is_dir():
            for candidate in winget_root.glob("**/ffmpeg.exe"):
                if candidate.is_file():
                    return str(candidate.resolve())

    return None


def _cookies_browser() -> str:
    return os.getenv("YTDLP_COOKIES_FROM_BROWSER", "").strip()


def _cookies_file() -> str | None:
    path = os.getenv("YTDLP_COOKIES_FILE", "").strip()
    if path and Path(path).is_file():
        return str(Path(path).resolve())
    return None


def _youtube_player_clients() -> list[str]:
    raw = os.getenv("YTDLP_YOUTUBE_CLIENTS", "android,web").strip()
    clients = [c.strip() for c in raw.split(",") if c.strip()]
    return clients or ["android", "web"]


def _download_delay_seconds() -> float:
    try:
        return max(0.0, float(os.getenv("YTDLP_DOWNLOAD_DELAY_SEC", "4")))
    except ValueError:
        return 4.0


def notify_between_downloads() -> None:
    """Sleep between consecutive video downloads to reduce YouTube bot blocks."""
    delay = _download_delay_seconds()
    if delay > 0:
        time.sleep(delay)


def _apply_common_opts(
    opts: dict[str, Any],
    *,
    cookie_mode: str,
) -> dict[str, Any]:
    ffmpeg = _ffmpeg_location()
    if ffmpeg:
        opts["ffmpeg_location"] = ffmpeg
    else:
        logger.warning(
            "ffmpeg not found — using single-file formats where possible. "
            "Install ffmpeg for best quality merges."
        )

    if cookie_mode == "file":
        cookie_path = _cookies_file()
        if cookie_path:
            opts["cookiefile"] = cookie_path
            logger.info("yt-dlp using cookies file: %s", cookie_path)
    elif cookie_mode == "browser":
        browser = _cookies_browser()
        if browser:
            opts["cookiesfrombrowser"] = (browser,)
            logger.info("yt-dlp using cookies from browser: %s", browser)

    opts.setdefault(
        "http_headers",
        {"User-Agent": _DESKTOP_USER_AGENT},
    )
    return opts


def _find_downloaded_video(output_folder: Path, video_id: str | None = None) -> Path | None:
    """Locate the downloaded video file (merge may change extension or name)."""
    if video_id:
        for candidate in sorted(output_folder.glob(f"{video_id}*")):
            if candidate.is_file() and candidate.suffix.lower() in _VIDEO_EXTENSIONS:
                return candidate.resolve()

    video_files = [
        p
        for p in output_folder.iterdir()
        if p.is_file() and p.suffix.lower() in _VIDEO_EXTENSIONS
    ]
    if not video_files:
        return None
    return max(video_files, key=lambda p: p.stat().st_mtime).resolve()


def _resolve_downloaded_path(
    info: dict[str, Any] | None,
    output_folder: Path,
    *,
    ydl: yt_dlp.YoutubeDL | None = None,
) -> Path | None:
    if info:
        filepath = info.get("filepath")
        if filepath and Path(filepath).is_file():
            return Path(filepath).resolve()

        if info.get("requested_downloads"):
            for item in info["requested_downloads"]:
                filepath = item.get("filepath")
                if filepath and Path(filepath).is_file():
                    return Path(filepath).resolve()

        if ydl:
            try:
                prepared = Path(ydl.prepare_filename(info))
                for candidate in (
                    prepared,
                    prepared.with_suffix(".mp4"),
                    prepared.parent / f"{prepared.stem}.mp4",
                ):
                    if candidate.is_file():
                        return candidate.resolve()
            except Exception:
                pass

        video_id = info.get("id")
        found = _find_downloaded_video(
            output_folder, str(video_id) if video_id else None
        )
        if found:
            return found

    return _find_downloaded_video(output_folder)


def _attempt_download(
    url: str,
    out_dir: Path,
    ydl_opts: dict[str, Any],
    *,
    cookie_mode: str,
) -> str | None:
    global _browser_cookies_disabled

    opts = _apply_common_opts(dict(ydl_opts), cookie_mode=cookie_mode)
    opts["outtmpl"] = str(out_dir.resolve() / "%(id)s.%(ext)s")
    opts.setdefault("noplaylist", True)

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
        path = _resolve_downloaded_path(info, out_dir, ydl=ydl)
        if path and path.is_file():
            return str(path)
        logger.warning(
            "Download finished but no video file found in %s for %s", out_dir, url
        )
    except DownloadError as exc:
        msg = str(exc).lower()
        if cookie_mode == "browser" and "cookie" in msg:
            _browser_cookies_disabled = True
            logger.warning(
                "Browser cookies unavailable (close Chrome or set YTDLP_COOKIES_FILE): %s",
                exc,
            )
        logger.warning(
            "yt-dlp DownloadError for %s (cookies=%s): %s",
            url,
            cookie_mode,
            exc,
        )
        raise
    except Exception as exc:
        logger.warning("yt-dlp error for %s: %s", url, exc)
    return None


def _cookie_modes_for_attempt() -> list[str]:
    modes: list[str] = []
    if _cookies_file():
        modes.append("file")
    if _cookies_browser() and not _browser_cookies_disabled:
        modes.append("browser")
    modes.append("none")
    return modes


def _download_with_opts(url: str, out_dir: Path, ydl_opts: dict[str, Any]) -> str | None:
    """Try cookie file → browser → none; each with configured YouTube player clients."""
    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    is_youtube = "youtube.com" in url.lower() or "youtu.be" in url.lower()
    clients = _youtube_player_clients() if is_youtube else [""]

    for client in clients:
        opts = dict(ydl_opts)
        if client:
            opts["extractor_args"] = {"youtube": {"player_client": [client]}}

        for cookie_mode in _cookie_modes_for_attempt():
            try:
                path = _attempt_download(url, out_dir, opts, cookie_mode=cookie_mode)
                if path:
                    if cookie_mode != "file" or client != clients[0]:
                        logger.info(
                            "Download succeeded (client=%s, cookies=%s): %s",
                            client or "default",
                            cookie_mode,
                            url,
                        )
                    return path
            except DownloadError:
                continue
    return None


def download_youtube_video(url: str, output_folder: str | Path) -> str | None:
    """
    Download best quality up to 1080p merged with best audio as .mp4.
    Returns absolute path string, or None if unavailable / blocked.
    """
    url = url.strip()
    out_dir = Path(output_folder)
    out_dir.mkdir(parents=True, exist_ok=True)
    has_ffmpeg = _ffmpeg_location() is not None

    if has_ffmpeg:
        primary_opts: dict[str, Any] = {
            "format": (
                "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/"
                "bv*[height<=1080]+ba/"
                "b[height<=1080][ext=mp4]/b[ext=mp4]/b"
            ),
            "merge_output_format": "mp4",
            "quiet": True,
            "no_warnings": True,
        }
        path = _download_with_opts(url, out_dir, primary_opts)
        if path:
            return path

    logger.info("Retrying YouTube download with single-file format: %s", url)
    fallback_opts: dict[str, Any] = {
        "format": "best[ext=mp4]/best",
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
    }
    return _download_with_opts(url, out_dir, fallback_opts)


def download_tiktok_video(url: str, output_folder: str | Path) -> str | None:
    """
    Download TikTok video as .mp4 (mobile UA + extractor args).
    Returns absolute path string, or None on failure.
    """
    url = url.strip()
    out_dir = Path(output_folder)
    out_dir.mkdir(parents=True, exist_ok=True)

    opts: dict[str, Any] = {
        "format": "best[ext=mp4]/best",
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
        "http_headers": {"User-Agent": _MOBILE_USER_AGENT},
        "extractor_args": {
            "tiktok": {"api_hostname": "api16-normal-c-useast1a.tiktokv.com"},
        },
    }
    path = _download_with_opts(url, out_dir, opts)
    if path:
        return path

    logger.info("Retrying TikTok download with desktop UA: %s", url)
    opts["http_headers"] = {"User-Agent": _DESKTOP_USER_AGENT}
    return _download_with_opts(url, out_dir, opts)


def download_trend_video(url: str, output_folder: str | Path, platform: str = "") -> str | None:
    """Route to YouTube or TikTok downloader by platform / URL."""
    platform = (platform or "").lower()
    if platform == "tiktok" or "tiktok.com" in url.lower():
        return download_tiktok_video(url, output_folder)
    return download_youtube_video(url, output_folder)
