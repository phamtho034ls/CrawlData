"""Speech-to-text by minute (JSON) + split source video into per-minute clips."""

from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from typing import Any

import ffmpeg

from src.audio_transcriber import extract_audio_from_video, transcribe_audio_segments

logger = logging.getLogger(__name__)


def _minute_label(minute_index: int) -> str:
    """1-based minute label for JSON keys."""
    return f"phút thứ {minute_index}"


def get_video_duration_seconds(video_path: str | Path) -> float:
    probe = ffmpeg.probe(str(video_path))
    duration = probe.get("format", {}).get("duration")
    if duration is None:
        raise RuntimeError(f"Could not read duration for {video_path}")
    return float(duration)


def group_segments_by_minute(
    segments: list[dict[str, Any]],
    *,
    duration_seconds: float,
) -> list[dict[str, Any]]:
    """Merge whisper segments into 1-based minute buckets."""
    total_minutes = max(1, math.ceil(duration_seconds / 60.0))
    buckets: list[dict[str, Any]] = [
        {
            "minute": m,
            "label": _minute_label(m),
            "start_seconds": (m - 1) * 60,
            "end_seconds": min(m * 60, duration_seconds),
            "text": "",
            "segments": [],
        }
        for m in range(1, total_minutes + 1)
    ]

    for seg in segments:
        minute_idx = int(seg["start"] // 60) + 1
        if minute_idx < 1:
            minute_idx = 1
        if minute_idx > len(buckets):
            minute_idx = len(buckets)
        bucket = buckets[minute_idx - 1]
        bucket["segments"].append(seg)
        piece = seg["text"].strip()
        if piece:
            bucket["text"] = f"{bucket['text']} {piece}".strip()

    return buckets


def build_minute_content_json(
    video_path: str | Path,
    segments: list[dict[str, Any]],
    *,
    language: str,
    clips_relative_dir: str | None = None,
) -> dict[str, Any]:
    video_file = Path(video_path).resolve()
    duration = get_video_duration_seconds(video_file)
    minutes = group_segments_by_minute(segments, duration_seconds=duration)

    noi_dung: dict[str, str] = {}
    for row in minutes:
        row["clip"] = None
        if clips_relative_dir:
            row["clip"] = f"{clips_relative_dir}/minute_{row['minute']:02d}.mp4"
        noi_dung[row["label"]] = row["text"]

    return {
        "video": str(video_file),
        "video_id": video_file.stem,
        "language": language,
        "duration_seconds": round(duration, 3),
        "noi_dung_theo_phut": noi_dung,
        "minutes": minutes,
    }


def split_video_by_minutes(
    video_path: str | Path,
    clips_dir: str | Path,
    *,
    duration_seconds: float | None = None,
) -> list[str]:
    """
    Export minute_01.mp4, minute_02.mp4, ... (re-encoded for clean cuts).
    Returns absolute paths of clip files.
    """
    video_file = Path(video_path)
    out_dir = Path(clips_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    duration = duration_seconds or get_video_duration_seconds(video_file)
    total_minutes = max(1, math.ceil(duration / 60.0))
    clip_paths: list[str] = []

    for minute in range(1, total_minutes + 1):
        start = (minute - 1) * 60
        clip_duration = min(60.0, duration - start)
        if clip_duration <= 0:
            break
        out_path = out_dir / f"minute_{minute:02d}.mp4"
        try:
            (
                ffmpeg.input(str(video_file), ss=start)
                .output(
                    str(out_path),
                    t=clip_duration,
                    vcodec="libx264",
                    acodec="aac",
                    preset="veryfast",
                    crf=23,
                    movflags="+faststart",
                )
                .overwrite_output()
                .run(capture_stdout=True, capture_stderr=True)
            )
        except ffmpeg.Error as exc:
            stderr = exc.stderr.decode(errors="replace") if exc.stderr else str(exc)
            raise RuntimeError(f"ffmpeg clip minute {minute} failed: {stderr}") from exc

        if out_path.is_file() and out_path.stat().st_size > 0:
            clip_paths.append(str(out_path.resolve()))
            logger.info("Clip saved: %s (%.1fs)", out_path.name, clip_duration)
        else:
            logger.warning("Clip missing for minute %d: %s", minute, out_path)

    return clip_paths


def transcript_text_from_minute_payload(payload: dict[str, Any]) -> str:
    """Plain transcript for trend_root/transcript.txt from minute JSON payload."""
    lines = [f"# Language: {payload.get('language', 'unknown')}", ""]
    for row in payload.get("minutes") or []:
        label = row.get("label") or f"phút thứ {row.get('minute', '?')}"
        text = (row.get("text") or "").strip()
        if text:
            lines.append(f"[{label}] {text}")
    return "\n".join(lines).strip() + "\n"


def process_video_minute_split(
    video_path: str | Path,
    *,
    trend_root: str | Path | None = None,
    language: str | None = None,
) -> dict[str, Any]:
    """
    Full pipeline for one file:
    - extract audio → transcribe
    - write Content/<video_id>.json
    - write Videos/clips/<video_id>/minute_XX.mp4
    """
    video_file = Path(video_path).resolve()
    if not video_file.is_file():
        raise FileNotFoundError(f"Video not found: {video_file}")

    root = Path(trend_root) if trend_root else video_file.parent.parent
    content_dir = root / "Content"
    clips_dir = root / "Videos" / "clips" / video_file.stem
    content_dir.mkdir(parents=True, exist_ok=True)

    audio_tmp = root / "Videos" / f"_audio_{video_file.stem}.wav"
    try:
        extract_audio_from_video(video_file, audio_tmp)
        segments, detected = transcribe_audio_segments(audio_tmp, language=language)
        lang = language or detected

        clips_rel = f"Videos/clips/{video_file.stem}"
        payload = build_minute_content_json(
            video_file,
            segments,
            language=lang,
            clips_relative_dir=clips_rel,
        )
        duration = payload["duration_seconds"]
        split_video_by_minutes(video_file, clips_dir, duration_seconds=duration)

        json_path = content_dir / f"{video_file.stem}.json"
        json_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        payload["content_json"] = str(json_path.resolve())
        payload["clips_dir"] = str(clips_dir.resolve())
        logger.info("Minute content JSON: %s", json_path)
        return payload
    finally:
        if audio_tmp.is_file():
            audio_tmp.unlink(missing_ok=True)
