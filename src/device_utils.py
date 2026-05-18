"""GPU / device detection for ML workloads."""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def resolve_whisper_device() -> str:
    """
    Choose Whisper device: cuda, cpu, or auto (cuda if available).

    Override with WHISPER_DEVICE=cuda|cpu|auto in .env
    """
    override = os.getenv("WHISPER_DEVICE", "auto").strip().lower()
    if override in {"cuda", "cpu"}:
        return override

    try:
        import torch
    except ImportError as exc:
        logger.warning("PyTorch not installed (%s); using CPU for Whisper.", exc)
        return "cpu"

    if torch.cuda.is_available():
        device_name = torch.cuda.get_device_name(0)
        logger.info("CUDA GPU detected: %s", device_name)
        return "cuda"

    logger.info("No CUDA GPU available; using CPU for Whisper.")
    return "cpu"


def gpu_status_message() -> str:
    """Human-readable GPU status for logs and UI."""
    try:
        import torch
    except ImportError:
        return "PyTorch not installed — install CUDA build (see install_gpu.ps1)."

    if not torch.cuda.is_available():
        return (
            "CUDA not available — CPU-only PyTorch or missing NVIDIA driver. "
            "Run install_gpu.ps1 to install torch with CUDA."
        )

    name = torch.cuda.get_device_name(0)
    capability = torch.cuda.get_device_capability(0)
    return f"GPU ready: {name} (compute {capability[0]}.{capability[1]})"
