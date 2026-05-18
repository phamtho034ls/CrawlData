"""Media download and speech-to-text extraction."""

from __future__ import annotations

import logging
import os
from pathlib import Path

import whisper
import yt_dlp

from src.device_utils import gpu_status_message, resolve_whisper_device

logger = logging.getLogger(__name__)

WHISPER_MODEL = os.getenv("WHISPER_MODEL", "base")


def download_video(url: str, output_path: str | Path) -> Path:
    """
    Download the best quality video from the given URL into output_path.

    Returns the absolute path to the downloaded video file.
    """
    output_dir = Path(output_path)
    output_dir.mkdir(parents=True, exist_ok=True)

    ydl_opts = {
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "outtmpl": str(output_dir / "%(id)s.%(ext)s"),
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        if "requested_downloads" in info and info["requested_downloads"]:
            downloaded = info["requested_downloads"][0].get("filepath")
            if downloaded:
                return Path(downloaded).resolve()

        video_id = info.get("id", "video")
        for candidate in output_dir.glob(f"{video_id}.*"):
            if candidate.is_file():
                return candidate.resolve()

        for candidate in sorted(output_dir.iterdir()):
            if candidate.is_file():
                return candidate.resolve()

    raise FileNotFoundError(f"No video file found in {output_dir}")


def extract_transcript(video_path: str | Path, output_text_path: str | Path) -> Path:
    """
    Transcribe video audio with Whisper and save to output_text_path.

    Uses CUDA GPU when available (see WHISPER_DEVICE / install_gpu.ps1).
    """
    video_file = Path(video_path)
    if not video_file.is_file():
        raise FileNotFoundError(f"Video file not found: {video_file}")

    output_file = Path(output_text_path)
    if output_file.exists() and output_file.stat().st_size > 0:
        return output_file.resolve()

    output_file.parent.mkdir(parents=True, exist_ok=True)

    device = resolve_whisper_device()
    logger.info("%s", gpu_status_message())
    logger.info("Loading Whisper model '%s' on device=%s", WHISPER_MODEL, device)

    model = whisper.load_model(WHISPER_MODEL, device=device)
    use_fp16 = device == "cuda"
    result = model.transcribe(str(video_file), fp16=use_fp16)
    transcript = (result.get("text") or "").strip()

    output_file.write_text(transcript + "\n", encoding="utf-8")
    logger.info("Transcript saved (%d chars) using %s", len(transcript), device)
    return output_file.resolve()
