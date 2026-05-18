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
    current_keyword: str | None = None
    is_running: bool = False

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
        self.current_keyword = None
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

    def complete_keyword_search(self, keyword: str, video_count: int) -> None:
        self.keyword_states[keyword] = "done"
        self.keyword_video_counts[keyword] = video_count
        if self.current_keyword == keyword:
            self.current_keyword = None
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
    def fraction(self) -> float:
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
            "current_keyword": self.current_keyword,
            "keywords_done": done_kw,
            "keywords_total": len(self.expanded_keywords),
        }
