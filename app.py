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
from src.trend_reader import DATA_TRENDS_DIR, list_all_trends, load_trend_summary

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


def _read_logs(tail_lines: int = 120) -> str:
    if not LOG_FILE.exists():
        return "Chưa có log."
    return "\n".join(
        LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()[-tail_lines:]
    )


def _format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    if seconds < 60:
        return f"{seconds:.0f}s"
    return f"{seconds / 60:.1f} phút"


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
        result = run_pipeline(
            keyword=job["keyword"],
            mode=job["mode"],
            scraper_config=job["scraper_config"],
            progress=PipelineProgress(on_update=on_progress),
        )
        job["result"] = result
        job["error"] = None
    except Exception as exc:
        job["result"] = None
        job["error"] = str(exc)
    finally:
        job["running"] = False
        job["finished_at"] = time.time()


def _paint_progress_ui(
    data: dict[str, Any],
    *,
    elapsed_seconds: float,
    widgets: dict[str, Any],
) -> None:
    progress_bar = widgets["progress_bar"]
    status_box = widgets["status_box"]
    clock_display = widgets["clock_display"]
    elapsed_metric = widgets["elapsed_metric"]
    eta_metric = widgets["eta_metric"]
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
    elapsed_metric.metric("Đã chạy", _format_duration(elapsed_seconds))
    eta_metric.metric("Ước tính còn", _format_duration(data.get("eta_seconds")))
    step_metric.metric(
        "Bước",
        f"{data.get('current_step', 0)}/{data.get('total_steps', '?')}",
    )
    _render_keyword_list(data, keyword_list_placeholder)
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

    keyword = st.text_input("Chủ đề / từ khóa gốc", value="AI agent")

    st.markdown("#### Cấu hình thu thập")
    c1, c2, c3 = st.columns(3)
    with c1:
        keyword_count = st.number_input("Số từ khóa (LLM)", 3, 20, 10)
        top_per_kw = st.number_input("Top video / từ khóa", 3, 20, 10)
    with c2:
        pool_per_kw = st.number_input("Pool tìm kiếm / từ khóa", 10, 50, 20)
        min_views = st.number_input("Lượt xem tối thiểu", 10_000, 1_000_000, 50_000, step=10_000)
    with c3:
        recency_days = st.selectbox("Trong vòng", [7], format_func=lambda d: f"{d} ngày")
        pipeline_mode = st.selectbox(
            "Chế độ lưu",
            ["links", "full"],
            format_func=lambda x: "🔗 Link + text" if x == "links" else "📥 Full + Whisper",
        )

    st.caption(
        f"Mỗi chủ đề → **{keyword_count}** từ khóa (LLM) · mỗi từ khóa lấy tối đa "
        f"**{pool_per_kw}** video (YT Shorts, YT dài, TikTok) · giữ **{top_per_kw}** "
        f"video view cao nhất · ≥ **{min_views:,}** views · **{recency_days}** ngày."
    )

    st.markdown("#### Tiến trình pipeline")
    progress_bar = st.progress(0.0, text="Sẵn sàng")
    status_box = st.empty()

    timer_col, metrics_col = st.columns([1, 2])
    with timer_col:
        clock_display = st.empty()
    with metrics_col:
        m1, m2, m3 = st.columns(3)
        elapsed_metric = m1.empty()
        eta_metric = m2.empty()
        step_metric = m3.empty()

    kw_col, log_col = st.columns([1, 1])
    with kw_col:
        with st.container(border=True):
            keyword_list_placeholder = st.empty()
    with log_col:
        log_box = st.empty()

    ui_widgets = {
        "progress_bar": progress_bar,
        "status_box": status_box,
        "clock_display": clock_display,
        "elapsed_metric": elapsed_metric,
        "eta_metric": eta_metric,
        "step_metric": step_metric,
        "keyword_list_placeholder": keyword_list_placeholder,
        "log_box": log_box,
    }

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
            status_box.success(
                f"Hoàn tất sau **{_format_clock(elapsed)}** · "
                f"**{len(result.get('scraped_videos', []))}** video · "
                f"**{len(result.get('expanded_keywords', []))}** từ khóa"
            )
            final_data = {
                "expanded_keywords": result.get("expanded_keywords") or [],
                "keyword_states": {
                    k: "done" for k in (result.get("expanded_keywords") or [])
                },
                "keyword_video_counts": {},
                "keywords_done": len(result.get("expanded_keywords") or []),
                "keywords_total": len(result.get("expanded_keywords") or []),
                "fraction": 1.0,
                "message": "Hoàn tất",
            }
            _render_keyword_list(final_data, keyword_list_placeholder)
            st.session_state.last_result = result
            st.session_state.selected_trend_id = Path(result["trend_root"]).name
            st.session_state.pipeline_job = None
            st.session_state.page = "storage"
        st.rerun()

    pipeline_busy = bool(job and job.get("running"))
    if st.button(
        "▶ Bắt đầu pipeline",
        type="primary",
        use_container_width=True,
        disabled=pipeline_busy,
    ):
        if not keyword.strip():
            st.error("Vui lòng nhập chủ đề.")
        else:
            st.session_state.last_error = None
            scraper_config = ScraperConfig(
                recency_days=recency_days,
                min_views=int(min_views),
                keyword_count=int(keyword_count),
                videos_per_keyword_search=int(pool_per_kw),
                top_videos_per_keyword=int(top_per_kw),
            )
            st.session_state.pipeline_job = {
                "running": True,
                "handled": False,
                "started_at": time.time(),
                "keyword": keyword.strip(),
                "mode": pipeline_mode,
                "scraper_config": scraper_config,
                "progress_data": {
                    "fraction": 0.0,
                    "message": "Đang khởi động pipeline…",
                    "current_step": 0,
                    "total_steps": 3 + int(keyword_count),
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
            st.rerun()

else:
    st.markdown("## Nhật ký hệ thống")
    st.code(_read_logs(), language="text")
