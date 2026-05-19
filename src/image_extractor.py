"""Extract keyframes from video using FFmpeg scene detection."""

from __future__ import annotations

import logging
import re
from pathlib import Path

import ffmpeg

logger = logging.getLogger(__name__)

_SCENE_THRESHOLD = 0.4


def extract_video_keyframes(
    video_path: str | Path,
    output_image_folder: str | Path,
) -> list[str]:
    """
    Detect scene changes (select='gt(scene,0.4)') and save frames as keyframe_XX.jpg.
    Returns absolute paths of extracted images.
    """
    video_file = Path(video_path)
    if not video_file.is_file():
        raise FileNotFoundError(f"Video not found: {video_file}")

    out_dir = Path(output_image_folder)
    out_dir.mkdir(parents=True, exist_ok=True)

    pattern = str(out_dir / "keyframe_%02d.jpg")

    try:
        (
            ffmpeg.input(str(video_file))
            .filter("select", f"gt(scene,{_SCENE_THRESHOLD})")
            .filter("scale", 1920, -1)
            .output(pattern, vsync="vfr", qscale=2)
            .overwrite_output()
            .run(capture_stdout=True, capture_stderr=True)
        )
    except ffmpeg.Error as exc:
        stderr = exc.stderr.decode(errors="replace") if exc.stderr else str(exc)
        logger.warning("Scene detection failed (%s); saving first frame fallback.", stderr)
        fallback = out_dir / "keyframe_01.jpg"
        (
            ffmpeg.input(str(video_file), ss=0)
            .output(str(fallback), vframes=1, qscale=2)
            .overwrite_output()
            .run(capture_stdout=True, capture_stderr=True)
        )
        return [str(fallback.resolve())] if fallback.is_file() else []

    paths = sorted(out_dir.glob("keyframe_*.jpg"), key=_keyframe_sort_key)
    resolved = [str(p.resolve()) for p in paths if p.is_file()]
    logger.info("Extracted %d keyframes to %s", len(resolved), out_dir)
    return resolved


def _keyframe_sort_key(path: Path) -> int:
    match = re.search(r"keyframe_(\d+)", path.stem)
    return int(match.group(1)) if match else 0
