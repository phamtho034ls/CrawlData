"""Prefect orchestration for the AI content data pipeline."""

from __future__ import annotations

from src.prefect_bootstrap import configure_prefect

configure_prefect()

import json
from pathlib import Path
from typing import Any, Literal

from prefect import flow, task

from src.content_store import save_video_links
from src.context_agent import enrich_trend_context
from src.device_utils import gpu_status_message
from src.media_processor import download_video, extract_transcript
from src.pipeline_logging import setup_logging
from src.storage_manager import StorageManager, StorageManagerError
from src.trend_content import append_transcript_item
from src.trend_reader import upsert_trend_index
from src.trend_scraper import get_trending_videos

logger = setup_logging()

PipelineMode = Literal["links", "full"]


@task(name="setup_storage", log_prints=True)
def setup_storage_task(keyword: str) -> dict[str, str]:
    manager = StorageManager(trend_topic=keyword)
    try:
        paths = manager.resolve_structure()
    except StorageManagerError as exc:
        logger.error("%s", exc)
        raise
    logger.info("Storage ready at %s", paths["trend_root"])
    return paths


@task(name="scrape_trends", log_prints=True)
def scrape_trends_task(keyword: str, limit: int = 5) -> list[dict[str, Any]]:
    videos = get_trending_videos(keyword=keyword, limit=limit)
    logger.info("Scraped %d trending videos for keyword '%s'", len(videos), keyword)
    return videos


@task(name="save_video_links", log_prints=True)
def save_video_links_task(
    videos: list[dict[str, Any]], trend_root: str
) -> dict[str, str]:
    paths = save_video_links(videos, trend_root)
    logger.info("Video links saved to %s", paths["video_links_json"])
    return paths


@task(name="enrich_context", log_prints=True)
def enrich_context_task(video_title: str, trend_root: str) -> str:
    output = enrich_trend_context(video_title=video_title, trend_folder=trend_root)
    logger.info("Context saved to %s", output)
    return str(output)


@task(name="download_media", log_prints=True)
def download_media_task(video_url: str, videos_dir: str) -> str:
    downloaded = download_video(url=video_url, output_path=videos_dir)
    logger.info("Video downloaded to %s", downloaded)
    return str(downloaded)


@task(name="extract_transcript", log_prints=True)
def extract_transcript_task(video_path: str, trend_root: str) -> str:
    transcript_path = Path(trend_root) / "transcript.txt"
    if transcript_path.exists() and transcript_path.stat().st_size > 0:
        logger.info("Reusing existing transcript at %s", transcript_path)
        return str(transcript_path.resolve())

    output = extract_transcript(
        video_path=video_path,
        output_text_path=transcript_path,
    )
    logger.info("Transcript saved to %s", output)
    return str(output)


@flow(name="ai_content_pipeline", log_prints=True)
def run_pipeline(
    keyword: str,
    video_limit: int = 5,
    mode: PipelineMode = "links",
) -> dict[str, Any]:
    """
    Orchestrated workflow.

    mode=links (default): storage + scrape + save links + enrich text (fast, no download).
    mode=full: also download video file and run Whisper transcript.
    """
    logger.info("%s", gpu_status_message())
    paths = setup_storage_task(keyword)
    videos = scrape_trends_task(keyword, limit=video_limit)

    if not videos:
        raise RuntimeError(f"No trending videos found for keyword: {keyword}")

    link_paths = save_video_links_task(videos, paths["trend_root"])

    primary = videos[0]
    title = primary.get("title") or keyword
    trend_info_path = enrich_context_task(
        video_title=title, trend_root=paths["trend_root"]
    )

    result: dict[str, Any] = {
        "mode": mode,
        "keyword": keyword,
        "trend_root": paths["trend_root"],
        "videos_dir": paths["videos_dir"],
        "images_dir": paths["images_dir"],
        "scraped_videos": videos,
        "link_files": link_paths,
        "trend_info_path": trend_info_path,
        "downloaded_video": None,
        "transcript_path": None,
    }

    if mode == "full":
        url = primary.get("url")
        if not url:
            raise RuntimeError("Primary video has no URL; cannot download media.")
        video_path = download_media_task(video_url=url, videos_dir=paths["videos_dir"])
        transcript_path = extract_transcript_task(
            video_path=video_path,
            trend_root=paths["trend_root"],
        )
        result["downloaded_video"] = video_path
        result["transcript_path"] = transcript_path
        transcript_text = Path(transcript_path).read_text(encoding="utf-8")
        append_transcript_item(
            trend_root=paths["trend_root"],
            transcript=transcript_text,
            source_url=url,
            source_title=title,
        )

    summary_path = Path(paths["trend_root"]) / "pipeline_summary.json"
    summary_path.write_text(
        json.dumps(result, indent=2, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )
    result["summary_path"] = str(summary_path.resolve())
    upsert_trend_index(
        trend_root=paths["trend_root"],
        keyword=keyword,
        video_count=len(videos),
    )

    logger.info("Pipeline completed (%s) for %s", mode, paths["trend_root"])
    return result


if __name__ == "__main__":
    import sys

    kw = sys.argv[1] if len(sys.argv) > 1 else "AI tools"
    pipeline_mode: PipelineMode = "full" if "--full" in sys.argv else "links"
    output = run_pipeline(keyword=kw, mode=pipeline_mode)
    print(output)
