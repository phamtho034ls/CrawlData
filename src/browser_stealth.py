"""Playwright browser context with basic anti-bot hardening."""

from __future__ import annotations

import random
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.sync_api import Browser, BrowserContext, Page, Playwright

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

STEALTH_INIT_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
window.chrome = { runtime: {} };
Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
"""


def human_delay(min_s: float = 0.8, max_s: float = 2.2) -> None:
    time.sleep(random.uniform(min_s, max_s))


def launch_stealth_browser(playwright: Playwright) -> tuple[Browser, BrowserContext]:
    browser = playwright.chromium.launch(
        headless=True,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
            "--no-sandbox",
        ],
    )
    context = browser.new_context(
        user_agent=USER_AGENT,
        viewport={"width": 1366, "height": 768},
        locale="en-US",
        timezone_id="Asia/Ho_Chi_Minh",
        color_scheme="light",
        extra_http_headers={
            "Accept-Language": "en-US,en;q=0.9,vi;q=0.8",
        },
    )
    context.add_init_script(STEALTH_INIT_SCRIPT)
    return browser, context


def human_scroll(page: Page, rounds: int = 8) -> None:
    for _ in range(rounds):
        delta = random.randint(400, 900)
        page.mouse.wheel(0, delta)
        human_delay(0.6, 1.4)


def goto_like_human(page: Page, url: str, *, timeout: int = 60000) -> None:
    page.goto(url, wait_until="domcontentloaded", timeout=timeout)
    human_delay(1.2, 2.5)
