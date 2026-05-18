"""Structured text+link items for trend context display."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

_URL_RE = re.compile(r"https?://[^\s\)\]\"\'<>]+")


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
) -> list[dict[str, Any]]:
    """Build a list of {title, url, text, type} for UI display."""
    items: list[dict[str, Any]] = []
    summary = (summary_text or "").strip()
    search = (search_context or "").strip()

    if summary:
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", summary) if p.strip()]
        if not paragraphs:
            paragraphs = [summary]
        summary_urls = _unique_urls(summary)
        for index, paragraph in enumerate(paragraphs, start=1):
            url = summary_urls[index - 1] if index - 1 < len(summary_urls) else (
                summary_urls[0] if summary_urls else ""
            )
            items.append(
                {
                    "id": len(items) + 1,
                    "title": video_title if index == 1 else f"{video_title} — đoạn {index}",
                    "url": url,
                    "text": paragraph,
                    "type": "summary",
                }
            )

    for url in _unique_urls(search):
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
            return data if isinstance(data, list) else []
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
