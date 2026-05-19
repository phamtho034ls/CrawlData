"""Structured text+link items for trend context display."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

_URL_RE = re.compile(r"https?://[^\s\)\]\"\'<>]+")

_VIDEO_PLATFORM_HOSTS = (
    "youtube.com",
    "youtu.be",
    "m.youtube.com",
    "music.youtube.com",
    "tiktok.com",
    "vm.tiktok.com",
    "vt.tiktok.com",
)


def is_video_platform_url(url: str) -> bool:
    """True for YouTube / TikTok (already covered by video scraper)."""
    if not url:
        return False
    try:
        host = urlparse(url.strip()).netloc.lower().replace("www.", "")
    except Exception:
        return False
    return any(host == h or host.endswith(f".{h}") for h in _VIDEO_PLATFORM_HOSTS)


def _normalize_url(url: str) -> str:
    return url.strip().rstrip(".,;)")


def _blocked_url_set(exclude_urls: list[str] | None = None) -> set[str]:
    blocked: set[str] = set()
    for raw in exclude_urls or []:
        url = _normalize_url(raw)
        if url:
            blocked.add(url)
    return blocked


def filter_allowed_urls(
    urls: list[str],
    *,
    exclude_urls: list[str] | None = None,
) -> list[str]:
    """Drop YouTube/TikTok and explicitly excluded URLs."""
    blocked = _blocked_url_set(exclude_urls)
    out: list[str] = []
    seen: set[str] = set()
    for raw in urls:
        url = _normalize_url(raw)
        if not url or url in seen:
            continue
        if is_video_platform_url(url) or url in blocked:
            continue
        seen.add(url)
        out.append(url)
    return out


def sanitize_text_urls(
    text: str,
    *,
    exclude_urls: list[str] | None = None,
) -> str:
    """Remove YouTube/TikTok (and excluded) URLs from free text."""
    blocked = _blocked_url_set(exclude_urls)
    cleaned = text or ""
    for url in _unique_urls(cleaned):
        if is_video_platform_url(url) or url in blocked:
            cleaned = cleaned.replace(url, "")
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _domain(url: str) -> str:
    try:
        return urlparse(url).netloc.replace("www.", "")
    except Exception:
        return "Nguồn"


def _unique_urls(text: str) -> list[str]:
    seen: set[str] = set()
    urls: list[str] = []
    for match in _URL_RE.finditer(text or ""):
        url = match.group(0).rstrip(".,;)")
        if url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def build_text_items(
    video_title: str,
    summary_text: str,
    search_context: str = "",
    extra_items: list[dict[str, Any]] | None = None,
    *,
    exclude_urls: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Build a list of {title, url, text, type} for UI display (non-video-platform links only)."""
    items: list[dict[str, Any]] = []
    summary = sanitize_text_urls((summary_text or "").strip(), exclude_urls=exclude_urls)
    search = sanitize_text_urls((search_context or "").strip(), exclude_urls=exclude_urls)

    if summary:
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", summary) if p.strip()]
        if not paragraphs:
            paragraphs = [summary]
        summary_urls = filter_allowed_urls(_unique_urls(summary), exclude_urls=exclude_urls)
        for index, paragraph in enumerate(paragraphs, start=1):
            url = ""
            if summary_urls:
                url = summary_urls[min(index - 1, len(summary_urls) - 1)]
            items.append(
                {
                    "id": len(items) + 1,
                    "title": video_title if index == 1 else f"{video_title} — đoạn {index}",
                    "url": url,
                    "text": paragraph,
                    "type": "summary",
                }
            )

    for url in filter_allowed_urls(_unique_urls(search), exclude_urls=exclude_urls):
        if any(item.get("url") == url for item in items):
            continue
        snippet = search
        if len(snippet) > 400:
            snippet = snippet[:400] + "…"
        items.append(
            {
                "id": len(items) + 1,
                "title": f"Tham khảo · {_domain(url)}",
                "url": url,
                "text": snippet or f"Liên kết liên quan đến «{video_title}».",
                "type": "reference",
            }
        )

    if extra_items:
        for extra in extra_items:
            extra = dict(extra)
            extra["id"] = len(items) + 1
            items.append(extra)

    return items


def save_trend_content(
    trend_root: str | Path,
    items: list[dict[str, Any]],
) -> Path:
    root = Path(trend_root)
    path = root / "trend_content.json"
    path.write_text(
        json.dumps(items, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return path


def load_trend_content(trend_root: str | Path) -> list[dict[str, Any]]:
    path = Path(trend_root) / "trend_content.json"
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return [
                    item
                    for item in data
                    if item.get("type") == "transcript"
                    or not is_video_platform_url(item.get("url") or "")
                ]
        except json.JSONDecodeError:
            pass

    trend_info = Path(trend_root) / "trend_info.txt"
    if trend_info.is_file():
        text = trend_info.read_text(encoding="utf-8").strip()
        if text:
            return build_text_items(
                video_title="Tóm tắt trend",
                summary_text=text,
            )
    return []


def append_transcript_item(
    trend_root: str | Path,
    transcript: str,
    source_url: str = "",
    source_title: str = "Transcript video",
) -> None:
    items = load_trend_content(trend_root)
    if not transcript.strip():
        return
    if any(item.get("type") == "transcript" for item in items):
        return
    items.append(
        {
            "id": len(items) + 1,
            "title": source_title,
            "url": source_url,
            "text": transcript.strip(),
            "type": "transcript",
        }
    )
    save_trend_content(trend_root, items)
