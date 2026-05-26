from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.content_store import load_videos_for_trend
from main_pipeline import run_pipeline

TREND_ROOT = Path(
    r"D:/DSAI/code/DataCrawl/data_trends/2026-05-26_Topic_Claude_AI"
)


def main() -> None:
    videos = load_videos_for_trend(TREND_ROOT)
    print(f"Loaded videos: {len(videos)}")
    result = run_pipeline(
        keyword="Claude AI",
        mode="full",
        existing_trend_root=TREND_ROOT,
        module3_only=True,
        max_videos=0,
    )
    media = result.get("media_module") or {}
    print(f"Summary path: {result.get('summary_path')}")
    print(f"Videos attempted: {media.get('videos_attempted')}")
    print(f"Videos downloaded: {media.get('videos_downloaded')}")

    for idx, item in enumerate(media.get("processed") or [], start=1):
        print(
            f"{idx}. status={item.get('status')} title={item.get('title')} "
            f"minute_count={item.get('minute_count')} video_path={item.get('video_path')}"
        )


if __name__ == "__main__":
    main()
