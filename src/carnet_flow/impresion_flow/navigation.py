from __future__ import annotations

import logging
import time

from playwright.sync_api import Page

from .selectors import MENU, TEXTO_BANDEJA, TEXTO_CARNE, TEXTO_DSSP, VISTA

_NORMALIZE_JS = """(value) => String(value || '')
    .normalize('NFD').replace(/[\\u0300-\\u036f]/g, '')
    .replace(/\\s+/g, ' ').trim().toUpperCase()"""


def wait_primefaces_ajax(page: Page, timeout_ms: int = 7000) -> None:
    try:
        page.wait_for_function(
            """() => {
                try {
                    if (!window.PrimeFaces || !PrimeFaces.ajax || !PrimeFaces.ajax.Queue) return true;
                    const queue = PrimeFaces.ajax.Queue;
                    if (typeof queue.isEmpty === 'function') return queue.isEmpty();
                    const requests = queue.requests || queue.queue || [];
                    return !requests || requests.length === 0;
                } catch (e) { return true; }
            }""",
            timeout=max(1000, int(timeout_ms)),
        )
    except Exception:
        pass


def vista_bandeja_visible(page: Page, timeout_ms: int = 6000) -> bool:
    deadline = time.time() + max(600, timeout_ms) / 1000.0
    while time.time() < deadline:
        for selector in (VISTA["buscar_por_widget"], VISTA["filtro"]):
            try:
                if page.locator(selector).first.is_visible(timeout=150):
                    return True
            except Exception:
                pass
        page.wait_for_timeout(180)
    return False


def _click_por_texto(page: Page) -> bool:
    """Expande DSSP y CARNÉ si hace falta y acciona BANDEJA DE IMPRESIÓN dentro de DSSP."""
    return bool(
        page.evaluate(
            f"""([dssp, carne, bandeja]) => {{
                const norm = {_NORMALIZE_JS};
                const header = Array.from(document.querySelectorAll('h3.ui-panelmenu-header'))
                    .find((h) => norm(h.textContent) === dssp);
                let scope = document;
                if (header) {{
                    if (String(header.getAttribute('aria-expanded')).toLowerCase() !== 'true') header.click();
                    scope = header.nextElementSibling || header.parentElement || document;
                }}
                const links = Array.from(scope.querySelectorAll('a.ui-menuitem-link, a[onclick]'));
                const target = links.find((a) => norm(a.textContent) === bandeja);
                if (target) {{ target.click(); return true; }}
                const sub = links.find((a) => norm(a.textContent) === carne);
                if (sub) sub.click();
                return false;
            }}""",
            [TEXTO_DSSP, TEXTO_CARNE, TEXTO_BANDEJA],
        )
    )


def navigate_to_bandeja_impresion(page: Page, logger: logging.Logger) -> None:
    try:
        page.wait_for_load_state("domcontentloaded", timeout=8000)
    except Exception:
        pass
    page.locator(MENU["menu_root"]).first.wait_for(state="visible", timeout=15000)

    # El submenú CARNÉ puede necesitar un segundo clic tras expandirse.
    for intento in range(1, 4):
        if _click_por_texto(page):
            try:
                page.wait_for_load_state("domcontentloaded", timeout=10000)
            except Exception:
                pass
            wait_primefaces_ajax(page, timeout_ms=6000)
            if vista_bandeja_visible(page, timeout_ms=8000):
                logger.info("Vista DSSP > CARNÉ > BANDEJA DE IMPRESIÓN confirmada")
                return
        page.wait_for_timeout(500 * intento)

    raise RuntimeError("No se pudo abrir DSSP > CARNÉ > BANDEJA DE IMPRESIÓN")
