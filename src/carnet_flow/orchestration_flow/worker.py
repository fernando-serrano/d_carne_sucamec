"""Worker: un navegador y un login por proceso; toma DNIs de una cola compartida.

Recuperación en dos niveles (patrón de ESTADOS-GADSO/recovery.py):
1. Sesión cortada ("Error del servidor" o rebote a login) con la página viva:
   re-login sobre la misma página y se reintenta el DNI.
2. Navegador muerto o re-login fallido: se reabre el navegador y se reintenta el DNI.
Agotados los intentos, el DNI queda como ERROR y se reintenta en la próxima corrida.
"""

from __future__ import annotations

import logging
import queue as queue_mod
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

from ..config import credentials_for, load_settings
from ..domain import Estado, ResultadoConsulta
from ..impresion_flow.navigation import navigate_to_bandeja_impresion
from ..impresion_flow.search import consultar_dni
from ..login_flow.auth import login
from ..login_flow.browser import close_browser, open_browser
from . import manifest

MARCAS_ERROR_SERVIDOR = ("ERROR DEL SERVIDOR", "ERROR INTERNO EN EL SISTEMA", "VUELVA A INGRESAR AL SISTEMA")
MAX_SESIONES_FALLIDAS = 3


def _logger(run_dir: Path, nombre: str) -> logging.Logger:
    logger = logging.getLogger(nombre)
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s | %(message)s")
        archivo = logging.FileHandler(run_dir / "logs" / f"{nombre}.log", encoding="utf-8")
        archivo.setFormatter(fmt)
        consola = logging.StreamHandler()
        consola.setFormatter(fmt)
        logger.addHandler(archivo)
        logger.addHandler(consola)
    return logger


def sesion_cortada(page: Page) -> bool:
    try:
        if "login.xhtml" in (page.url or "").lower():
            return True
        cuerpo = (page.locator("body").inner_text(timeout=500) or "").upper()
    except Exception:
        return False
    return any(m in cuerpo for m in MARCAS_ERROR_SERVIDOR)


def worker_main(negocio: str, indice: int, cola, run_dir_txt: str) -> int:
    run_dir = Path(run_dir_txt)
    nombre = f"worker_{negocio}_{indice:02d}"
    log = _logger(run_dir, nombre)
    settings = load_settings()
    credenciales = credentials_for(negocio)
    ruta_manifest = run_dir / "manifest" / f"{nombre}.jsonl"
    staging = run_dir / "staging"
    procesados = 0
    pendiente: str | None = None
    intentos = 0
    sesiones_fallidas = 0

    with sync_playwright() as pw:
        while True:
            browser = context = None
            try:
                browser, context, page = open_browser(pw, settings)
                login(page, settings, credenciales, negocio, log)
                navigate_to_bandeja_impresion(page, log)
                sesiones_fallidas = 0

                while True:
                    if pendiente is None:
                        try:
                            pendiente = cola.get_nowait()
                            intentos = 0
                        except queue_mod.Empty:
                            log.info("Cola vacía; %s DNIs procesados", procesados)
                            return procesados
                    intentos += 1
                    try:
                        if sesion_cortada(page):
                            raise RuntimeError("sesión cortada antes de consultar")
                        resultado = consultar_dni(page, negocio, pendiente, staging, settings.descarga_timeout_ms, log)
                    except Exception as exc:
                        log.warning("[%s] intento %s/%s falló: %s", pendiente, intentos, settings.intentos_por_dni, exc)
                        if intentos >= settings.intentos_por_dni:
                            manifest.escribir(ruta_manifest, ResultadoConsulta(
                                negocio, pendiente, Estado.ERROR, detalle=str(exc)[:300]), nombre)
                            pendiente = None
                        if sesion_cortada(page):
                            page.wait_for_timeout(settings.server_error_wait_ms)
                            login(page, settings, credenciales, negocio, log)
                            navigate_to_bandeja_impresion(page, log)
                        else:
                            navigate_to_bandeja_impresion(page, log)  # restablece la vista
                        continue
                    manifest.escribir(ruta_manifest, resultado, nombre)
                    procesados += 1
                    log.info("[%s] %s %s", pendiente, resultado.estado.value,
                             resultado.fecha_emision.isoformat() if resultado.fecha_emision else "")
                    pendiente = None
            except Exception as exc:
                sesiones_fallidas += 1
                log.error("Sesión perdida (%s/%s): %s", sesiones_fallidas, MAX_SESIONES_FALLIDAS, exc)
                if sesiones_fallidas >= MAX_SESIONES_FALLIDAS:
                    if pendiente is not None:
                        cola.put(pendiente)  # que lo tome otro worker
                    log.error("Worker detenido tras %s sesiones fallidas", sesiones_fallidas)
                    return procesados
            finally:
                close_browser(browser, context, log)
