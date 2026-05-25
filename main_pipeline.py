"""Prefect orchestration for the AI content data pipeline."""

from __future__ import annotations

from src.prefect_bootstrap import configure_prefect

configure_prefect()

import json
import os
from pathlib import Path
from typing import Any, Literal

from prefect import flow, get_run_logger, task

from src.article_scraper import download_web_images, scrape_article_data
from src.content_store import save_video_links
from src.context_synthesizer import generate_trend_context
from src.device_utils import gpu_status_message
from src.trend_content import append_transcript_item, build_text_items, save_trend_content
from src.audio_transcriber import extract_audio_from_video, transcribe_audio
from src.image_extractor import extract_video_keyframes
from src.video_downloader import download_trend_video
from src.video_minute_splitter import (
    process_video_minute_split,
    transcript_text_from_minute_payload,
)
from src.web_searcher import search_trend_articles
from src.pipeline_logging import setup_logging
from src.pipeline_progress import PipelineProgress
from src.scraper_config import ScraperConfig
from src.storage_manager import StorageManager, StorageManagerError
from src.trend_reader import upsert_trend_index
from src.trend_scraper import collect_videos_for_topic

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


@task(name="save_video_links", log_prints=True)
def save_video_links_task(
    videos: list[dict[str, Any]], trend_root: str
) -> dict[str, str]:
    paths = save_video_links(videos, trend_root)
    logger.info("Video links saved to %s", paths["video_links_json"])
    return paths


@task(name="process_trend_context", log_prints=True)
def process_trend_context(
    keyword: str,
    folder_paths: dict[str, str],
    *,
    max_search_results: int = 3,
) -> dict[str, Any]:
    """
    Module 2: search articles → scrape → LLM summary → trend_info.txt + web images.
    """
    trend_root = folder_paths["trend_root"]
    images_dir = folder_paths["images_dir"]
    trend_info_path = Path(trend_root) / "trend_info.txt"

    search_hits = search_trend_articles(keyword, max_results=max_search_results)
    scraped_articles: list[dict[str, Any]] = []

    for hit in search_hits:
        url = hit.get("href") or ""
        if not url:
            continue
        logger.info("Scraping article: %s", url)
        data = scrape_article_data(url)
        if not data.get("main_text"):
            logger.debug("Skipping empty article: %s", url)
            continue
        scraped_articles.append(
            {
                "title": hit.get("title") or url,
                "href": url,
                "url": url,
                "body": hit.get("body") or "",
                "main_text": data.get("main_text") or "",
                "og_image": data.get("og_image") or "",
                "other_images": data.get("other_images") or [],
            }
        )

    summary = generate_trend_context(keyword, scraped_articles)
    trend_info_path.write_text(summary + "\n", encoding="utf-8")
    logger.info("Trend summary saved to %s", trend_info_path)

    image_paths = download_web_images(scraped_articles, images_dir)
    logger.info("Downloaded %d web images to %s", len(image_paths), images_dir)

    extra_items = [
        {
            "title": art.get("title") or art.get("url") or "Article",
            "url": art.get("url") or art.get("href") or "",
            "text": (art.get("main_text") or "")[:2000],
            "type": "article",
        }
        for art in scraped_articles
    ]
    save_trend_content(
        trend_root,
        build_text_items(keyword, summary, search_context="", extra_items=extra_items),
    )

    return {
        "trend_info_path": str(trend_info_path.resolve()),
        "image_paths": image_paths,
        "articles_scraped": len(scraped_articles),
        "search_hits": len(search_hits),
    }


def _platform_from_trend_item(item: dict[str, Any]) -> str:
    platform = (item.get("platform") or "").lower()
    if platform in {"youtube", "tiktok"}:
        return platform
    url = (item.get("url") or "").lower()
    if "tiktok.com" in url:
        return "tiktok"
    return "youtube"


