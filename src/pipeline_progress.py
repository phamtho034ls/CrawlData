"""Progress reporting for pipeline runs (Streamlit / logs)."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

KeywordStatus = Literal["pending", "searching", "done", "error"]


@dataclass
class PipelineProgress:
    """Track pipeline steps, keywords, and optional UI callback."""

    on_update: Callable[[dict[str, Any]], None] | None = None
    current_step: int = 0
    total_steps: int = 1
    message: str = "Khởi tạo…"
    started_at: float = field(default_factory=time.time)
    step_started_at: float = field(default_factory=time.time)
    expanded_keywords: list[str] = field(default_factory=list)
    keyword_states: dict[str, str] = field(default_factory=dict)
    keyword_video_counts: dict[str, int] = field(default_factory=dict)
    keyword_videos: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    downloaded_videos: list[dict[str, Any]] = field(default_factory=list)
    current_keyword: str | None = None
    is_running: bool = False
    # Module 3: download + analyze (minute JSON, clips, keyframes)
    in_media_module: bool = False
    media_total: int = 0
    media_done: int = 0
    media_videos: list[dict[str, Any]] = field(default_factory=list)
    media_current_index: int = 0
    media_phase: str = ""
    media_phase_message: str = ""

    def start(self, total_steps: int, message: str = "Bắt đầu pipeline") -> None:
        self.total_steps = max(1, total_steps)
        self.current_step = 0
        self.message = message
        self.started_at = time.time()
        self.step_started_at = time.time()
        self.is_running = True
        self.expanded_keywords = []
        self.keyword_states = {}
        self.keyword_video_counts = {}
        self.keyword_videos = {}
        self.downloaded_videos = []
        self.current_keyword = None
        self.in_media_module = False
        self.media_total = 0
        self.media_done = 0
        self.media_videos = []
        self.media_current_index = 0
        self.media_phase = ""
        self.media_phase_message = ""
        self._emit()

    def finish(self, message: str = "Hoàn tất pipeline") -> None:
        self.is_running = False
        self.current_keyword = None
        self.message = message
        self._emit()

    def step(self, message: str, *, step: int | None = None) -> None:
        if step is not None:
            self.current_step = step
        else:
            self.current_step = min(self.current_step + 1, self.total_steps)
        self.message = message
        self.step_started_at = time.time()
        self._emit()

    def tick(self) -> None:
        """Refresh elapsed time only (for live timer during long steps)."""
        if self.is_running and self.on_update:
            self.on_update(self.to_dict())

    def set_keywords(self, keywords: list[str]) -> None:
        self.expanded_keywords = list(keywords)
        self.keyword_states = {kw: "pending" for kw in keywords}
        self.keyword_video_counts = {}
        self.keyword_videos = {}
        self.downloaded_videos = []
        self.current_keyword = None
        self.message = f"Đã tạo {len(keywords)} từ khóa — bắt đầu tìm video…"
        self._emit()

    def begin_keyword_search(
        self,
        keyword: str,
        index: int,
        total: int,
        *,
        step: int | None = None,
    ) -> None:
        self.current_keyword = keyword
        self.keyword_states[keyword] = "searching"
        if step is not None:
            self.current_step = min(step, self.total_steps)
        else:
            self.current_step = min(self.current_step + 1, self.total_steps)
        self.message = f"Đang tìm [{index}/{total}]: «{keyword}»"
        self._emit()

    def complete_keyword_search(
        self,
        keyword: str,
        video_count: int,
        videos: list[dict[str, Any]] | None = None,
    ) -> None:
        self.keyword_states[keyword] = "done"
        self.keyword_video_counts[keyword] = video_count
        if videos:
            self.keyword_videos[keyword] = list(videos)
        if self.current_keyword == keyword:
            self.current_keyword = None
        self._emit()

    def add_downloaded_video(self, video: dict[str, Any]) -> None:
        """Append a successfully downloaded file (Module 3)."""
        self.downloaded_videos.append(dict(video))
        self._emit()

    def set_downloaded_videos(self, videos: list[dict[str, Any]]) -> None:
        self.downloaded_videos = [dict(v) for v in videos]
        self._emit()

    def start_media_module(self, total: int) -> None:
        """Begin Module 3 (download + speech-to-text + clips + keyframes)."""
        self.in_media_module = True
        self.media_total = max(0, total)
        self.media_done = 0
        self.media_videos = []
        self.media_current_index = 0
        self.media_phase = "starting"
        self.media_phase_message = ""
        self.message = f"Module 3: tải & phân tích {total} video…"
        self._emit()

    def _find_media_row(self, index: int) -> dict[str, Any] | None:
        for row in self.media_videos:
            if row.get("index") == index:
                return row
        return None

    def begin_media_video(
        self,
        index: int,
        total: int,
        *,
        title: str,
        url: str,
        platform: str,
        source_keyword: str = "",
    ) -> None:
        self.media_current_index = index
        self.media_total = total
        self.media_phase = "downloading"
        self.media_phase_message = "Đang tải video…"
        short = (title or "Video")[:56]
        self.message = f"Module 3 [{index}/{total}]: đang tải «{short}»"
        row = {
            "index": index,
            "title": title,
            "url": url,
            "platform": platform,
            "source_keyword": source_keyword,
            "status": "downloading",
            "phase_message": self.media_phase_message,
            "video_path": "",
            "content_json": "",
            "clips_dir": "",
            "minute_count": 0,
            "keyframes_count": 0,
        }
        existing = self._find_media_row(index)
        if existing:
            existing.update(row)
        else:
            self.media_videos.append(row)
        self._emit()

    def set_media_phase(
        self,
        index: int,
        phase: str,
        message: str,
        *,
        total: int | None = None,
    ) -> None:
        if total is not None:
            self.media_total = total
        self.media_current_index = index
        self.media_phase = phase
        self.media_phase_message = message
        row = self._find_media_row(index)
        if row:
            row["status"] = phase
            row["phase_message"] = message
        phase_labels = {
            "downloading": "đang tải",
            "analyzing": "đang phân tích (STT + JSON + clip/phút)",
            "keyframes": "đang trích keyframe",
            "done": "hoàn tất",
            "download_failed": "tải thất bại",
            "analyze_failed": "phân tích lỗi một phần",
        }
        label = phase_labels.get(phase, phase)
        short = (row.get("title") if row else "") or "Video"
        short = short[:56]
        self.message = f"Module 3 [{index}/{self.media_total}]: {label} — «{short}»"
        self._emit()

    def complete_media_video(self, index: int, details: dict[str, Any]) -> None:
        row = self._find_media_row(index)
        if not row:
            return
        row.update(details)
        row["status"] = details.get("status", "done")
        row["phase_message"] = details.get("phase_message", "Hoàn tất")
        self.media_done = sum(
            1
            for v in self.media_videos
            if v.get("status")
            in ("done", "download_failed", "analyze_failed")
        )
        self.media_phase = row["status"]
        self.media_phase_message = row["phase_message"]
        self._emit()

    def finish_media_module(self, downloaded: list[dict[str, Any]]) -> None:
        self.in_media_module = False
        self.media_phase = "finished"
        self.media_done = self.media_total
        self.set_downloaded_videos(downloaded)
        ok = sum(1 for v in self.media_videos if v.get("status") == "done")
        self.message = f"Module 3 xong: {ok}/{self.media_total} video thành công"
        self._emit()

    def fail_keyword_search(self, keyword: str) -> None:
        self.keyword_states[keyword] = "error"
        self.keyword_video_counts[keyword] = 0
        if self.current_keyword == keyword:
            self.current_keyword = None
        self._emit()

    def _emit(self) -> None:
        if self.on_update:
            self.on_update(self.to_dict())

    @property
    def media_fraction(self) -> float:
        if self.media_total <= 0:
            return 0.0
        return min(1.0, self.media_done / self.media_total)

    @property
    def fraction(self) -> float:
        if self.in_media_module and self.media_total > 0:
            base = 0.82 if self.expanded_keywords else 0.55
            return min(1.0, base + self.media_fraction * (1.0 - base))
        if self.expanded_keywords:
            kw_done = sum(
                1 for s in self.keyword_states.values() if s in ("done", "error")
            )
            kw_total = len(self.expanded_keywords)
            kw_part = kw_done / max(1, kw_total)
            step_part = self.current_step / max(1, self.total_steps)
            return min(1.0, 0.15 + kw_part * 0.7 + step_part * 0.15)
        return min(1.0, self.current_step / max(1, self.total_steps))

    @property
    def elapsed_seconds(self) -> float:
        return time.time() - self.started_at

    @property
    def eta_seconds(self) -> float | None:
        if self.current_step <= 0:
            return None
        rate = self.elapsed_seconds / self.current_step
        remaining = self.total_steps - self.current_step
        return rate * remaining

    def to_dict(self) -> dict[str, Any]:
        done_kw = sum(1 for s in self.keyword_states.values() if s == "done")
        return {
            "current_step": self.current_step,
            "total_steps": self.total_steps,
            "fraction": self.fraction,
            "message": self.message,
            "elapsed_seconds": self.elapsed_seconds,
            "eta_seconds": self.eta_seconds,
            "is_running": self.is_running,
            "expanded_keywords": list(self.expanded_keywords),
            "keyword_states": dict(self.keyword_states),
            "keyword_video_counts": dict(self.keyword_video_counts),
            "keyword_videos": {k: list(v) for k, v in self.keyword_videos.items()},
            "downloaded_videos": list(self.downloaded_videos),
            "matched_videos": self.all_matched_videos(),
            "current_keyword": self.current_keyword,
            "keywords_done": done_kw,
            "keywords_total": len(self.expanded_keywords),
            "in_media_module": self.in_media_module,
            "media_total": self.media_total,
            "media_done": self.media_done,
            "media_fraction": self.media_fraction,
            "media_videos": list(self.media_videos),
            "media_current_index": self.media_current_index,
            "media_phase": self.media_phase,
            "media_phase_message": self.media_phase_message,
        }

    def all_matched_videos(self) -> list[dict[str, Any]]:
        """Flatten per-keyword matches preserving discovery order."""
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        for kw in self.expanded_keywords:
            for video in self.keyword_videos.get(kw, []):
                key = (video.get("url") or video.get("video_id") or "").lower()
                if key and key in seen:
                    continue
                if key:
                    seen.add(key)
                out.append(video)
        return out
