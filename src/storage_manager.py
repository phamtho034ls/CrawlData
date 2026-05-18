"""Directory structure management for trend data output."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path


class StorageManagerError(Exception):
    """Raised when storage operations cannot be completed safely."""


class StorageManager:
    """Creates and tracks standardized folders for a trend topic."""

    def __init__(
        self,
        trend_topic: str,
        base_dir: str | Path | None = None,
    ) -> None:
        if not trend_topic or not trend_topic.strip():
            raise StorageManagerError("trend_topic must be a non-empty string.")

        self.trend_topic = self._sanitize_topic(trend_topic.strip())
        project_root = Path(__file__).resolve().parent.parent
        self.base_dir = Path(base_dir) if base_dir else project_root / "data_trends"
        self.date_str = date.today().isoformat()
        self.folder_name = f"{self.date_str}_Topic_{self.trend_topic}"
        self.trend_root = self.base_dir / self.folder_name
        self.videos_dir = self.trend_root / "Videos"
        self.images_dir = self.trend_root / "Images"

    @staticmethod
    def _sanitize_topic(topic: str) -> str:
        sanitized = re.sub(r"[^\w\s-]", "", topic, flags=re.UNICODE)
        sanitized = re.sub(r"[\s_-]+", "_", sanitized.strip())
        return sanitized or "unknown_topic"

    def create_structure(self) -> dict[str, str]:
        """
        Create trend folder with Videos/ and Images/ subdirectories.

        Raises StorageManagerError if the trend folder already exists.
        Returns absolute paths for all created directories.
        """
        if self.trend_root.exists():
            raise StorageManagerError(
                f"Trend folder already exists and will not be overwritten: {self.trend_root}"
            )

        try:
            self.base_dir.mkdir(parents=True, exist_ok=True)
            self.trend_root.mkdir(parents=False, exist_ok=False)
            self.videos_dir.mkdir(parents=False, exist_ok=False)
            self.images_dir.mkdir(parents=False, exist_ok=False)
        except FileExistsError as exc:
            raise StorageManagerError(
                f"Directory already exists during creation: {exc}"
            ) from exc
        except OSError as exc:
            raise StorageManagerError(f"Failed to create directories: {exc}") from exc

        return self._paths_dict()

    def resolve_structure(self) -> dict[str, str]:
        """
        Create a new structure, or return paths if a valid one already exists.

        Allows resuming a pipeline run after a partial failure on the same day/topic.
        """
        self.base_dir.mkdir(parents=True, exist_ok=True)

        if not self.trend_root.exists():
            return self.create_structure()

        if not self.videos_dir.is_dir() or not self.images_dir.is_dir():
            raise StorageManagerError(
                f"Incomplete trend folder (missing Videos/ or Images/): {self.trend_root}"
            )

        return self._paths_dict()

    def _paths_dict(self) -> dict[str, str]:
        return {
            "trend_root": str(self.trend_root.resolve()),
            "videos_dir": str(self.videos_dir.resolve()),
            "images_dir": str(self.images_dir.resolve()),
        }

    @property
    def trend_info_path(self) -> Path:
        return self.trend_root / "trend_info.txt"

    @property
    def transcript_path(self) -> Path:
        return self.trend_root / "transcript.txt"

    @property
    def video_links_json_path(self) -> Path:
        return self.trend_root / "video_links.json"

    @property
    def video_links_md_path(self) -> Path:
        return self.trend_root / "video_links.md"
