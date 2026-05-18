"""Read saved trend folders for the dashboard."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from src.content_store import load_video_links, read_text_file
from src.trend_content import load_trend_content

DATA_TRENDS_DIR = Path(__file__).resolve().parent.parent / "data_trends"
TRENDS_INDEX_FILE = DATA_TRENDS_DIR / "trends_index.json"


def parse_trend_folder_name(folder_name: str) -> dict[str, str]:
    """Parse folder name like 2026-05-18_Topic_AI_tools."""
    if "_Topic_" in folder_name:
        date_part, topic_part = folder_name.split("_Topic_", 1)
        topic = topic_part.replace("_", " ")
        return {"date": date_part, "topic": topic, "label": f"{date_part} · {topic}"}
    return {"date": "", "topic": folder_name, "label": folder_name}


def list_trend_folders(base_dir: Path | None = None) -> list[Path]:
    root = base_dir or DATA_TRENDS_DIR
    if not root.is_dir():
        return []
    folders = [
        path
        for path in root.iterdir()
        if path.is_dir() and not path.name.startswith(".")
    ]
    return sorted(folders, key=lambda p: p.stat().st_mtime, reverse=True)


def load_trends_index() -> list[dict[str, Any]]:
    if not TRENDS_INDEX_FILE.is_file():
        return []
    try:
        data = json.loads(TRENDS_INDEX_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


def save_trends_index(entries: list[dict[str, Any]]) -> None:
    DATA_TRENDS_DIR.mkdir(parents=True, exist_ok=True)
    TRENDS_INDEX_FILE.write_text(
        json.dumps(entries, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def upsert_trend_index(
    trend_root: str | Path,
    keyword: str,
    video_count: int,
) -> None:
    """Maintain trends_index.json — master list of all stored trends."""
    root = Path(trend_root)
    folder_name = root.name
    parsed = parse_trend_folder_name(folder_name)
    entries = load_trends_index()
    entry = {
        "id": folder_name,
        "folder_name": folder_name,
        "keyword": keyword,
        "date": parsed["date"],
        "topic": parsed["topic"],
        "label": parsed["label"],
        "video_count": video_count,
        "trend_root": str(root.resolve()),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    entries = [e for e in entries if e.get("id") != folder_name]
    entries.insert(0, entry)
    save_trends_index(entries)


def load_trend_summary(trend_root: str | Path) -> dict[str, Any]:
    root = Path(trend_root)
    videos = load_video_links(root)

    if not videos:
        summary_file = root / "pipeline_summary.json"
        if summary_file.is_file():
            try:
                payload = json.loads(summary_file.read_text(encoding="utf-8"))
                videos = payload.get("scraped_videos") or []
            except json.JSONDecodeError:
                pass

    trend_info = read_text_file(root / "trend_info.txt")
    transcript = read_text_file(root / "transcript.txt")
    parsed = parse_trend_folder_name(root.name)
    modified = datetime.fromtimestamp(root.stat().st_mtime).strftime("%Y-%m-%d %H:%M")

    text_items = load_trend_content(root)

    return {
        "id": root.name,
        "folder_name": root.name,
        "trend_root": str(root.resolve()),
        "date": parsed["date"],
        "topic": parsed["topic"],
        "label": parsed["label"],
        "video_count": len(videos),
        "videos": videos,
        "text_items": text_items,
        "text_item_count": len(text_items),
        "trend_info": trend_info,
        "transcript": transcript,
        "has_trend_info": bool(trend_info),
        "has_transcript": bool(transcript),
        "updated_at": modified,
    }


def rebuild_trends_index_from_disk() -> None:
    """Build trends_index.json from existing folders (migration / first load)."""
    if load_trends_index():
        return
    for folder in list_trend_folders():
        summary = load_trend_summary(folder)
        upsert_trend_index(
            trend_root=folder,
            keyword=summary.get("topic") or summary["folder_name"],
            video_count=summary["video_count"],
        )


def list_all_trends() -> list[dict[str, Any]]:
    """
    Return all trends as a list (newest first).
    Merges trends_index.json with on-disk folders.
    """
    rebuild_trends_index_from_disk()
    summaries: dict[str, dict[str, Any]] = {}

    for folder in list_trend_folders():
        summary = load_trend_summary(folder)
        summaries[summary["id"]] = summary

    for entry in load_trends_index():
        trend_id = entry.get("id") or entry.get("folder_name")
        if not trend_id:
            continue
        if trend_id in summaries:
            summaries[trend_id].update(
                {
                    "keyword": entry.get("keyword", summaries[trend_id].get("topic", "")),
                    "updated_at": entry.get("updated_at", summaries[trend_id]["updated_at"]),
                }
            )
        else:
            root = Path(entry.get("trend_root", DATA_TRENDS_DIR / trend_id))
            if root.is_dir():
                summaries[trend_id] = load_trend_summary(root)

    def sort_key(item: dict[str, Any]) -> str:
        return item.get("updated_at") or item.get("date") or ""

    return sorted(summaries.values(), key=sort_key, reverse=True)
