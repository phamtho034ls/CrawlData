from src.content_store import load_videos_for_trend, parse_video_links_txt


def test_parse_video_links_txt():
    text = """1. First video title
   https://www.youtube.com/watch?v=abc123
   [youtube] views=1,234,567

2. Second
   https://www.tiktok.com/@u/video/999
   [tiktok] views=500
"""
    videos = parse_video_links_txt(text)
    assert len(videos) == 2
    assert videos[0]["url"] == "https://www.youtube.com/watch?v=abc123"
    assert videos[0]["title"] == "First video title"
    assert videos[0]["view_count"] == 1234567
    assert videos[1]["platform"] == "tiktok"


def test_load_videos_for_trend_prefers_json(tmp_path):
    root = tmp_path / "trend"
    root.mkdir()
    (root / "Videos").mkdir()
    (root / "video_links.json").write_text(
        '[{"url": "https://youtu.be/x", "title": "From JSON"}]\n',
        encoding="utf-8",
    )
    (root / "Videos" / "video_links.txt").write_text(
        "1. From TXT\n   https://youtu.be/y\n",
        encoding="utf-8",
    )
    videos = load_videos_for_trend(root)
    assert videos[0]["title"] == "From JSON"
