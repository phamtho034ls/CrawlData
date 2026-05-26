"""Rewrite translated segments into platform-ready scripts."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from src.translation_service import _extract_json_block, _get_llm

load_dotenv()
logger = logging.getLogger(__name__)

DEFAULT_PROFILE = {
    "name": "short_vi_60s",
    "platform": "tiktok",
    "style": "short-form viral",
    "tone": "energetic",
    "target_duration_sec": 60,
    "max_script_segments": 6,
    "segment_duration_sec": 10,
    "burn_subtitles": True,
}


class _RewriteSegment(BaseModel):
    segment_id: str = Field(default="s1")
    source_minutes: list[int] = Field(default_factory=list)
    voiceover_text: str = Field(default="")
    on_screen_text: str = Field(default="")
    visual_intent: str = Field(default="")


class _RewriteResult(BaseModel):
    hook: str = Field(default="")
    script_segments: list[_RewriteSegment] = Field(default_factory=list)
    cta: str = Field(default="")


def load_localization_profiles(
    config_path: str | Path | None = None,
) -> dict[str, dict[str, Any]]:
    path = (
        Path(config_path)
        if config_path
        else Path(__file__).resolve().parent.parent / "configs" / "localization_profiles.json"
    )
    if not path.is_file():
        return {DEFAULT_PROFILE["name"]: dict(DEFAULT_PROFILE)}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {DEFAULT_PROFILE["name"]: dict(DEFAULT_PROFILE)}
    profiles = payload.get("profiles") if isinstance(payload, dict) else None
    if not isinstance(profiles, list):
        return {DEFAULT_PROFILE["name"]: dict(DEFAULT_PROFILE)}

    out: dict[str, dict[str, Any]] = {}
    for item in profiles:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        merged = dict(DEFAULT_PROFILE)
        merged.update(item)
        out[str(item["name"])] = merged
    if not out:
        out[DEFAULT_PROFILE["name"]] = dict(DEFAULT_PROFILE)
    return out


def _fallback_rewrite(
    translated_payload: dict[str, Any],
    *,
    profile: dict[str, Any],
) -> dict[str, Any]:
    segments = translated_payload.get("segments") or []
    non_empty = [s for s in segments if (s.get("translated_text") or "").strip()]
    max_parts = int(profile.get("max_script_segments", 6))
    selected = non_empty[:max_parts]
    if not selected:
        selected = non_empty[:1]

    script_segments: list[dict[str, Any]] = []
    for idx, seg in enumerate(selected, start=1):
        text = (seg.get("translated_text") or "").strip()
        script_segments.append(
            {
                "segment_id": f"s{idx}",
                "source_minutes": [int(seg.get("minute") or 0)],
                "voiceover_text": text,
                "on_screen_text": text[:72],
                "visual_intent": "Speaker + supporting b-roll",
            }
        )

    hook = (
        script_segments[0]["voiceover_text"][:120] if script_segments else "Tổng hợp nội dung chính"
    )
    return {
        "video_id": translated_payload.get("video_id") or "",
        "language": translated_payload.get("target_language") or "vi",
        "style": profile.get("style") or "short-form viral",
        "target_duration_sec": int(profile.get("target_duration_sec", 60)),
        "hook": hook,
        "script_segments": script_segments,
        "cta": "Follow để xem phần tiếp theo.",
        "meta": {"provider": "fallback", "model": "none"},
    }


def rewrite_translated_payload(
    translated_payload: dict[str, Any],
    *,
    profile: dict[str, Any],
) -> dict[str, Any]:
    """
    Rewrite translated segments into concise social script.
    Returns structured JSON for edit planning.
    """
    llm = _get_llm()
    if not llm:
        return _fallback_rewrite(translated_payload, profile=profile)

    segments = translated_payload.get("segments") or []
    compact_rows = []
    for row in segments:
        text = (row.get("translated_text") or "").strip()
        if not text:
            continue
        compact_rows.append(
            {
                "minute": int(row.get("minute") or 0),
                "text": text,
            }
        )
    if not compact_rows:
        return _fallback_rewrite(translated_payload, profile=profile)

    prompt_text = (
        "Rewrite translated transcript segments into a social-video script.\n"
        f"platform={profile.get('platform', 'tiktok')}, "
        f"style={profile.get('style', 'short-form viral')}, "
        f"tone={profile.get('tone', 'energetic')}, "
        f"target_duration_sec={int(profile.get('target_duration_sec', 60))}, "
        f"max_script_segments={int(profile.get('max_script_segments', 6))}\n\n"
        "Input segments JSON:\n"
        f"{json.dumps(compact_rows, ensure_ascii=False)}"
    )
    try:
        if hasattr(llm, "with_structured_output"):
            structured = llm.with_structured_output(_RewriteResult)
            obj = structured.invoke(prompt_text)
            payload: Any = obj.model_dump()
        else:
            prompt = ChatPromptTemplate.from_messages(
                [
                    (
                        "system",
                        "You are a social video scriptwriter. Produce concise, punchy scripts. "
                        "Return strict JSON only.",
                    ),
                    ("human", "{prompt_text}"),
                ]
            )
            chain = prompt | llm
            response = chain.invoke({"prompt_text": prompt_text})
            raw = response.content if hasattr(response, "content") else str(response)
            payload = json.loads(_extract_json_block(raw))

        if isinstance(payload, list):
            payload = payload[0] if payload else {}
        script_segments = payload.get("script_segments") or []
        if not isinstance(script_segments, list) or not script_segments:
            raise ValueError("script_segments missing")
        return {
            "video_id": translated_payload.get("video_id") or "",
            "language": translated_payload.get("target_language") or "vi",
            "style": profile.get("style") or "short-form viral",
            "target_duration_sec": int(profile.get("target_duration_sec", 60)),
            "hook": str(payload.get("hook") or "").strip(),
            "script_segments": script_segments,
            "cta": str(payload.get("cta") or "").strip(),
            "meta": {
                "provider": "llm",
                "model": getattr(llm, "model_name", llm.__class__.__name__),
            },
        }
    except Exception as exc:
        logger.warning("Rewrite failed (%s). Using fallback rewrite.", exc)
        return _fallback_rewrite(translated_payload, profile=profile)
