"""Configurable parameters for multi-keyword trend scraping."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class ScraperConfig:
    recency_days: int = 7
    min_views: int = 50_000
    keyword_count: int = 10
    videos_per_keyword_search: int = 20
    top_videos_per_keyword: int = 10

    @classmethod
    def from_env(cls, **overrides: int) -> ScraperConfig:
        base = cls(
            recency_days=int(os.getenv("RECENCY_DAYS", "7")),
            min_views=int(os.getenv("MIN_VIEW_COUNT", "50000")),
            keyword_count=int(os.getenv("KEYWORD_COUNT", "10")),
            videos_per_keyword_search=int(os.getenv("VIDEOS_PER_KEYWORD_SEARCH", "20")),
            top_videos_per_keyword=int(os.getenv("TOP_VIDEOS_PER_KEYWORD", "10")),
        )
        if not overrides:
            return base
        fields = {f.name: getattr(base, f.name) for f in base.__dataclass_fields__.values()}
        fields.update(overrides)
        return cls(**fields)

    @property
    def max_total_videos(self) -> int:
        return self.keyword_count * self.top_videos_per_keyword
