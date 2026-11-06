from __future__ import annotations

import logging
import time
import warnings
from io import BytesIO

from PIL import Image, ImageEnhance, ImageFilter, ImageOps
from playwright.sync_api import Page

from ..config import Credentials, Settings
from .selectors import ERROR_SELECTORS, LOGIN_SELECTORS, SUCCESS_SELECTORS

warnings.filterwarnings(
    "ignore",
    message="'pin_memory' argument is set as true but no accelerator is found.*",
    category=UserWarning,
)

OCR_READER = None
NUMPY_MODULE = None


def get_ocr_reader():
    global OCR_READER, NUMPY_MODULE
    if OCR_READER is not None and NUMPY_MODULE is not None:
        return OCR_READER, NUMPY_MODULE
    try:
        import easyocr
        import numpy as np
    except Exception:
        return None, None

    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message="'pin_memory' argument is set as true but no accelerator is found.*",
            category=UserWarning,
        )
        OCR_READER = easyocr.Reader(["en"], gpu=False, verbose=False)
    NUMPY_MODULE = np
    return OCR_READER, NUMPY_MODULE


def write_input(page: Page, selector: str, value: str) -> None:
    field = page.locator(selector).first
    field.wait_for(state="visible", timeout=12000)
    field.click()
    field.fill(value)
    field.evaluate(
        'el => { el.dispatchEvent(new Event("input", {bubbles:true})); '
        'el.dispatchEvent(new Event("change", {bubbles:true})); }'
    )
    field.blur()
    if (field.input_value() or "") != value:
        field.click()
        field.press("Control+A")
        field.press("Backspace")
        field.type(value, delay=12)
        field.evaluate(
            'el => { el.dispatchEvent(new Event("input", {bubbles:true})); '
            'el.dispatchEvent(new Event("change", {bubbles:true})); }'
        )
        field.blur()


def _compact_text(value: str | None) -> str:
    return str(value or "").strip()


def _read_input_value(page: Page, selector: str) -> str:
    try:
        return _compact_text(page.locator(selector).first.input_value(timeout=800))
    except Exception:
        return ""


def ensure_login_form_values(
    page: Page,
    credentials: Credentials,
    captcha_text: str,
    grupo: str,
    logger: logging.Logger,
) -> None:
    expected_values = [
        ("numero_documento", credentials.numero_documento, "Numero de Documento"),
        ("usuario", credentials.usuario, "Usuario"),
        ("clave", credentials.contrasena, "Clave"),
        ("captcha_input", captcha_text, "Captcha"),
    ]

    restored_fields: list[str] = []
    for selector_key, expected_value, label in expected_values:
        selector = LOGIN_SELECTORS[selector_key]
        current_value = _read_input_value(page, selector)
        if current_value == _compact_text(expected_value):
            continue
        write_input(page, selector, expected_value)
        restored_fields.append(label)

    if restored_fields:
        logger.warning(
            "[%s] Campos reescritos antes de Ingresar por limpieza/intermitencia UI: %s",
            grupo,
            ", ".join(restored_fields),
        )


def fill_credentials(page: Page, credentials: Credentials, grupo: str, logger: logging.Logger) -> None:
    page.locator(LOGIN_SELECTORS["numero_documento"]).wait_for(state="visible", timeout=9000)
    page.select_option(LOGIN_SELECTORS["tipo_doc_select"], value=credentials.tipo_documento_valor)
    page.wait_for_timeout(200)

    write_input(page, LOGIN_SELECTORS["numero_documento"], credentials.numero_documento)
    write_input(page, LOGIN_SELECTORS["usuario"], credentials.usuario)
    write_input(page, LOGIN_SELECTORS["clave"], credentials.contrasena)
    logger.info("[%s] Credenciales cargadas", grupo)


def activate_traditional_tab(page: Page) -> None:
    tab = page.locator(LOGIN_SELECTORS["tab_tradicional"]).first
    tab.wait_for(state="visible", timeout=6000)
    tab.click(timeout=6000)


def page_shows_service_unavailable(page: Page) -> bool:
    try:
        title = (page.title() or "").lower()
        if "service unavailable" in title:
            return True
    except Exception:
        pass

    try:
        body = (page.locator("body").inner_text(timeout=350) or "").lower()
        return "service unavailable" in body and "sucamec" in body
    except Exception:
        return False


def wait_until_service_available(page: Page, login_url: str, wait_seconds: int = 8) -> None:
    while page_shows_service_unavailable(page):
        page.wait_for_timeout(max(1, wait_seconds) * 1000)
        page.goto(login_url, wait_until="domcontentloaded", timeout=45000)


def page_shows_server_error_panel(page: Page) -> bool:
    """Detecta la pantalla 'Error del servidor' (panel-header) tambien en el login."""
    try:
        body = (page.locator("body").inner_text(timeout=400) or "").upper()
    except Exception:
        return False
    return "ERROR DEL SERVIDOR" in body or "ERROR INTERNO EN EL SISTEMA" in body


