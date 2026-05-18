"""Streamlit dashboard — polished sidebar, list views for video & text+link."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

from src.prefect_bootstrap import configure_prefect

configure_prefect()

from main_pipeline import run_pipeline
from src.device_utils import gpu_status_message
from src.pipeline_logging import LOG_FILE, setup_logging
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
    [data-testid="stSidebar"] {
        min-width: 300px !important;
        max-width: 300px !important;
        background: linear-gradient(165deg, #0f172a 0%, #1e293b 55%, #0f172a 100%);
        border-right: 1px solid #334155;
    }
    [data-testid="stSidebar"] .stMarkdown,
    [data-testid="stSidebar"] label,
    [data-testid="stSidebar"] p,
    [data-testid="stSidebar"] span { color: #e2e8f0 !important; }
    [data-testid="stSidebar"] hr { border-color: #334155; }
    .sidebar-brand {
        font-size: 1.45rem; font-weight: 700; color: #f8fafc !important;
        letter-spacing: -0.02em; margin-bottom: 0.15rem;
    }
    .sidebar-tagline { color: #94a3b8 !important; font-size: 0.82rem; }
    .nav-hint { color: #64748b !important; font-size: 0.75rem; margin-top: -0.4rem; }
    .item-card {
        background: #1e293b;
        border: 1px solid #334155;
        border-radius: 12px;
        padding: 1rem 1.1rem;
        margin-bottom: 0.65rem;
    }
    .item-card .card-title { color: #f1f5f9; font-weight: 600; font-size: 0.95rem; }
    .item-card .card-meta { color: #94a3b8; font-size: 0.78rem; margin: 0.25rem 0 0.5rem 0; }
    .item-card .card-text { color: #cbd5e1; font-size: 0.88rem; line-height: 1.55; }
    .item-card a { color: #38bdf8 !important; }
    div[data-testid="stMetric"] {
        background: #1e293b; border: 1px solid #334155; border-radius: 10px;
    }
    div[data-testid="stMetric"] label { color: #94a3b8 !important; }
    div[data-testid="stMetric"] [data-testid="stMetricValue"] { color: #f8fafc !important; }
</style>
"""
st.markdown(APP_CSS, unsafe_allow_html=True)

PAGES = {
    "storage": ("📚", "Kho lưu trữ", "Xem trend & video đã lưu"),
    "collect": ("➕", "Thu thập mới", "Chạy pipeline từ khóa"),
    "logs": ("📋", "Nhật ký", "Log hệ thống"),
}

if "page" not in st.session_state:
    st.session_state.page = "storage"
if "selected_trend_id" not in st.session_state:
    st.session_state.selected_trend_id = None
if "last_result" not in st.session_state:
    st.session_state.last_result = None
if "last_error" not in st.session_state:
    st.session_state.last_error = None


def _read_logs(tail_lines: int = 120) -> str:
    if not LOG_FILE.exists():
        return "Chưa có log."
    return "\n".join(
        LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()[-tail_lines:]
    )


def _type_label(item_type: str) -> str:
    return {
        "summary": "📝 Tóm tắt",
        "reference": "🔗 Tham khảo",
        "transcript": "🎙️ Transcript",
    }.get(item_type, "📄 Nội dung")


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
        meta = _type_label(item.get("type") or "")

        with st.container(border=True):
            head_l, head_r = st.columns([5, 1])
            with head_l:
                st.markdown(f"**#{index} · {title}**")
                st.caption(meta)
            with head_r:
                if url:
                    st.link_button(
                        link_label,
                        url,
                        key=f"txt_link_{index}_{hash(url) % 10**6}",
                    )
            display_text = text[:1500] + ("..." if len(text) > 1500 else "")
            st.write(display_text)
            if url:
                st.markdown(f"[{url}]({url})")


