"""Localization pipeline: translate -> rewrite -> edit plan -> render."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from src.content_store import load_videos_for_trend
from src.edit_plan_builder import build_edit_plan
from src.rewrite_service import load_localization_profiles, rewrite_translated_payload
from src.translation_service import translate_minute_payload
from src.video_renderer import render_video_from_plan

logger = logging.getLogger(__name__)


def _load_minute_payloads(trend_root: str | Path) -> dict[str, dict[str, Any]]:
    content_dir = Path(trend_root) / "Content"
    if not content_dir.is_dir():
        return {}

    out: dict[str, dict[str, Any]] = {}
    for path in sorted(content_dir.glob("*.json")):
        name = path.name
        if ".translated." in name or ".rewrite." in name:
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        if not payload.get("video_id") or not payload.get("minutes"):
            continue
        out[str(payload["video_id"])] = payload
    return out


def _ordered_video_ids(
    trend_root: str | Path,
    available_ids: set[str],
) -> list[str]:
    ordered: list[str] = []
    for row in load_videos_for_trend(trend_root):
        video_id = str(row.get("video_id") or "").strip()
        if not video_id:
            url = str(row.get("url") or "")
            if "v=" in url:
                video_id = url.split("v=", 1)[1].split("&", 1)[0]
            else:
                video_id = Path(url.rstrip("/")).name
        if video_id in available_ids and video_id not in ordered:
            ordered.append(video_id)
    for vid in sorted(available_ids):
        if vid not in ordered:
            ordered.append(vid)
    return ordered


def _save_json(path: Path, payload: dict[str, Any]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return str(path.resolve())


def run_localization_pipeline(
    *,
    trend_root: str | Path,
    target_langs: list[str],
    profile_name: str = "short_vi_60s",
    render: bool = True,
    max_videos: int = 0,
) -> dict[str, Any]:
    """
    Run localization MVP on existing trend folder.
    Requires Module 3 outputs under Content/<video_id>.json.
    """
    root = Path(trend_root).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Trend folder not found: {root}")

    profiles = load_localization_profiles()
    profile = profiles.get(profile_name)
    if not profile:
        raise ValueError(
            f"Unknown profile '{profile_name}'. Available: {', '.join(sorted(profiles))}"
        )

    minute_payloads = _load_minute_payloads(root)
    if not minute_payloads:
        raise ValueError(
            f"No minute payload JSON found in {root / 'Content'}. Run Module 3 first."
        )

    ordered_ids = _ordered_video_ids(root, set(minute_payloads))
    if max_videos > 0:
        ordered_ids = ordered_ids[:max_videos]

    content_dir = root / "Content"
    edit_dir = root / "Videos" / "edit_plan"
    final_dir = root / "Videos" / "final"
    results: list[dict[str, Any]] = []

    langs = [lang.strip().lower() for lang in target_langs if lang.strip()]
    if not langs:
        raise ValueError("target_langs is empty")

    for video_id in ordered_ids:
        minute_payload = minute_payloads[video_id]
        for lang in langs:
            row: dict[str, Any] = {
                "video_id": video_id,
                "language": lang,
                "status": "ok",
            }
            try:
                translated = translate_minute_payload(
                    minute_payload,
                    target_language=lang,
                )
                row["translated_path"] = _save_json(
                    content_dir / f"{video_id}.translated.{lang}.json",
                    translated,
                )

                rewritten = rewrite_translated_payload(
                    translated,
                    profile=profile,
                )
                row["rewrite_path"] = _save_json(
                    content_dir / f"{video_id}.rewrite.{lang}.json",
                    rewritten,
                )

                plan = build_edit_plan(
                    rewritten,
                    minute_payload,
                    trend_root=root,
                    profile=profile,
                )
                row["edit_plan_path"] = _save_json(
                    edit_dir / f"{video_id}.{lang}.edl.json",
                    plan,
                )

                if render:
                    render_report = render_video_from_plan(
                        plan,
                        output_path=final_dir / f"{video_id}.{lang}.mp4",
                    )
                    row["render_report_path"] = _save_json(
                        final_dir / f"{video_id}.{lang}.render.json",
                        render_report,
                    )
                    row["output_video"] = render_report.get("output_path") or ""
                else:
                    row["output_video"] = ""
            except Exception as exc:
                row["status"] = "failed"
                row["error"] = str(exc)
                logger.exception(
                    "Localization failed for %s/%s in %s",
                    video_id,
                    lang,
                    root,
                )
            results.append(row)

    summary = {
        "trend_root": str(root),
        "profile": profile_name,
        "target_langs": langs,
        "videos_requested": len(ordered_ids),
        "jobs_total": len(results),
        "jobs_ok": sum(1 for r in results if r.get("status") == "ok"),
        "jobs_failed": sum(1 for r in results if r.get("status") == "failed"),
        "results": results,
    }
    summary_path = root / "localization_summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    summary["summary_path"] = str(summary_path.resolve())
    return summary
