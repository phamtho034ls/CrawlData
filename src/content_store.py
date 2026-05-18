"""Persist trend data as links and text (no binary media download)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def save_video_links(videos: list[dict[str, Any]], trend_root: str | Path) -> dict[str, str]:
    """Save scraped videos as JSON, Markdown, and plain-text link files."""
    root = Path(trend_root)
    root.mkdir(parents=True, exist_ok=True)
    videos_dir = root / "Videos"
    videos_dir.mkdir(parents=True, exist_ok=True)

    json_path = root / "video_links.json"
    md_path = root / "video_links.md"
    txt_path = videos_dir / "video_links.txt"

    json_path.write_text(
        json.dumps(videos, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    md_lines = [f"# Video links ({len(videos)} items)\n"]
    txt_lines = []
    for index, video in enumerate(videos, start=1):
        title = video.get("title") or "Untitled"
        url = video.get("url") or ""
        platform = video.get("platform") or "unknown"
        views = video.get("view_count")
        view_label = f"{views:,}" if isinstance(views, int) else "N/A"
        md_lines.append(
            f"{index}. **[{title}]({url})**  \n"
            f"   - Platform: `{platform}` | Views: {view_label} | ID: `{video.get('video_id', '')}`\n"
        )
        txt_lines.append(f"{index}. {title}\n   {url}\n   [{platform}] views={view_label}\n")

    md_path.write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    txt_path.write_text("\n".join(txt_lines) + "\n", encoding="utf-8")

    return {
        "video_links_json": str(json_path.resolve()),
        "video_links_md": str(md_path.resolve()),
        "video_links_txt": str(txt_path.resolve()),
    }


def load_video_links(trend_root: str | Path) -> list[dict[str, Any]]:
    json_path = Path(trend_root) / "video_links.json"
    if not json_path.is_file():
        return []
    return json.loads(json_path.read_text(encoding="utf-8"))


def read_text_file(path: str | Path, default: str = "") -> str:
    file_path = Path(path)
    if not file_path.is_file():
        return default
    return file_path.read_text(encoding="utf-8", errors="replace").strip()
