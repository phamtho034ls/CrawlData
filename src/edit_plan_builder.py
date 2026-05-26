"""Build lightweight edit plans from rewrite output and minute payloads."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def _clip_lookup(minute_payload: dict[str, Any], trend_root: str | Path) -> dict[int, str]:
    root = Path(trend_root)
    clips: dict[int, str] = {}
    for row in minute_payload.get("minutes") or []:
        minute = int(row.get("minute") or 0)
        clip_rel = row.get("clip")
        if minute <= 0 or not clip_rel:
            continue
        clip_path = root / clip_rel
        clips[minute] = str(clip_path.resolve())
    return clips


def build_edit_plan(
    rewrite_payload: dict[str, Any],
    minute_payload: dict[str, Any],
    *,
    trend_root: str | Path,
    profile: dict[str, Any],
) -> dict[str, Any]:
    """
    Convert rewritten script into an EDL-style JSON plan.
    MVP strategy:
    - 1 script segment -> 1 source minute clip snippet
    - each shot uses fixed duration from profile (default 8-12s)
    """
    width = int(profile.get("canvas_width", 1080))
    height = int(profile.get("canvas_height", 1920))
    fps = int(profile.get("fps", 30))
    shot_duration = float(profile.get("segment_duration_sec", 10))
    target_duration = int(profile.get("target_duration_sec", 60))

    clip_map = _clip_lookup(minute_payload, trend_root)
    script_segments = rewrite_payload.get("script_segments") or []
    video_tracks: list[dict[str, Any]] = []
    subtitle_tracks: list[dict[str, Any]] = []
    t = 0.0
    shot_idx = 0

    for segment in script_segments:
        if t >= target_duration:
            break
        mins = segment.get("source_minutes") or []
        minute_candidates = [int(m) for m in mins if isinstance(m, int) or str(m).isdigit()]
        minute = int(minute_candidates[0]) if minute_candidates else 0
        clip_path = clip_map.get(minute)
        if not clip_path:
            continue

        remaining = max(0.0, target_duration - t)
        dur = min(shot_duration, remaining)
        if dur <= 0:
            break
        src_start = float((shot_idx % 5) * 2)
        src_end = src_start + dur
        shot_idx += 1

        video_tracks.append(
            {
                "clip_path": clip_path,
                "src_start": round(src_start, 3),
                "src_end": round(src_end, 3),
                "timeline_start": round(t, 3),
                "timeline_end": round(t + dur, 3),
                "transition_in": "cut",
                "transition_out": "cut",
            }
        )

        on_screen = (segment.get("on_screen_text") or segment.get("voiceover_text") or "").strip()
        if on_screen:
            subtitle_tracks.append(
                {
                    "start": round(t, 3),
                    "end": round(t + dur, 3),
                    "text": on_screen,
                }
            )
        t += dur

    return {
        "video_id": rewrite_payload.get("video_id") or minute_payload.get("video_id") or "",
        "language": rewrite_payload.get("language") or "vi",
        "canvas": {"width": width, "height": height, "fps": fps},
        "tracks": {
            "video": video_tracks,
            "subtitle": subtitle_tracks,
            "audio": [],
        },
        "meta": {
            "style": rewrite_payload.get("style") or profile.get("style"),
            "hook": rewrite_payload.get("hook") or "",
            "cta": rewrite_payload.get("cta") or "",
            "target_duration_sec": target_duration,
            "burn_subtitles": bool(profile.get("burn_subtitles", True)),
        },
    }
