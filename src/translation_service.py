"""Translate minute-based transcript payloads into target languages."""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from dotenv import load_dotenv
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

load_dotenv()
logger = logging.getLogger(__name__)

_PLACEHOLDER_RE = re.compile(
    r"your[_-]?(openai|api|key|secret|token|google)|changeme|placeholder|xxx+",
    re.IGNORECASE,
)


def _valid_openai_key() -> str | None:
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key or len(key) < 20 or _PLACEHOLDER_RE.search(key):
        return None
    return key


def _valid_gemini_key() -> str | None:
    key = (
        os.getenv("GOOGLE_API_KEY", "").strip()
        or os.getenv("GEMINI_API_KEY", "").strip()
    )
    if not key or len(key) < 20 or _PLACEHOLDER_RE.search(key):
        return None
    return key


def _get_llm():
    api_key = _valid_openai_key()
    if api_key:
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(model="gpt-4o-mini", temperature=0.2, api_key=api_key)

    gemini_key = _valid_gemini_key()
    if gemini_key:
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(
            model="gemini-1.5-flash",
            temperature=0.2,
            google_api_key=gemini_key,
        )

    return None


def _extract_json_block(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].strip()
    if text.startswith("{") or text.startswith("["):
        return text
    start = text.find("[")
    end = text.rfind("]")
    if start >= 0 and end > start:
        return text[start : end + 1]
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return text[start : end + 1]
    raise ValueError("No JSON object found in model response")


class _TranslationItem(BaseModel):
    id: int = Field(...)
    translated_text: str = Field(default="")


class _TranslationBatch(BaseModel):
    items: list[_TranslationItem] = Field(default_factory=list)


def _fallback_translation(
    segments: list[dict[str, Any]],
    target_language: str,
    source_language: str,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for seg in segments:
        text = (seg.get("text") or "").strip()
        if source_language.lower().startswith(target_language.lower()):
            translated = text
        else:
            translated = text
        out.append({**seg, "source_text": text, "translated_text": translated})
    return out


def _translate_batch(
    llm,
    batch: list[dict[str, Any]],
    *,
    source_language: str,
    target_language: str,
) -> dict[int, str]:
    lines = []
    for seg in batch:
        lines.append(
            f"{seg['id']}. minute={seg['minute']} text={json.dumps(seg['text'], ensure_ascii=False)}"
        )
    prompt_text = (
        "Translate subtitle lines from "
        f"{source_language} to {target_language}. Preserve meaning, names, "
        "and technical terms.\n\n"
        "Input lines:\n"
        f"{chr(10).join(lines)}\n\n"
        "Return JSON ONLY with shape:\n"
        '{"items":[{"id":1,"translated_text":"..."}]}'
    )
    payload: Any
    if hasattr(llm, "with_structured_output"):
        structured = llm.with_structured_output(_TranslationBatch)
        obj = structured.invoke(prompt_text)
        payload = obj.model_dump()
    else:
        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are a professional subtitle translator. Keep meaning, names, and "
                    "technical terms accurate. Return JSON only.",
                ),
                ("human", "{prompt_text}"),
            ]
        )
        chain = prompt | llm
        response = chain.invoke({"prompt_text": prompt_text})
        raw = response.content if hasattr(response, "content") else str(response)
        payload = json.loads(_extract_json_block(raw))

    if isinstance(payload, list):
        payload = {"items": payload}

    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        items = []

    out: dict[int, str] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        idx = item.get("id")
        if isinstance(idx, int):
            out[idx] = str(item.get("translated_text") or "").strip()
    return out


def translate_minute_payload(
    minute_payload: dict[str, Any],
    *,
    target_language: str,
    batch_size: int = 12,
) -> dict[str, Any]:
    """
    Translate transcript segments from Content/<video_id>.json into target language.
    Falls back to source text when no model key is configured.
    """
    source_language = str(minute_payload.get("language") or "unknown").strip()
    minutes = minute_payload.get("minutes") or []
    segments: list[dict[str, Any]] = []
    for row in minutes:
        text = (row.get("text") or "").strip()
        if not text:
            continue
        segments.append(
            {
                "id": len(segments) + 1,
                "minute": int(row.get("minute") or 0),
                "start_seconds": float(row.get("start_seconds") or 0.0),
                "end_seconds": float(row.get("end_seconds") or 0.0),
                "text": text,
            }
        )

    llm = _get_llm()
    if not llm or not segments:
        translated_rows = _fallback_translation(segments, target_language, source_language)
        provider = "fallback"
        model_name = "none"
    else:
        translated_map: dict[int, str] = {}
        for start in range(0, len(segments), batch_size):
            batch = segments[start : start + batch_size]
            try:
                translated_map.update(
                    _translate_batch(
                        llm,
                        batch,
                        source_language=source_language,
                        target_language=target_language,
                    )
                )
            except Exception as exc:
                logger.warning("Translate batch failed (%s). Falling back for that batch.", exc)
                for seg in batch:
                    translated_map[seg["id"]] = seg["text"]

        translated_rows = []
        for seg in segments:
            translated_rows.append(
                {
                    **seg,
                    "source_text": seg["text"],
                    "translated_text": translated_map.get(seg["id"], seg["text"]),
                }
            )
        provider = "llm"
        model_name = getattr(llm, "model_name", llm.__class__.__name__)

    return {
        "video_id": minute_payload.get("video_id") or "",
        "video": minute_payload.get("video") or "",
        "source_language": source_language,
        "target_language": target_language,
        "segments": translated_rows,
        "meta": {
            "provider": provider,
            "model": model_name,
            "segment_count": len(translated_rows),
        },
    }