@task(name="process_media_assets", log_prints=True)
def process_media_assets(
    trend_data_list: list[dict[str, Any]],
    folder_paths: dict[str, str],
    *,
    max_videos: int | None = 3,
    progress: PipelineProgress | None = None,
) -> dict[str, Any]:
    """
    Module 3: download videos → minute JSON + clips → transcript → keyframes.
    """
    log = get_run_logger()
    videos_dir = folder_paths["videos_dir"]
    images_dir = folder_paths["images_dir"]
    trend_root = Path(folder_paths["trend_root"])
    transcript_path = trend_root / "transcript.txt"

    ranked = sorted(
        trend_data_list,
        key=lambda v: v.get("view_count") if isinstance(v.get("view_count"), int) else 0,
        reverse=True,
    )
    if max_videos is not None and max_videos > 0:
        ranked = ranked[:max_videos]

    total_videos = len(ranked)
    if progress and total_videos:
        progress.start_media_module(total_videos)

    processed: list[dict[str, Any]] = []
    transcript_sections: list[str] = []
    all_keyframes: list[str] = []
    minute_content_files: list[str] = []
    minute_clips_dirs: list[str] = []
    minute_split_enabled = os.getenv("PIPELINE_MINUTE_SPLIT", "1").strip().lower() not in {
        "0",
        "false",
        "no",
    }

    for index, item in enumerate(ranked, start=1):
        url = (item.get("url") or "").strip()
        title = item.get("title") or f"Video {index}"
        if not url:
            log.warning("Skipping item %d: no URL", index)
            continue

        platform = _platform_from_trend_item(item)
        source_keyword = item.get("source_keyword") or ""
        if progress:
            progress.begin_media_video(
                index,
                total_videos,
                title=title,
                url=url,
                platform=platform,
                source_keyword=source_keyword,
            )

        log.info("[%d/%d] Downloading %s: %s", index, len(ranked), platform, url)

        video_path = download_trend_video(url, videos_dir, platform=platform)

        if not video_path or not Path(video_path).is_file():
            log.warning("Download failed (no file on disk): %s", url)
            processed.append(
                {
                    "url": url,
                    "platform": platform,
                    "status": "download_failed",
                    "error": "no_file",
                }
            )
            if progress:
                progress.complete_media_video(
                    index,
                    {
                        "status": "download_failed",
                        "phase_message": "Không tải được file video",
                    },
                )
            continue

        log.info("Downloaded to %s (%s bytes)", video_path, Path(video_path).stat().st_size)
        if progress:
            progress.set_media_phase(
                index,
                "downloading",
                f"Đã tải xong ({Path(video_path).stat().st_size // 1024} KB)",
                total=total_videos,
            )

        content_json_path: str | None = None
        clips_dir: str | None = None
        minute_count = 0
        transcript_ok = False

        if minute_split_enabled:
            try:
                if progress:
                    progress.set_media_phase(
                        index,
                        "analyzing",
                        "Speech-to-text → JSON theo phút → cắt clip…",
                        total=total_videos,
                    )
                log.info("Minute split: speech-to-text JSON + per-minute clips")
                minute_payload = process_video_minute_split(
                    video_path,
                    trend_root=trend_root,
                    language=None,
                )
                content_json_path = minute_payload.get("content_json")
                clips_dir = minute_payload.get("clips_dir")
                minute_count = len(minute_payload.get("minutes") or [])
                if content_json_path:
                    minute_content_files.append(content_json_path)
                if clips_dir:
                    minute_clips_dirs.append(clips_dir)
                segment_text = transcript_text_from_minute_payload(minute_payload)
                transcript_sections.append(
                    f"## Video {index}: {title}\nURL: {url}\n\n{segment_text}"
                )
                transcript_ok = True
                log.info(
                    "Minute content: %s (%d phút, clips → %s)",
                    content_json_path,
                    minute_count,
                    clips_dir,
                )
            except Exception as exc:
                log.warning("Minute split failed for %s: %s", url, exc)
                if progress:
                    progress.set_media_phase(
                        index,
                        "analyze_failed",
                        f"Phân tích theo phút lỗi: {exc}",
                        total=total_videos,
                    )

        if not transcript_ok:
            if progress:
                progress.set_media_phase(
                    index,
                    "analyzing",
                    "Fallback: transcribe toàn bộ (transcript.txt)…",
                    total=total_videos,
                )
            audio_path = Path(videos_dir) / f"_audio_{index}.wav"
            try:
                log.info("Fallback transcript → %s", audio_path.name)
                extract_audio_from_video(video_path, audio_path)
                transcribe_audio(audio_path, transcript_path)
                segment_text = transcript_path.read_text(encoding="utf-8").strip()
                transcript_sections.append(
                    f"## Video {index}: {title}\nURL: {url}\n\n{segment_text}"
                )
            except Exception as exc:
                log.warning("Audio/transcription failed for %s: %s", url, exc)
            finally:
                if audio_path.is_file():
                    audio_path.unlink(missing_ok=True)

        try:
            if progress:
                progress.set_media_phase(
                    index,
                    "keyframes",
                    "Trích keyframe (scene detection)…",
                    total=total_videos,
                )
            log.info("Extracting keyframes → %s", images_dir)
            keyframes = extract_video_keyframes(video_path, images_dir)
            all_keyframes.extend(keyframes)
            log.info("Saved %d keyframes", len(keyframes))
        except Exception as exc:
            log.warning("Keyframe extraction failed for %s: %s", url, exc)
            keyframes = []

        final_status = "ok" if transcript_ok else "analyze_failed"
        phase_msg = (
            f"Xong: {minute_count} phút JSON, {len(keyframes)} keyframes"
            if transcript_ok
            else "Tải OK; transcript/phân tích chưa đủ"
        )
        processed.append(
            {
                "url": url,
                "platform": platform,
                "title": title,
                "video_path": video_path,
                "keyframes": keyframes,
                "content_json": content_json_path,
                "clips_dir": clips_dir,
                "minute_count": minute_count,
                "status": "ok",
            }
        )
        if progress:
            progress.complete_media_video(
                index,
                {
                    "status": "done" if transcript_ok else "analyze_failed",
                    "phase_message": phase_msg,
                    "video_path": video_path,
                    "content_json": content_json_path or "",
                    "clips_dir": clips_dir or "",
                    "minute_count": minute_count,
                    "keyframes_count": len(keyframes),
                },
            )

    if progress and total_videos:
        downloaded_rows = [
            {
                "title": item.get("title") or "Video",
                "url": item.get("url") or "",
                "platform": item.get("platform") or "",
                "video_path": item.get("video_path") or "",
                "source_keyword": next(
                    (
                        v.get("source_keyword")
                        for v in trend_data_list
                        if v.get("url") == item.get("url")
                    ),
                    "",
                ),
                "content_json": item.get("content_json") or "",
                "clips_dir": item.get("clips_dir") or "",
                "minute_count": item.get("minute_count") or 0,
            }
            for item in processed
            if item.get("status") == "ok"
        ]
        progress.finish_media_module(downloaded_rows)

    if transcript_sections:
        combined = "\n\n---\n\n".join(transcript_sections) + "\n"
        transcript_path.write_text(combined, encoding="utf-8")
        log.info("Combined transcript saved to %s", transcript_path)
    elif not transcript_path.exists():
        transcript_path.write_text("", encoding="utf-8")

    ok_count = sum(1 for p in processed if p.get("status") == "ok")
    log.info(
        "Module 3 complete: %d/%d videos downloaded, %d keyframes → %s",
        ok_count,
        len(ranked),
        len(all_keyframes),
        videos_dir,
    )
    if ranked and ok_count == 0:
        log.error(
            "No videos downloaded. Check: mode=Full+Whisper, ffmpeg on PATH. "
            "If YTDLP_COOKIES_FROM_BROWSER is set, close Chrome or leave it empty."
        )

    return {
        "processed": processed,
        "transcript_path": str(transcript_path.resolve()),
        "keyframe_paths": all_keyframes,
        "minute_content_files": minute_content_files,
        "minute_clips_dirs": minute_clips_dirs,
        "minute_split_enabled": minute_split_enabled,
        "videos_attempted": len(ranked),
        "videos_downloaded": ok_count,
    }