def _render_video_cards(videos: list[dict]) -> None:
    if not videos:
        st.warning("Chưa có video. Chạy pipeline ở **Thu thập mới**.")
        return

    rows = []
    for index, video in enumerate(videos, start=1):
        views = video.get("view_count")
        platform = video.get("platform") or "?"
        rows.append(
            {
                "#": index,
                "Tiêu đề": video.get("title") or "—",
                "Nền tảng": platform.upper(),
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
            empty_message="Chưa có nội dung dạng danh sách. Chạy lại pipeline để tạo `trend_content.json`.",
            link_label="Mở nguồn",
        )


# ——————————————————————————————————————————————
# Sidebar
# ——————————————————————————————————————————————
with st.sidebar:
    st.markdown('<p class="sidebar-brand">DataCrawl</p>', unsafe_allow_html=True)
    st.markdown(
        '<p class="sidebar-tagline">Trend pipeline · link + text</p>',
        unsafe_allow_html=True,
    )
    st.divider()

    for key, (icon, label, hint) in PAGES.items():
        if st.button(
            f"{icon}  {label}",
            key=f"nav_{key}",
            use_container_width=True,
            type="primary" if st.session_state.page == key else "secondary",
        ):
            st.session_state.page = key

    st.markdown(
        f'<p class="nav-hint">{PAGES[st.session_state.page][2]}</p>',
        unsafe_allow_html=True,
    )

    trends = list_all_trends()

    if st.session_state.page == "storage":
        st.divider()
        st.markdown("**Chọn trend**")
        if trends:
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
            st.caption(f"{len(trends)} trend trong kho")
        else:
            st.caption("Chưa có trend nào.")

    if st.session_state.page == "collect":
        st.divider()
        st.markdown("**Gợi ý**")
        st.caption("Dùng chế độ Link + text để lưu nhanh, không tải file video.")

    st.divider()
    st.caption(gpu_status_message()[:80] + "…" if len(gpu_status_message()) > 80 else gpu_status_message())

# ——————————————————————————————————————————————
# Pages
# ——————————————————————————————————————————————
page = st.session_state.page

if page == "storage":
    st.markdown("## Kho lưu trữ trend")
    st.caption("Danh sách trend ở sidebar · video và text+link hiển thị dạng bảng / thẻ.")

    if not trends:
        st.info("Chưa có dữ liệu. Vào **Thu thập mới** (sidebar) để chạy pipeline.")
    elif st.session_state.selected_trend_id:
        trend = next(
            (t for t in trends if t["id"] == st.session_state.selected_trend_id),
            load_trend_summary(DATA_TRENDS_DIR / st.session_state.selected_trend_id),
        )
        _render_trend_detail(trend)

        with st.expander("Bảng tổng quan tất cả trend"):
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Trend": t["label"],
                            "Video": t["video_count"],
                            "Text": t.get("text_item_count", 0),
                            "Cập nhật": t.get("updated_at", "—"),
                        }
                        for t in trends
                    ]
                ),
                use_container_width=True,
                hide_index=True,
            )

elif page == "collect":
    st.markdown("## Thu thập trend mới")

    keyword = st.text_input("Từ khóa trend", value="AI tools")
    col_a, col_b = st.columns(2)
    with col_a:
        video_limit = st.slider("Số video tối đa", 3, 15, 8)
    with col_b:
        pipeline_mode = st.selectbox(
            "Chế độ lưu",
            ["links", "full"],
            format_func=lambda x: "🔗 Link + text (khuyến nghị)" if x == "links" else "📥 Full + Whisper",
        )

    if st.session_state.last_error:
        st.error(st.session_state.last_error)

    if st.button("▶ Bắt đầu pipeline", type="primary", use_container_width=True):
        if not keyword.strip():
            st.error("Vui lòng nhập từ khóa.")
        else:
            st.session_state.last_error = None
            with st.spinner("Đang thu thập…"):
                try:
                    result = run_pipeline(
                        keyword=keyword.strip(),
                        video_limit=video_limit,
                        mode=pipeline_mode,
                    )
                    st.session_state.last_result = result
                    st.session_state.selected_trend_id = Path(result["trend_root"]).name
                    st.session_state.page = "storage"
                    st.success("Hoàn tất! Chuyển sang **Kho lưu trữ**.")
                    st.rerun()
                except Exception as exc:
                    st.session_state.last_error = str(exc)
                    st.error(str(exc))

else:
    st.markdown("## Nhật ký hệ thống")
    st.code(_read_logs(), language="text")
