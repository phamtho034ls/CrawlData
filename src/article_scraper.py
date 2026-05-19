"""Scrape article text and images from web pages."""

from __future__ import annotations

import logging
import mimetypes
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
import trafilatura
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
DEFAULT_TIMEOUT = 20
_REQUEST_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,vi;q=0.8",
}


def _absolute_url(base: str, src: str) -> str:
    if not src:
        return ""
    if src.startswith("//"):
        return "https:" + src
    if src.startswith("http"):
        return src
    return urljoin(base, src)


def _image_extension(url: str, content_type: str | None) -> str:
    path = urlparse(url).path.lower()
    for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
        if path.endswith(ext):
            return ext
    if content_type:
        guessed = mimetypes.guess_extension(content_type.split(";")[0].strip())
        if guessed:
            return guessed
    return ".jpg"


def _extract_other_images(soup: BeautifulSoup, page_url: str, *, limit: int = 3) -> list[str]:
    scored: list[tuple[int, str]] = []
    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src") or img.get("data-lazy-src")
        if not src or str(src).startswith("data:"):
            continue
        full = _absolute_url(page_url, str(src).strip())
        if not full.startswith("http"):
            continue
        width = 0
        for attr in ("width", "data-width"):
            raw = img.get(attr)
            if raw and str(raw).isdigit():
                width = max(width, int(raw))
        scored.append((width, full))

    scored.sort(key=lambda x: x[0], reverse=True)
    out: list[str] = []
    seen: set[str] = set()
    for _w, url in scored:
        if url in seen:
            continue
        seen.add(url)
        out.append(url)
        if len(out) >= limit:
            break
    return out


def scrape_article_data(url: str) -> dict[str, Any]:
    """
    Scrape main article text (trafilatura) and images (og:image + up to 3 img tags).
    """
    url = url.strip()
    result: dict[str, Any] = {
        "url": url,
        "main_text": "",
        "og_image": "",
        "other_images": [],
    }
    if not url:
        return result

    try:
        downloaded = trafilatura.fetch_url(url)
        if downloaded:
            text = trafilatura.extract(downloaded, include_comments=False, include_tables=False)
            result["main_text"] = (text or "").strip()
    except Exception as exc:
        logger.debug("trafilatura failed for %s: %s", url, exc)

    try:
        response = requests.get(url, headers=_REQUEST_HEADERS, timeout=DEFAULT_TIMEOUT)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        og = soup.find("meta", property="og:image") or soup.find(
            "meta", attrs={"name": "og:image"}
        )
        if og and og.get("content"):
            result["og_image"] = _absolute_url(url, og["content"].strip())

        result["other_images"] = _extract_other_images(soup, url, limit=3)
    except Exception as exc:
        logger.warning("HTML parse failed for %s: %s", url, exc)

    return result


def download_web_images(
    scraped_articles: list[dict[str, Any]],
    images_dir: str | Path,
) -> list[str]:
    """
    Download og_image and other_images from scraped articles into Images/.
    Files are named web_img_1.jpg, web_img_2.jpg, ...
    """
    images_path = Path(images_dir)
    images_path.mkdir(parents=True, exist_ok=True)

    url_queue: list[str] = []
    seen: set[str] = set()
    for article in scraped_articles:
        for key in ("og_image",):
            u = (article.get(key) or "").strip()
            if u and u not in seen:
                seen.add(u)
                url_queue.append(u)
        for u in article.get("other_images") or []:
            u = str(u).strip()
            if u and u not in seen:
                seen.add(u)
                url_queue.append(u)

    saved: list[str] = []
    index = 0
    for image_url in url_queue:
        index += 1
        try:
            resp = requests.get(
                image_url,
                headers=_REQUEST_HEADERS,
                timeout=DEFAULT_TIMEOUT,
            )
            resp.raise_for_status()
            ext = _image_extension(image_url, resp.headers.get("Content-Type"))
            filename = f"web_img_{index}{ext}"
            dest = images_path / filename
            dest.write_bytes(resp.content)
            saved.append(str(dest.resolve()))
        except Exception as exc:
            logger.warning("Image download failed %s: %s", image_url, exc)

    return saved