def wait_until_login_form_ready(
    page: Page,
    settings: Settings,
    grupo: str,
    logger: logging.Logger,
) -> None:
    """Si SUCAMEC sirve la pantalla de error en lugar del login, re-ingresa al href.

    Replica lo que haria una persona: ante 'Error del servidor', volver a entrar al
    login y reintentar, en vez de fallar de inmediato.
    """
    max_reloads = max(1, settings.server_error_retries)
    wait_ms = max(0, settings.server_error_wait_ms)
    for attempt in range(1, max_reloads + 1):
        if not page_shows_server_error_panel(page):
            return
        logger.warning(
            "[%s] Pantalla de error del servidor en el login; reingresando (%s/%s)",
            grupo,
            attempt,
            max_reloads,
        )
        if wait_ms:
            page.wait_for_timeout(wait_ms)
        page.goto(settings.login_url, wait_until="domcontentloaded", timeout=45000)
        wait_until_service_available(page, settings.login_url)


def clean_captcha_text(raw_text: str) -> str:
    text = str(raw_text or "").strip().upper().replace(" ", "").replace("\n", "").replace("\r", "")
    return "".join(char for char in text if char.isalnum())


def is_valid_captcha(text: str) -> bool:
    return bool(text) and len(text) == 5 and text.isalnum()


def preprocess_captcha(image_bytes: bytes, variant: int = 0) -> Image.Image:
    image = Image.open(BytesIO(image_bytes)).convert("L")
    if variant == 0:
        image = ImageEnhance.Contrast(image).enhance(3.5)
        width, height = image.size
        image = image.resize((width * 3, height * 3), Image.LANCZOS)
        image = image.filter(ImageFilter.MedianFilter(size=3))
        image = ImageOps.invert(image)
        return image.point(lambda p: 255 if p > 130 else 0)
    if variant == 1:
        image = ImageEnhance.Contrast(image).enhance(2.8)
        width, height = image.size
        image = image.resize((width * 2, height * 2), Image.LANCZOS)
        image = image.filter(ImageFilter.MedianFilter(size=5))
        return image.point(lambda p: 255 if p > 160 else 0)

    image = ImageEnhance.Contrast(image).enhance(4.0)
    width, height = image.size
    image = image.resize((width * 3, height * 3), Image.LANCZOS)
    image = image.filter(ImageFilter.GaussianBlur(radius=0.5))
    image = ImageOps.invert(image)
    return image.point(lambda p: 255 if p > 110 else 0)


def read_captcha_from_image(image: Image.Image) -> str:
    reader, np_module = get_ocr_reader()
    if reader is None or np_module is None:
        return ""
    try:
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message="'pin_memory' argument is set as true but no accelerator is found.*",
                category=UserWarning,
            )
            result = reader.readtext(
                np_module.array(image),
                detail=0,
                paragraph=False,
                allowlist="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
            )
    except Exception:
        return ""
    return clean_captcha_text(" ".join(str(item or "") for item in result))


def solve_captcha(page: Page, logger: logging.Logger, max_attempts: int, total_timeout_ms: int) -> str:
    reader, _ = get_ocr_reader()
    if reader is None:
        raise RuntimeError("OCR no disponible. Instala easyocr, pillow y numpy.")

    started_at = time.time()
    for attempt in range(1, max_attempts + 1):
        elapsed_ms = (time.time() - started_at) * 1000
        if elapsed_ms >= total_timeout_ms:
            raise RuntimeError(
                f"No se pudo resolver captcha dentro del timeout total ({int(total_timeout_ms)} ms)"
            )

        image_locator = page.locator(LOGIN_SELECTORS["captcha_img"])
        image_locator.wait_for(state="visible", timeout=12000)
        image_bytes = image_locator.screenshot()

        for variant in (0, 1, 2):
            candidate = read_captcha_from_image(preprocess_captcha(image_bytes, variant))
            if is_valid_captcha(candidate):
                logger.info("Captcha OCR resuelto en intento %s", attempt)
                return candidate

        logger.warning("OCR no encontro captcha valido en intento %s/%s", attempt, max_attempts)
        try:
            page.locator(LOGIN_SELECTORS["boton_refresh"]).click(timeout=4000)
            page.wait_for_timeout(150)
        except Exception:
            pass

    raise RuntimeError(f"No se pudo resolver captcha tras {max_attempts} intentos")


def refresh_captcha(page: Page, logger: logging.Logger) -> None:
    try:
        captcha_img = page.locator(LOGIN_SELECTORS["captcha_img"]).first
        previous_src = captcha_img.get_attribute("src", timeout=1500) or ""
    except Exception:
        previous_src = ""

    button = page.locator(LOGIN_SELECTORS["boton_refresh"]).first
    button.wait_for(state="visible", timeout=8000)
    button.click(timeout=8000)

    try:
        page.wait_for_function(
            """([selector, previousSrc]) => {
                const img = document.querySelector(selector);
                if (!img) return false;
                const src = img.getAttribute('src') || '';
                return src && src !== previousSrc && img.complete;
            }""",
            arg=[LOGIN_SELECTORS["captcha_img"], previous_src],
            timeout=3500,
        )
    except Exception:
        page.wait_for_timeout(500)

    logger.info("Captcha refrescado")


