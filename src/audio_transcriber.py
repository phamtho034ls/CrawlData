"""Extract audio with ffmpeg and transcribe with faster-whisper."""

from __future__ import annotations

import logging
import os
from pathlib import Path

import ffmpeg
from dotenv import load_dotenv
from faster_whisper import WhisperModel

from src.device_utils import resolve_whisper_device

load_dotenv()
logger = logging.getLogger(__name__)

WHISPER_MODEL_SIZE = os.getenv("WHISPER_MODEL", "small")
if WHISPER_MODEL_SIZE not in {"tiny", "base", "small", "medium", "large", "large-v2", "large-v3"}:
    WHISPER_MODEL_SIZE = "small"


def extract_audio_from_video(video_path: str | Path, output_audio_path: str | Path) -> Path:
    """
    Extract 16 kHz mono WAV audio from video (Whisper-optimized).
    """
    video_file = Path(video_path)
    if not video_file.is_file():
        raise FileNotFoundError(f"Video not found: {video_file}")

    audio_file = Path(output_audio_path)
    audio_file.parent.mkdir(parents=True, exist_ok=True)

    try:
        (
            ffmpeg.input(str(video_file))
            .output(
                str(audio_file),
                ac=1,
                ar=16000,
                format="wav",
                acodec="pcm_s16le",
            )
            .overwrite_output()
            .run(capture_stdout=True, capture_stderr=True)
        )
    except ffmpeg.Error as exc:
        stderr = exc.stderr.decode(errors="replace") if exc.stderr else str(exc)
        raise RuntimeError(f"ffmpeg audio extraction failed: {stderr}") from exc

    if not audio_file.is_file() or audio_file.stat().st_size == 0:
        raise RuntimeError(f"Audio file was not created: {audio_file}")

    logger.info("Audio extracted to %s", audio_file)
    return audio_file.resolve()


def _format_timestamp(seconds: float) -> str:
    total_ms = int(seconds * 1000)
    hours, rem = divmod(total_ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs = rem / 1000.0
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:06.3f}"
    return f"{minutes:02d}:{secs:06.3f}"


def transcribe_audio(
    audio_path: str | Path,
    output_txt_path: str | Path,
    *,
    language: str | None = "vi",
) -> Path:
    """
    Transcribe WAV with faster-whisper; write timestamped lines to output_txt_path.
    """
    audio_file = Path(audio_path)
    if not audio_file.is_file():
        raise FileNotFoundError(f"Audio not found: {audio_file}")

    output_file = Path(output_txt_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)

    device = resolve_whisper_device()
    compute_type = "float16" if device == "cuda" else "int8"
    logger.info(
        "Loading faster-whisper '%s' on device=%s (%s)",
        WHISPER_MODEL_SIZE,
        device,
        compute_type,
    )

    model = WhisperModel(WHISPER_MODEL_SIZE, device=device, compute_type=compute_type)
    segments, info = model.transcribe(
        str(audio_file),
        language=language,
        beam_size=5,
        vad_filter=True,
    )

    detected = getattr(info, "language", None) or "unknown"
    lines = [f"# Language: {detected}", ""]
    for segment in segments:
        start = _format_timestamp(segment.start)
        end = _format_timestamp(segment.end)
        text = (segment.text or "").strip()
        if text:
            lines.append(f"[{start} -> {end}] {text}")

    body = "\n".join(lines).strip() + "\n"
    output_file.write_text(body, encoding="utf-8")
    logger.info("Transcript saved to %s (%d segments)", output_file, len(lines) - 2)
    return output_file.resolve()