@flow(name="ai_content_pipeline", log_prints=True)
def run_pipeline(
    keyword: str,
    mode: PipelineMode = "links",
    scraper_config: ScraperConfig | None = None,
    progress: PipelineProgress | None = None,
    *,
    pre_scraped_videos: list[dict[str, Any]] | None = None,
    pre_expanded_keywords: list[str] | None = None,
    **config_overrides: int,
) -> dict[str, Any]:
    """
    Orchestrated workflow.

    mode=links: storage + multi-keyword scrape + save links + enrich text.
    mode=full: also download top video + Whisper transcript.

    When pre_scraped_videos is set, skips scraping and uses that video list.
    When only pre_expanded_keywords is set, scrapes with those keywords (AI Forecaster).
    """
    config = scraper_config or ScraperConfig.from_env(**config_overrides)

    use_prefetched_videos = pre_scraped_videos is not None
    forecaster_keywords = (
        list(pre_expanded_keywords)
        if pre_expanded_keywords and not use_prefetched_videos
        else None
    )
    if use_prefetched_videos:
        scrape_steps = 0
    elif forecaster_keywords:
        scrape_steps = len(forecaster_keywords)
    else:
        scrape_steps = config.keyword_count
    total_steps = 3 + scrape_steps + (1 if mode == "full" else 0)
    if progress:
        progress.start(total_steps, "Bắt đầu pipeline")

    logger.info("%s", gpu_status_message())
    if progress:
        progress.step("Thiết lập kho lưu trữ")
    paths = setup_storage_task(keyword)

    if use_prefetched_videos:
        videos = list(pre_scraped_videos or [])
        expanded_keywords = list(pre_expanded_keywords or [])
        if not videos:
            raise ValueError("pre_scraped_videos is empty")
        if progress and expanded_keywords:
            progress.set_keywords(expanded_keywords)
            for index, kw in enumerate(expanded_keywords, start=1):
                count = sum(1 for v in videos if v.get("source_keyword") == kw)
                progress.begin_keyword_search(kw, index, len(expanded_keywords), step=2 + index)
                progress.complete_keyword_search(kw, count)
    else:
        videos, expanded_keywords = collect_videos_for_topic(
            keyword,
            config,
            progress,
            keywords=forecaster_keywords,
        )

    if progress:
        progress.step("Lưu link & metadata video")
    link_paths = save_video_links_task(videos, paths["trend_root"])

    keywords_path = Path(paths["trend_root"]) / "expanded_keywords.json"
    keywords_path.write_text(
        json.dumps(
            {"topic": keyword, "keywords": expanded_keywords},
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    context_keyword = (expanded_keywords[0] if expanded_keywords else keyword).strip()
    if progress:
        progress.step("Module 2: tìm bài viết & tổng hợp ngữ cảnh (LLM)")
    context_result = process_trend_context(
        context_keyword,
        paths,
        max_search_results=3,
    )
    trend_info_path = context_result["trend_info_path"]

    primary = videos[0] if videos else {}
    title = primary.get("title") or keyword

    result: dict[str, Any] = {
        "mode": mode,
        "keyword": keyword,
        "scraper_config": {
            "recency_days": config.recency_days,
            "min_views": config.min_views,
            "keyword_count": config.keyword_count,
            "videos_per_platform": config.videos_per_platform,
            "videos_per_keyword_search": config.videos_per_keyword_search,
            "top_videos_per_keyword": config.top_videos_per_keyword,
        },
        "expanded_keywords": expanded_keywords,
        "trend_root": paths["trend_root"],
        "videos_dir": paths["videos_dir"],
        "images_dir": paths["images_dir"],
        "scraped_videos": videos,
        "link_files": link_paths,
        "trend_info_path": trend_info_path,
        "context_module": context_result,
        "web_image_paths": context_result.get("image_paths") or [],
        "downloaded_video": None,
        "transcript_path": None,
        "media_module": None,
    }

    if mode == "full":
        if progress:
            progress.step("Module 3: tải video, transcript, keyframes")
        media_result = process_media_assets(
            videos,
            paths,
            max_videos=3,
            progress=progress,
        )
        result["media_module"] = media_result
        result["transcript_path"] = media_result.get("transcript_path")
        ok_videos = [
            p for p in media_result.get("processed") or [] if p.get("status") == "ok"
        ]
        if ok_videos:
            result["downloaded_video"] = ok_videos[0].get("video_path")
            transcript_text = Path(media_result["transcript_path"]).read_text(
                encoding="utf-8"
            )
            append_transcript_item(
                trend_root=paths["trend_root"],
                transcript=transcript_text,
                source_url=ok_videos[0].get("url") or "",
                source_title=ok_videos[0].get("title") or title,
            )
        else:
            logger.warning(
                "Module 3: no videos were downloaded (%s attempted). See logs.",
                media_result.get("videos_attempted", 0),
            )
            result["media_download_error"] = (
                "Không tải được video. Chọn Full + Whisper, cài ffmpeg, "
                "hoặc để trống YTDLP_COOKIES_FROM_BROWSER nếu Chrome đang mở."
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

    if progress:
        progress.finish("Hoàn tất pipeline")

    logger.info("Pipeline completed (%s) for %s", mode, paths["trend_root"])
    return result


if __name__ == "__main__":
    import sys

    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}

    kw = args[0] if args else "AI tools"
    pipeline_mode: PipelineMode = "full" if "--full" in flags else "links"

    output = run_pipeline(keyword=kw, mode=pipeline_mode)
    print(output)
