"""Process downloaded test videos: JSON nội dung theo phút + clip video."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.video_minute_splitter import process_video_minute_split

TEST_TREND_FOLDERS = [
    ROOT / "data_trends" / "2026-05-25_Topic_tiktok_video_test",
    ROOT / "data_trends" / "2026-05-25_Topic_long_video_test",
]


def main() -> None:
    targets: list[Path] = []
    for folder in TEST_TREND_FOLDERS:
        videos_dir = folder / "Videos"
        if not videos_dir.is_dir():
            continue
        for mp4 in sorted(videos_dir.glob("*.mp4")):
            if mp4.parent.name == "clips":
                continue
            targets.append(mp4)

    if not targets:
        print("No test MP4 found under data_trends/*_test*/Videos/")
        sys.exit(1)

    for video in targets:
        print(f"\n=== Processing {video.name} ===")
        result = process_video_minute_split(video, trend_root=video.parent.parent)
        print(f"JSON:  {result['content_json']}")
        print(f"Clips: {result['clips_dir']}")
        print(f"Minutes: {len(result['minutes'])}")


if __name__ == "__main__":
    main()
