from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.localization_pipeline import run_localization_pipeline


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run localization pipeline on an existing data_trends folder."
    )
    parser.add_argument(
        "--trend-root",
        required=True,
        help="Path to data_trends/<date>_Topic_<keyword> folder",
    )
    parser.add_argument(
        "--langs",
        default="vi",
        help="Comma-separated target languages, e.g. vi,th,id",
    )
    parser.add_argument(
        "--profile",
        default="short_vi_60s",
        help="Localization profile name",
    )
    parser.add_argument(
        "--max-videos",
        type=int,
        default=0,
        help="0 means all videos that have Content/<video_id>.json",
    )
    parser.add_argument(
        "--no-render",
        action="store_true",
        help="Skip ffmpeg render step (produce translated/rewrite/edit-plan only)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    langs = [lang.strip() for lang in args.langs.split(",") if lang.strip()]
    result = run_localization_pipeline(
        trend_root=args.trend_root,
        target_langs=langs,
        profile_name=args.profile,
        render=not args.no_render,
        max_videos=int(args.max_videos),
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