def is_captcha_error(error_message: str | None) -> bool:
    text = clean_captcha_text(error_message or "")
    return "CAPTCHA" in text and ("INCORRECTO" in text or "INVALIDO" in text)


def validate_login_result(page: Page, timeout_ms: int) -> tuple[bool, str | None, float]:
    started_at = time.time()
    while (time.time() - started_at) * 1000 < timeout_ms:
        try:
            if "/faces/aplicacion/inicio.xhtml" in (page.url or "").lower():
                return True, None, time.time() - started_at
        except Exception:
            pass

        for selector in SUCCESS_SELECTORS:
            try:
                if page.locator(selector).first.is_visible(timeout=120):
                    return True, None, time.time() - started_at
            except Exception:
                pass

        for selector in ERROR_SELECTORS:
            try:
                locator = page.locator(selector)
                if locator.count() > 0:
                    text = (locator.first.inner_text() or "").strip()
                    if text:
                        return False, text, time.time() - started_at
            except Exception:
                pass

        page.wait_for_timeout(140)

    return False, "No se confirmo sesion autenticada en el tiempo esperado", time.time() - started_at


def login(page: Page, settings: Settings, credentials: Credentials, grupo: str, logger: logging.Logger) -> None:
    started_at = time.time()
    credentials.validate(grupo)
    logger.info("[%s] Navegando a login", grupo)
    page.goto(settings.login_url, wait_until="domcontentloaded", timeout=45000)
    wait_until_service_available(page, settings.login_url)
    wait_until_login_form_ready(page, settings, grupo, logger)

    activate_traditional_tab(page)

    validation_elapsed = 0.0
    for login_attempt in range(1, settings.login_captcha_retries + 1):
        if login_attempt > 1:
            logger.info(
                "[%s] Reintentando login por captcha incorrecto (%s/%s)",
                grupo,
                login_attempt,
                settings.login_captcha_retries,
            )
            try:
                activate_traditional_tab(page)
            except Exception:
                pass
            refresh_captcha(page, logger)

        fill_credentials(page, credentials, grupo, logger)
        captcha_text = solve_captcha(
            page,
            logger,
            settings.ocr_max_intentos,
            settings.captcha_solve_timeout_ms,
        )
        write_input(page, LOGIN_SELECTORS["captcha_input"], captcha_text)
        logger.info("[%s] Captcha escrito automaticamente", grupo)
        ensure_login_form_values(page, credentials, captcha_text, grupo, logger)

        page.locator(LOGIN_SELECTORS["ingresar"]).click(timeout=10000)
        ok, error_message, validation_elapsed = validate_login_result(
            page,
            settings.login_validation_timeout_ms,
        )
        if ok:
            break

        if is_captcha_error(error_message) and login_attempt < settings.login_captcha_retries:
            logger.warning("[%s] Captcha rechazado por SUCAMEC: %s", grupo, error_message)
            continue

        raise RuntimeError(f"[{grupo}] Login fallido: {error_message}")

    logger.info(
        "[%s] Login exitoso en %.2fs (validacion UI: %.2fs)",
        grupo,
        time.time() - started_at,
        validation_elapsed,
    )
    cerrar_encuesta_overlay(page, logger, grupo)


def cerrar_encuesta_overlay(page, logger=None, grupo: str = "", wait_ms: int = 4000) -> bool:
    """Cierra la encuesta de satisfaccion que SUCAMEC superpone en inicio.xhtml.

    Tolerante: si no aparece no hace nada; nunca lanza excepcion.
    """
    def _log(nivel, msg, *args):
        if logger is not None:
            getattr(logger, nivel)(msg, *args)
        else:
            print(f"[{nivel.upper()}] " + (msg % args))

    cerrar = page.locator(".encuesta-overlay-cerrar").first
    try:
        cerrar.wait_for(state="visible", timeout=wait_ms)
    except Exception:
        _log("info", "[%s] Encuesta overlay no presente, se continua", grupo)
        return False
    try:
        cerrar.click(timeout=3000)
        page.locator(".encuesta-modal").first.wait_for(state="hidden", timeout=3000)
        _log("info", "[%s] Encuesta overlay cerrada con boton X", grupo)
        return True
    except Exception as exc:
        _log("warning", "[%s] Clic en cerrar encuesta fallo (%s); forzando cierre por JS", grupo, exc)
    try:
        page.evaluate(
            """() => {
                try { if (typeof cerrarEncuestaOverlay === 'function') cerrarEncuestaOverlay(); } catch (e) {}
                document.querySelectorAll('.encuesta-modal').forEach(el => el.remove());
            }"""
        )
        _log("info", "[%s] Encuesta overlay removida por JS", grupo)
        return True
    except Exception as exc:
        _log("warning", "[%s] No se pudo remover encuesta overlay: %s", grupo, exc)
        return False
