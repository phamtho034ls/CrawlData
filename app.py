"""Streamlit dashboard — sidebar nav, pipeline progress, multi-keyword collect."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from src.prefect_bootstrap import configure_prefect

configure_prefect()

from main_pipeline import run_pipeline
from src.device_utils import gpu_status_message
from src.pipeline_logging import LOG_FILE, setup_logging
from src.pipeline_progress import PipelineProgress
from src.scraper_config import ScraperConfig
from src.trend_ai_forecaster import (
    RAW_KEYWORD_TARGET,
    REFINED_KEYWORD_COUNT,
    discover_keywords_for_pipeline,
)
from src.trend_leaderboard import scrape_tiktok_discover, scrape_youtube_trending
from src.trend_reader import DATA_TRENDS_DIR, list_all_trends, load_trend_summary
from src.trend_velocity_tracker import calculate_channel_velocity, velocity_to_records

setup_logging()

st.set_page_config(
    page_title="DataCrawl",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

APP_CSS = """
<style>
    @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&display=swap');
    html, body, [class*="css"] { font-family: 'DM Sans', sans-serif; }
    .block-container { padding-top: 1.25rem; max-width: 1200px; }
    section[data-testid="stSidebar"] > div {
        background: linear-gradient(165deg, #0f172a 0%, #1e293b 55%, #0f172a 100%);
    }
    section[data-testid="stSidebar"] .stMarkdown,
    section[data-testid="stSidebar"] label,
    section[data-testid="stSidebar"] p,
    section[data-testid="stSidebar"] span { color: #e2e8f0 !important; }
    section[data-testid="stSidebar"] hr { border-color: #334155; }
    .sidebar-brand {
        font-size: 1.45rem; font-weight: 700; color: #f8fafc !important;
        letter-spacing: -0.02em; margin-bottom: 0.15rem;
    }
    .sidebar-tagline { color: #94a3b8 !important; font-size: 0.82rem; }
    div[data-testid="stMetric"] {
        background: #1e293b; border: 1px solid #334155; border-radius: 10px;
    }
    div[data-testid="stMetric"] label { color: #94a3b8 !important; }
    div[data-testid="stMetric"] [data-testid="stMetricValue"] { color: #f8fafc !important; }
</style>
"""
st.markdown(APP_CSS, unsafe_allow_html=True)

PAGE_LABELS = {
    "storage": "📚 Kho lưu trữ",
    "collect": "➕ Thu thập mới",
    "logs": "📋 Nhật ký",
}

if "page" not in st.session_state:
    st.session_state.page = "storage"
if "selected_trend_id" not in st.session_state:
    st.session_state.selected_trend_id = None
if "last_result" not in st.session_state:
    st.session_state.last_result = None
if "last_error" not in st.session_state:
    st.session_state.last_error = None
if "pipeline_job" not in st.session_state:
    st.session_state.pipeline_job = None
if "discovery_ai_keywords" not in st.session_state:
    st.session_state.discovery_ai_keywords = []
if "discovery_ai_scraped" not in st.session_state:
    st.session_state.discovery_ai_scraped = {}
if "discovery_ai_videos" not in st.session_state:
    st.session_state.discovery_ai_videos = []
if "discovery_ai_raw_keywords" not in st.session_state:
    st.session_state.discovery_ai_raw_keywords = []
if "discovery_ai_scraper_config" not in st.session_state:
    st.session_state.discovery_ai_scraper_config = None
if "discovery_leaderboard_videos" not in st.session_state:
    st.session_state.discovery_leaderboard_videos = []
if "discovery_velocity_df" not in st.session_state:
    st.session_state.discovery_velocity_df = None


def _read_logs(tail_lines: int = 120) -> str:
    if not LOG_FILE.exists():
        return "Chưa có log."
    return "\n".join(
        LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()[-tail_lines:]
    )


def _format_clock(seconds: float) -> str:
    total = int(max(0, seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


_KEYWORD_ICONS = {
    "pending": "⏳ Chờ",
    "searching": "🔍 Đang tìm",
    "done": "✅ Xong",
    "error": "⚠️ Lỗi",
}


def _render_matched_videos(data: dict[str, Any], placeholder) -> None:
    """Videos that passed scrape filters (per keyword), shown as they are found."""
    matched = data.get("matched_videos") or []
    if not matched:
        placeholder.markdown("**Video đã tìm thấy**\n\n_Chưa có video phù hợp._")
        return

    rows = []
    for index, video in enumerate(matched, start=1):
        views = video.get("view_count")
        rows.append(
            {
                "#": index,
                "Từ khóa": video.get("source_keyword") or "—",
                "Tiêu đề": (video.get("title") or "—")[:80],
                "Nền tảng": (video.get("platform") or "?").upper(),
                "Lượt xem": f"{views:,}" if isinstance(views, int) else "—",
                "Link": video.get("url") or "",
            }
        )

    with placeholder.container():
        st.markdown(f"**Video đã tìm thấy ({len(matched)})**")
        st.dataframe(
            pd.DataFrame(rows),
            column_config={
                "Link": st.column_config.LinkColumn("Link", display_text="▶ Mở"),
            },
            use_container_width=True,
            hide_index=True,
            height=min(320, 38 + len(rows) * 35),
        )


def _render_downloaded_videos(data: dict[str, Any], placeholder) -> None:
    """Local MP4 files after Module 3 download."""
    downloaded = data.get("downloaded_videos") or []
    if not downloaded:
        placeholder.empty()
        return

    rows = []
    for index, item in enumerate(downloaded, start=1):
        rows.append(
            {
                "#": index,
                "Tiêu đề": (item.get("title") or "—")[:60],
                "Nền tảng": (item.get("platform") or "?").upper(),
                "Từ khóa": item.get("source_keyword") or "—",
                "File": item.get("video_path") or "",
                "Link": item.get("url") or "",
            }
        )

    with placeholder.container():
        st.markdown(f"**Video đã tải xuống ({len(downloaded)})**")
        st.dataframe(
            pd.DataFrame(rows),
            column_config={
                "Link": st.column_config.LinkColumn("Nguồn", display_text="▶ Mở"),
                "File": st.column_config.TextColumn("Đường dẫn file", width="large"),
            },
            use_container_width=True,
            hide_index=True,
        )


def _render_keyword_list(data: dict[str, Any], placeholder) -> None:
    keywords = data.get("expanded_keywords") or []
    if not keywords:
        placeholder.empty()
        return

    states: dict[str, str] = data.get("keyword_states") or {}
    counts: dict[str, int] = data.get("keyword_video_counts") or {}
    current = data.get("current_keyword")
    done = data.get("keywords_done", 0)
    total = data.get("keywords_total", len(keywords))

    lines = [f"**Từ khóa mở rộng ({done}/{total})**"]
    for kw in keywords:
        status = states.get(kw, "pending")
        label = _KEYWORD_ICONS.get(status, "•")
        line = f"- {label} · `{kw}`"
        if status == "done" and kw in counts:
            line += f" — **{counts[kw]}** video"
        if status == "searching" or kw == current:
            line += " **← đang chạy**"
        lines.append(line)
    placeholder.markdown("\n".join(lines))


def _run_pipeline_worker(job: dict[str, Any]) -> None:
    """Background worker — only mutates job dict (no Streamlit calls)."""

    def on_progress(data: dict[str, Any]) -> None:
        job["progress_data"] = data
        message = data.get("message")
        if message:
            job["log"].append(message)

    try:
        kwargs: dict[str, Any] = {
            "keyword": job["keyword"],
            "mode": job["mode"],
            "scraper_config": job["scraper_config"],
            "progress": PipelineProgress(on_update=on_progress),
        }
        if job.get("pre_scraped_videos") is not None:
            kwargs["pre_scraped_videos"] = job["pre_scraped_videos"]
        if job.get("pre_expanded_keywords"):
            kwargs["pre_expanded_keywords"] = job["pre_expanded_keywords"]
        result = run_pipeline(**kwargs)
        job["result"] = result
        job["error"] = None
    except Exception as exc:
        job["result"] = None
        job["error"] = str(exc)
    finally:
        job["running"] = False
        job["finished_at"] = time.time()


def _start_forecaster_pipeline(
    *,
    keyword: str,
    expanded_keywords: list[str],
    pipeline_mode: str,
    scraper_config: ScraperConfig,
) -> None:
    """Run classic multi-keyword pipeline with AI Forecaster keywords (no pre-scrape)."""
    n_kw = len(expanded_keywords)
    st.session_state.last_error = None
    st.session_state.pipeline_job = {
        "running": True,
        "handled": False,
        "started_at": time.time(),
        "keyword": keyword.strip(),
        "mode": pipeline_mode,
        "scraper_config": scraper_config,
        "pre_expanded_keywords": expanded_keywords,
        "progress_data": {
            "fraction": 0.0,
            "message": f"Đang chạy pipeline với {n_kw} từ khóa AI Forecaster…",
            "current_step": 0,
            "total_steps": 3 + n_kw + (2 if pipeline_mode == "full" else 0),
            "expanded_keywords": expanded_keywords,
        },
        "log": [],
        "result": None,
        "error": None,
    }
    threading.Thread(
        target=_run_pipeline_worker,
        args=(st.session_state.pipeline_job,),
        daemon=True,
    ).start()


def _start_discovery_pipeline(
    *,
    keyword: str,
    videos: list[dict],
    expanded_keywords: list[str],
    pipeline_mode: str,
    scraper_config: ScraperConfig,
) -> None:
    st.session_state.last_error = None
    st.session_state.pipeline_job = {
        "running": True,
        "handled": False,
        "started_at": time.time(),
        "keyword": keyword.strip(),
        "mode": pipeline_mode,
        "scraper_config": scraper_config,
        "pre_scraped_videos": videos,
        "pre_expanded_keywords": expanded_keywords,
        "progress_data": {
            "fraction": 0.0,
            "message": "Đang khởi động pipeline (dữ liệu đã thu thập)…",
            "current_step": 0,
            "total_steps": 3 + (2 if pipeline_mode == "full" else 0),
        },
        "log": [],
        "result": None,
        "error": None,
    }
    threading.Thread(
        target=_run_pipeline_worker,
        args=(st.session_state.pipeline_job,),
        daemon=True,
    ).start()


def _render_pipeline_controls(*, topic_default: str, videos: list[dict], keywords: list[str]) -> None:
    """Shared enrichment / download controls for discovery tabs."""
    if not videos:
        st.caption("Chưa có video — chạy bước thu thập phía trên.")
        return

    st.markdown("#### Context Enrichment & Media Download")
    topic = st.text_input("Chủ đề lưu trend", value=topic_default, key=f"pipe_topic_{hash(topic_default) % 10**5}")
    pipeline_mode = st.selectbox(
        "Chế độ lưu",
        ["links", "full"],
        format_func=lambda x: "🔗 Link + text" if x == "links" else "📥 Full + Whisper",
        key=f"pipe_mode_{hash(topic_default) % 10**5}",
    )
    st.caption(f"Sẽ chạy pipeline với **{len(videos)}** video · **{len(keywords)}** từ khóa/nguồn.")

    job = st.session_state.pipeline_job
    pipeline_busy = bool(job and job.get("running"))
    if st.button(
        "▶ Chạy Context Enrichment & Download",
        type="primary",
        disabled=pipeline_busy or not topic.strip(),
        key=f"pipe_go_{hash(topic_default) % 10**5}",
    ):
        _start_discovery_pipeline(
            keyword=topic.strip(),
            videos=videos,
            expanded_keywords=keywords or [topic.strip()],
            pipeline_mode=pipeline_mode,
            scraper_config=ScraperConfig.from_env(),
        )
        st.rerun()


def _scraper_config_summary(config: ScraperConfig) -> str:
    per_kw = config.videos_per_platform * 3
    return (
        f"**{config.videos_per_platform}** video/nền tảng (YT Shorts + YT dài + TikTok) "
        f"→ tối đa **{per_kw}**/từ khóa · ≥ **{config.min_views:,}** views · "
        f"**{config.recency_days}** ngày"
    )


def _paint_progress_ui(
    data: dict[str, Any],
    *,
    elapsed_seconds: float,
    widgets: dict[str, Any],
) -> None:
    progress_bar = widgets["progress_bar"]
    status_box = widgets["status_box"]
    clock_display = widgets["clock_display"]
    step_metric = widgets["step_metric"]
    keyword_list_placeholder = widgets["keyword_list_placeholder"]
    log_box = widgets["log_box"]
    log_lines: list[str] = widgets.get("log_lines") or []

    progress_bar.progress(
        data.get("fraction", 0.0),
        text=data.get("message", "Đang chạy…"),
    )
    status_box.info(data.get("message", "Đang chạy…"))
    clock_display.markdown(
        f"### ⏱ {_format_clock(elapsed_seconds)}",
        help="Thời gian chạy thực",
    )
    step_metric.metric(
        "Bước",
        f"{data.get('current_step', 0)}/{data.get('total_steps', '?')}",
    )
    _render_keyword_list(data, keyword_list_placeholder)
    _render_matched_videos(data, widgets["matched_videos_placeholder"])
    _render_downloaded_videos(data, widgets["downloaded_videos_placeholder"])
    if log_lines:
        log_box.code("\n".join(log_lines[-10:]), language="text")


def _render_item_cards(
    items: list[dict],
    *,
    empty_message: str,
    link_label: str = "Mở link",
) -> None:
    if not items:
        st.info(empty_message)
        return

    for index, item in enumerate(items, start=1):
        title = item.get("title") or f"Mục {index}"
        text = (item.get("text") or "").strip()
        url = (item.get("url") or "").strip()

        with st.container(border=True):
            st.markdown(f"**#{index} · {title}**")
            st.write(text[:1500] + ("..." if len(text) > 1500 else ""))
            if url:
                st.link_button(link_label, url, key=f"txt_{index}_{hash(url) % 10**6}")


def _render_video_cards(videos: list[dict]) -> None:
    if not videos:
        st.warning("Chưa có video. Chạy pipeline ở **Thu thập mới**.")
        return

    rows = []
    for index, video in enumerate(videos, start=1):
        views = video.get("view_count")
        platform = video.get("platform") or "?"
        fmt = video.get("video_format") or "—"
        rows.append(
            {
                "#": index,
                "Tiêu đề": video.get("title") or "—",
                "Nền tảng": platform.upper(),
                "Định dạng": fmt,
                "Từ khóa": video.get("source_keyword") or "—",
                "Lượt xem": f"{views:,}" if isinstance(views, int) else "—",
                "Link": video.get("url") or "",
            }
        )
    st.dataframe(
        pd.DataFrame(rows),
        column_config={
            "Link": st.column_config.LinkColumn("Link", display_text="▶ Mở"),
        },
        use_container_width=True,
        hide_index=True,
    )


def _render_trend_detail(trend: dict) -> None:
    st.markdown(f"## {trend['label']}")
    st.caption(trend["trend_root"])

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Video", trend["video_count"])
    c2.metric("Nội dung text", trend.get("text_item_count", 0))
    c3.metric("Ngày", trend.get("date") or "—")
    c4.metric("Cập nhật", (trend.get("updated_at") or "—")[:10])

    tab_videos, tab_text = st.tabs(
        [
            f"🔗 Video ({trend['video_count']})",
            f"📝 Text + link ({trend.get('text_item_count', 0)})",
        ]
    )

    with tab_videos:
        _render_video_cards(trend["videos"])

    with tab_text:
        _render_item_cards(
            trend.get("text_items") or [],
            empty_message="Chưa có nội dung. Chạy lại pipeline.",
            link_label="Mở nguồn",
        )


# ——— Sidebar (radio nav — không chặn nút thu gọn) ———
with st.sidebar:
    st.markdown('<p class="sidebar-brand">DataCrawl</p>', unsafe_allow_html=True)
    st.markdown(
        '<p class="sidebar-tagline">Multi-keyword · YT + TikTok</p>',
        unsafe_allow_html=True,
    )
    st.divider()

    page_keys = list(PAGE_LABELS.keys())
    selected = st.radio(
        "Điều hướng",
        page_keys,
        format_func=lambda k: PAGE_LABELS[k],
        index=page_keys.index(st.session_state.page),
        label_visibility="collapsed",
    )
    st.session_state.page = selected

    trends = list_all_trends()

    if st.session_state.page == "storage" and trends:
        st.divider()
        st.markdown("**Chọn trend**")
        options = {t["id"]: t["label"] for t in trends}
        ids = list(options.keys())
        if st.session_state.selected_trend_id not in ids:
            st.session_state.selected_trend_id = ids[0]
        st.session_state.selected_trend_id = st.selectbox(
            "Trend",
            options=ids,
            format_func=lambda x: f"{options[x]} ({next(t['video_count'] for t in trends if t['id']==x)} video)",
            label_visibility="collapsed",
        )

    st.divider()
    st.caption(gpu_status_message())

page = st.session_state.page

if page == "storage":
    st.markdown("## Kho lưu trữ trend")

    if not trends:
        st.info("Chưa có dữ liệu. Vào **Thu thập mới** để chạy pipeline.")
    elif st.session_state.selected_trend_id:
        trend = next(
            (t for t in trends if t["id"] == st.session_state.selected_trend_id),
            load_trend_summary(DATA_TRENDS_DIR / st.session_state.selected_trend_id),
        )
        _render_trend_detail(trend)

elif page == "collect":
    st.markdown("## Thu thập trend mới")

    tab_ai, tab_board, tab_velocity = st.tabs(
        [
            "AI Keyword Forecaster",
            "Native Leaderboards",
            "KOL Velocity Tracker",
        ]
    )

    with tab_ai:
        st.markdown("### AI Keyword Forecaster")
        st.caption(
            f"~{RAW_KEYWORD_TARGET} từ khóa thô (pytrends + Suggest, lọc theo chủ đề) → "
            f"LLM top {REFINED_KEYWORD_COUNT} → pipeline"
        )

        seed_topic = st.text_input("Seed Topic", value="AI agent", key="ai_seed_topic")

        st.markdown("#### Cấu hình pipeline")
        ac1, ac2, ac3 = st.columns(3)
        with ac1:
            ai_videos_per_platform = st.number_input(
                "Video / nền tảng (YT Shorts, YT dài, TikTok)",
                1,
                10,
                5,
                key="ai_videos_per_platform",
                help="Mỗi từ khóa lấy tối đa N video trên từng nền tảng.",
            )
        with ac2:
            ai_min_views = st.number_input(
                "Lượt xem tối thiểu",
                10_000,
                1_000_000,
                50_000,
                step=10_000,
                key="ai_min_views",
            )
            ai_recency = st.selectbox(
                "Trong vòng", [7], format_func=lambda d: f"{d} ngày", key="ai_recency"
            )
        with ac3:
            ai_pipeline_mode = st.selectbox(
                "Chế độ lưu",
                ["links", "full"],
                format_func=lambda x: "🔗 Link + text" if x == "links" else "📥 Full + Whisper",
                key="ai_pipeline_mode",
            )
            if ai_pipeline_mode == "links":
                st.caption("Link + text: **không** tải file .mp4 — chỉ lưu link video.")
            else:
                st.caption(
                    "Full + Whisper: tải .mp4 vào `Videos/`, transcript + keyframes. "
                    "Cần **ffmpeg**. YouTube 18+: `YTDLP_COOKIES_FROM_BROWSER=chrome` (đóng Chrome nếu tải lỗi)."
                )

        ai_preview_config = ScraperConfig(
            recency_days=ai_recency,
            min_views=int(ai_min_views),
            keyword_count=REFINED_KEYWORD_COUNT,
            videos_per_platform=int(ai_videos_per_platform),
        )
        st.caption(_scraper_config_summary(ai_preview_config))

        pipeline_busy_ai = bool(
            st.session_state.pipeline_job and st.session_state.pipeline_job.get("running")
        )
        if st.button(
            f"▶ Discover {RAW_KEYWORD_TARGET} keywords → Top {REFINED_KEYWORD_COUNT} & Run Pipeline",
            type="primary",
            use_container_width=True,
            disabled=pipeline_busy_ai,
            key="ai_run_pipeline",
        ):
            if not seed_topic.strip():
                st.error("Vui lòng nhập Seed Topic.")
            else:
                try:
                    with st.spinner(
                        f"Thu thập ~{RAW_KEYWORD_TARGET} từ khóa, LLM chọn top "
                        f"{REFINED_KEYWORD_COUNT}, khởi động pipeline…"
                    ):
                        raw, refined = discover_keywords_for_pipeline(seed_topic.strip())
                        st.session_state.discovery_ai_raw_keywords = raw
                        st.session_state.discovery_ai_keywords = refined
                        scraper_config = ScraperConfig(
                            recency_days=ai_recency,
                            min_views=int(ai_min_views),
                            keyword_count=REFINED_KEYWORD_COUNT,
                            videos_per_platform=int(ai_videos_per_platform),
                        )
                        st.session_state.discovery_ai_scraper_config = scraper_config
                        _start_forecaster_pipeline(
                            keyword=seed_topic.strip(),
                            expanded_keywords=refined,
                            pipeline_mode=ai_pipeline_mode,
                            scraper_config=scraper_config,
                        )
                    st.rerun()
                except Exception as exc:
                    st.error(str(exc))

        if st.session_state.discovery_ai_keywords:
            st.markdown("#### Từ khóa đã chọn cho pipeline")
            st.write(
                ", ".join(f"`{k}`" for k in st.session_state.discovery_ai_keywords)
            )
            cfg = st.session_state.get("discovery_ai_scraper_config")
            if cfg:
                st.caption("Cấu hình scrape đang dùng: " + _scraper_config_summary(cfg))
            if st.session_state.discovery_ai_raw_keywords:
                with st.expander(
                    f"Từ khóa thô ({len(st.session_state.discovery_ai_raw_keywords)})",
                    expanded=False,
                ):
                    st.write(
                        ", ".join(
                            f"`{k}`" for k in st.session_state.discovery_ai_raw_keywords
                        )
                    )

    with tab_board:
        st.markdown("### Native Leaderboards")
        platform = st.selectbox("Platform", ["YouTube", "TikTok"], key="lb_platform")
        yt_category = st.selectbox(
            "Category",
            ["General", "Music", "Gaming"],
            key="lb_category",
        )

        if st.button("Fetch Leaderboard", type="primary", key="lb_fetch"):
            with st.spinner("Đang lấy leaderboard…"):
                try:
                    if platform == "YouTube":
                        cat_map = {"General": "now", "Music": "music", "Gaming": "gaming"}
                        st.session_state.discovery_leaderboard_videos = scrape_youtube_trending(
                            category=cat_map[yt_category],
                            limit=10,
                        )
                    else:
                        st.session_state.discovery_leaderboard_videos = scrape_tiktok_discover()
                    n = len(st.session_state.discovery_leaderboard_videos)
                    if n:
                        st.success(f"Lấy được **{n}** video.")
                    else:
                        st.warning("Không lấy được video — thử lại hoặc đổi nền tảng.")
                except Exception as exc:
                    st.error(str(exc))

        if st.session_state.discovery_leaderboard_videos:
            _render_video_cards(st.session_state.discovery_leaderboard_videos)

        _render_pipeline_controls(
            topic_default=f"{platform} {yt_category} trending",
            videos=st.session_state.discovery_leaderboard_videos,
            keywords=[f"{platform.lower()}_{yt_category.lower()}"],
        )

    with tab_velocity:
        st.markdown("### KOL Velocity Tracker")
        st.caption("View Velocity = views ÷ giờ kể từ khi đăng (video ≤ timeframe giờ)")

        channel_text = st.text_area(
            "Channel URLs (một URL mỗi dòng)",
            height=120,
            placeholder="https://www.youtube.com/@channel\nhttps://www.tiktok.com/@user",
            key="velocity_channels",
        )
        timeframe_hours = st.slider("Timeframe (Hours)", 6, 72, 48, key="velocity_hours")

        if st.button("Track Velocity", type="primary", key="velocity_track"):
            urls = [u.strip() for u in channel_text.splitlines() if u.strip()]
            if not urls:
                st.warning("Dán ít nhất một URL kênh.")
            else:
                with st.spinner("Đang tính view velocity…"):
                    df = calculate_channel_velocity(urls, max_age_hours=float(timeframe_hours))
                    st.session_state.discovery_velocity_df = df
                if df.empty:
                    st.warning("Không có video trong khung thời gian đã chọn.")
                else:
                    st.success(f"**{len(df)}** video trong {timeframe_hours}h gần nhất.")

        df_vel = st.session_state.discovery_velocity_df
        if df_vel is not None and not df_vel.empty:
            chart_df = df_vel[["title", "view_velocity"]].copy()
            chart_df["title"] = chart_df["title"].str.slice(0, 40)
            st.bar_chart(chart_df.set_index("title")["view_velocity"])
            st.dataframe(
                df_vel,
                column_config={
                    "url": st.column_config.LinkColumn("Video", display_text="▶ Mở"),
                },
                use_container_width=True,
                hide_index=True,
            )

        velocity_videos = (
            velocity_to_records(df_vel) if df_vel is not None and not df_vel.empty else []
        )
        _render_pipeline_controls(
            topic_default="KOL velocity",
            videos=velocity_videos,
            keywords=[f"velocity_{i}" for i in range(len(velocity_videos))],
        )

    st.markdown("#### Tiến trình pipeline")
    progress_bar = st.progress(0.0, text="Sẵn sàng")
    status_box = st.empty()

    timer_col, step_col = st.columns([1, 1])
    with timer_col:
        clock_display = st.empty()
    with step_col:
        step_metric = st.empty()

    kw_col, log_col = st.columns([1, 1])
    with kw_col:
        with st.container(border=True):
            keyword_list_placeholder = st.empty()
    with log_col:
        log_box = st.empty()

    matched_videos_placeholder = st.empty()
    downloaded_videos_placeholder = st.empty()

    ui_widgets = {
        "progress_bar": progress_bar,
        "status_box": status_box,
        "clock_display": clock_display,
        "step_metric": step_metric,
        "keyword_list_placeholder": keyword_list_placeholder,
        "log_box": log_box,
        "matched_videos_placeholder": matched_videos_placeholder,
        "downloaded_videos_placeholder": downloaded_videos_placeholder,
    }

    active_job = st.session_state.pipeline_job
    if active_job and active_job.get("scraper_config"):
        st.caption(
            "Cấu hình job: "
            + _scraper_config_summary(active_job["scraper_config"])
        )

    if st.session_state.last_error:
        st.error(st.session_state.last_error)

    job = st.session_state.pipeline_job

    if job and job.get("running"):
        elapsed = time.time() - job["started_at"]
        data = job.get("progress_data") or {
            "fraction": 0.0,
            "message": "Đang khởi động pipeline…",
            "current_step": 0,
            "total_steps": "?",
        }
        ui_widgets["log_lines"] = job.get("log") or []
        _paint_progress_ui(data, elapsed_seconds=elapsed, widgets=ui_widgets)
        time.sleep(0.4)
        st.rerun()

    if job and not job.get("running") and not job.get("handled"):
        job["handled"] = True
        elapsed = job.get("finished_at", time.time()) - job["started_at"]
        data = job.get("progress_data") or {}

        if job.get("error"):
            st.session_state.last_error = job["error"]
            progress_bar.progress(data.get("fraction", 0.0), text="Lỗi")
            status_box.error(job["error"])
            clock_display.markdown(f"### ⏱ {_format_clock(elapsed)} (dừng)")
            ui_widgets["log_lines"] = job.get("log") or []
            _paint_progress_ui(data, elapsed_seconds=elapsed, widgets=ui_widgets)
            st.session_state.pipeline_job = None
        else:
            result = job["result"]
            progress_bar.progress(1.0, text="Hoàn tất")
            clock_display.markdown(f"### ⏱ {_format_clock(elapsed)} (xong)")
            ctx = result.get("context_module") or {}
            n_images = len(result.get("web_image_paths") or [])
            trend_txt = result.get("trend_info_path") or ""
            status_box.success(
                f"Hoàn tất sau **{_format_clock(elapsed)}** · "
                f"**{len(result.get('scraped_videos', []))}** video · "
                f"**{len(result.get('expanded_keywords', []))}** từ khóa · "
                f"**{ctx.get('articles_scraped', 0)}** bài viết · "
                f"**{n_images}** ảnh web"
            )
            if trend_txt:
                st.caption(
                    f"Đã lưu `trend_info.txt` và ảnh vào thư mục trend "
                    f"(Module 2: {ctx.get('articles_scraped', 0)} bài, {n_images} ảnh)."
                )
            media = result.get("media_module") or {}
            if result.get("mode") == "full":
                n_dl = int(media.get("videos_downloaded") or 0)
                if n_dl:
                    st.success(f"Module 3: đã tải **{n_dl}** video vào thư mục `Videos/`.")
                elif result.get("media_download_error"):
                    st.warning(result["media_download_error"])
            scraped = result.get("scraped_videos") or []
            kw_counts: dict[str, int] = {}
            kw_videos: dict[str, list] = {}
            for video in scraped:
                kw = video.get("source_keyword") or "—"
                kw_counts[kw] = kw_counts.get(kw, 0) + 1
                kw_videos.setdefault(kw, []).append(video)
            media = result.get("media_module") or {}
            downloaded_rows = [
                {
                    "title": item.get("title") or "Video",
                    "url": item.get("url") or "",
                    "platform": item.get("platform") or "",
                    "video_path": item.get("video_path") or "",
                    "source_keyword": next(
                        (v.get("source_keyword") for v in scraped if v.get("url") == item.get("url")),
                        "",
                    ),
                }
                for item in media.get("processed") or []
                if item.get("status") == "ok"
            ]
            final_data = {
                "expanded_keywords": result.get("expanded_keywords") or [],
                "keyword_states": {
                    k: "done" for k in (result.get("expanded_keywords") or [])
                },
                "keyword_video_counts": kw_counts,
                "keyword_videos": kw_videos,
                "matched_videos": scraped,
                "downloaded_videos": downloaded_rows,
                "keywords_done": len(result.get("expanded_keywords") or []),
                "keywords_total": len(result.get("expanded_keywords") or []),
                "fraction": 1.0,
                "message": "Hoàn tất",
            }
            _render_keyword_list(final_data, keyword_list_placeholder)
            _render_matched_videos(final_data, matched_videos_placeholder)
            _render_downloaded_videos(final_data, downloaded_videos_placeholder)
            st.session_state.last_result = result
            st.session_state.selected_trend_id = Path(result["trend_root"]).name
            st.session_state.pipeline_job = None
            st.session_state.page = "storage"
        st.rerun()

else:
    st.markdown("## Nhật ký hệ thống")
    st.code(_read_logs(), language="text")
