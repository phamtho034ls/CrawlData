"""Unit tests for minute grouping (no Whisper/ffmpeg required)."""

from src.video_minute_splitter import _minute_label, group_segments_by_minute


def test_minute_label_vietnamese():
    assert _minute_label(1) == "phút thứ 1"
    assert _minute_label(3) == "phút thứ 3"


def test_group_segments_by_minute():
    segments = [
        {"start": 5.0, "end": 12.0, "text": "Hello"},
        {"start": 65.0, "end": 70.0, "text": "Minute two"},
    ]
    rows = group_segments_by_minute(segments, duration_seconds=125.0)
    assert len(rows) == 3
    assert rows[0]["label"] == "phút thứ 1"
    assert "Hello" in rows[0]["text"]
    assert rows[1]["label"] == "phút thứ 2"
    assert "Minute two" in rows[1]["text"]
    assert rows[2]["text"] == ""
