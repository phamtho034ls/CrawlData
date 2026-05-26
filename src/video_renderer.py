"""Render final videos from edit plans using ffmpeg."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import ffmpeg

logger = logging.getLogger(__name__)


def _format_srt_time(seconds: float) -> str:
    total_ms = int(seconds * 1000)
    hours, rem = divmod(total_ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, ms = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def write_srt(subtitles: list[dict[str, Any]], output_path: str | Path) -> Path:
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    for i, row in enumerate(subtitles, start=1):
        start = float(row.get("start") or 0.0)
        end = float(row.get("end") or start + 1.0)
        text = (row.get("text") or "").strip()
        if not text:
            continue
        lines.extend(
            [
                str(i),
                f"{_format_srt_time(start)} --> {_format_srt_time(end)}",
                text,
                "",
            ]
        )
    out.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
    return out.resolve()


def render_video_from_plan(
    plan: dict[str, Any],
    *,
    output_path: str | Path,
) -> dict[str, Any]:
    """
    Render MP4 from EDL plan.
    MVP:
    - hard cuts only
    - reuse original clip audio
    - optional .srt sidecar (not burned-in)
    """
    tracks = plan.get("tracks") or {}
    shots = tracks.get("video") or []
    if not shots:
        raise ValueError("Edit plan has no video shots")

    canvas = plan.get("canvas") or {}
    width = int(canvas.get("width") or 1080)
    height = int(canvas.get("height") or 1920)
    fps = int(canvas.get("fps") or 30)

    inputs = []
    streams = []
    for shot in shots:
        clip = Path(str(shot.get("clip_path") or "")).resolve()
        if not clip.is_file():
            raise FileNotFoundError(f"Clip not found for render: {clip}")
        ss = float(shot.get("src_start") or 0.0)
        duration = float(shot.get("src_end") or 0.0) - ss
        if duration <= 0:
            continue
        inp = ffmpeg.input(str(clip), ss=ss, t=duration)
        inputs.append(inp)
        v = (
            inp.video.filter("scale", width, height, force_original_aspect_ratio="decrease")
            .filter("pad", width, height, "(ow-iw)/2", "(oh-ih)/2")
            .filter("fps", fps=fps)
            .filter("setsar", 1)
        )
        a = inp.audio.filter("aformat", sample_rates=48000, channel_layouts="stereo")
        streams.extend([v, a])

    if not streams:
        raise ValueError("Edit plan has no valid shot durations")

    concat_node = ffmpeg.concat(*streams, v=1, a=1).node
    vout, aout = concat_node[0], concat_node[1]

    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    (
        ffmpeg.output(
            vout,
            aout,
            str(out_path),
            vcodec="libx264",
            acodec="aac",
            preset="veryfast",
            movflags="+faststart",
            pix_fmt="yuv420p",
            r=fps,
        )
        .overwrite_output()
        .run(capture_stdout=True, capture_stderr=True)
    )

    subtitles = tracks.get("subtitle") or []
    srt_path = None
    warnings: list[str] = []
    burned_subtitles = False
    if subtitles:
        srt_path = write_srt(subtitles, out_path.with_suffix(".srt"))
        burn_subtitles = bool(plan.get("meta", {}).get("burn_subtitles", True))
        if burn_subtitles:
            burn_tmp = out_path.with_name(f"{out_path.stem}.burn.mp4")
            try:
                srt_filter_path = str(srt_path).replace("\\", "/").replace(":", "\\:")
                (
                    ffmpeg.input(str(out_path))
                    .output(
                        str(burn_tmp),
                        vf=f"subtitles='{srt_filter_path}'",
                        vcodec="libx264",
                        acodec="aac",
                        preset="veryfast",
                        movflags="+faststart",
                        pix_fmt="yuv420p",
                        r=fps,
                    )
                    .overwrite_output()
                    .run(capture_stdout=True, capture_stderr=True)
                )
                if burn_tmp.is_file() and burn_tmp.stat().st_size > 0:
                    out_path.unlink(missing_ok=True)
                    os.replace(str(burn_tmp), str(out_path))
                    burned_subtitles = True
            except Exception as exc:
                warnings.append(f"subtitle_burn_failed: {exc}")
                if burn_tmp.is_file():
                    burn_tmp.unlink(missing_ok=True)

    duration = max((float(s.get("timeline_end") or 0.0) for s in shots), default=0.0)
    report = {
        "video_id": plan.get("video_id") or "",
        "language": plan.get("language") or "",
        "output_path": str(out_path.resolve()),
        "subtitle_path": str(srt_path) if srt_path else "",
        "burned_subtitles": burned_subtitles,
        "duration_sec": round(duration, 3),
        "status": "ok",
        "warnings": warnings,
    }
    logger.info("Rendered localization output: %s", out_path)
    return report
