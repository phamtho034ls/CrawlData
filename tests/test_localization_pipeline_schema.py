from pathlib import Path

from src.edit_plan_builder import build_edit_plan
from src.rewrite_service import load_localization_profiles, rewrite_translated_payload
from src.translation_service import translate_minute_payload


def _sample_minute_payload(tmp_path: Path) -> dict:
    trend_root = tmp_path / "trend"
    clip_dir = trend_root / "Videos" / "clips" / "abc"
    clip_dir.mkdir(parents=True, exist_ok=True)
    # Files are not rendered in these tests, so placeholders are enough.
    (clip_dir / "minute_01.mp4").write_text("x", encoding="utf-8")
    (clip_dir / "minute_02.mp4").write_text("x", encoding="utf-8")
    return {
        "video_id": "abc",
        "video": str((trend_root / "Videos" / "abc.mp4").resolve()),
        "language": "en",
        "minutes": [
            {
                "minute": 1,
                "start_seconds": 0.0,
                "end_seconds": 60.0,
                "text": "Hello this is minute one.",
                "clip": "Videos/clips/abc/minute_01.mp4",
            },
            {
                "minute": 2,
                "start_seconds": 60.0,
                "end_seconds": 120.0,
                "text": "This is minute two content.",
                "clip": "Videos/clips/abc/minute_02.mp4",
            },
        ],
    }


def test_translate_payload_schema(tmp_path: Path):
    payload = _sample_minute_payload(tmp_path)
    translated = translate_minute_payload(payload, target_language="vi")
    assert translated["video_id"] == "abc"
    assert translated["target_language"] == "vi"
    assert len(translated["segments"]) == 2
    assert "translated_text" in translated["segments"][0]


def test_rewrite_and_edit_plan_schema(tmp_path: Path):
    payload = _sample_minute_payload(tmp_path)
    profiles = load_localization_profiles()
    profile = profiles["short_vi_60s"]
    translated = translate_minute_payload(payload, target_language="vi")
    rewritten = rewrite_translated_payload(translated, profile=profile)
    plan = build_edit_plan(
        rewritten,
        payload,
        trend_root=tmp_path / "trend",
        profile=profile,
    )
    assert plan["video_id"] == "abc"
    assert "tracks" in plan
    assert isinstance(plan["tracks"]["video"], list)
