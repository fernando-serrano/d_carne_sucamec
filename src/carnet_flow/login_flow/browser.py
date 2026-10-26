from __future__ import annotations

import logging

from playwright.sync_api import Browser, BrowserContext, Page, Playwright

from ..config import Settings


def open_browser(playwright: Playwright, settings: Settings) -> tuple[Browser, BrowserContext, Page]:
    browser = playwright.chromium.launch(
        headless=settings.headless,
        args=[
            "--disable-infobars",
            "--disable-backgrounding-occluded-windows",
            "--disable-renderer-backgrounding",
        ],
    )
    # accept_downloads: en headless el PDF del carné llega como descarga, no como visor.
    context = browser.new_context(no_viewport=True, ignore_https_errors=True, accept_downloads=True)
    page = context.new_page()
    return browser, context, page


def close_browser(browser: Browser | None, context: BrowserContext | None, logger: logging.Logger | None = None) -> None:
    for target in (context, browser):
        try:
            if target is not None:
                target.close()
        except Exception:
            pass
    if logger is not None and (browser is not None or context is not None):
        logger.info("Navegador cerrado")
