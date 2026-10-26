from __future__ import annotations

import logging
import time
from pathlib import Path

from playwright.sync_api import Download, Page, Response

from ..domain import Estado, ResultadoConsulta, nombre_pdf, parse_emision, sin_tildes
from .navigation import wait_primefaces_ajax
from .selectors import TEXTO_BUSCAR_POR, VISTA


def _asegurar_buscar_por(page: Page, logger: logging.Logger) -> None:
    """Pasos 4–6: el desplegable debe quedar en 'DNI/CE PERSONAL DE SEGURIDAD'."""
    label = page.locator(VISTA["buscar_por_label"]).first
    for _ in range(3):
        actual = sin_tildes(label.inner_text(timeout=5000) or "").strip().upper()
        if actual == TEXTO_BUSCAR_POR:
            return
        page.locator(VISTA["buscar_por_widget"]).first.click(timeout=6000)
        opcion = page.locator(VISTA["buscar_por_opcion"]).first
        opcion.wait_for(state="visible", timeout=6000)
        opcion.click(timeout=6000)
        wait_primefaces_ajax(page, timeout_ms=5000)
        page.wait_for_timeout(250)
    raise RuntimeError("No se pudo seleccionar 'DNI/CE PERSONAL DE SEGURIDAD' en Buscar por")


def _escribir_filtro(page: Page, dni: str) -> None:
    campo = page.locator(VISTA["filtro"]).first
    campo.wait_for(state="visible", timeout=8000)
    campo.click()
    campo.fill(dni)
    if (campo.input_value() or "") != dni:
        campo.press("Control+A")
        campo.press("Backspace")
        campo.type(dni, delay=15)


def _leer_filas(page: Page) -> list[str]:
    try:
        return page.eval_on_selector_all(
            VISTA["filas"],
            "rows => rows.filter(r => !r.classList.contains('ui-datatable-empty-message'))"
            ".map(r => (r.innerText || '').replace(/\\s+/g, ' ').trim())",
        )
    except Exception:
        return []


def _esperar_resultados(page: Page, dni: str, timeout_ms: int = 12000) -> list[str]:
    """Filas de la búsqueda de ESTE dni (evita leer la tabla del registro anterior)."""
    deadline = time.time() + timeout_ms / 1000.0
    while time.time() < deadline:
        filas = _leer_filas(page)
        if filas and any(dni in f for f in filas):
            return [f for f in filas if dni in f] or filas
        try:
            if page.locator(VISTA["fila_vacia"]).first.is_visible(timeout=150):
                return []
        except Exception:
            pass
        page.wait_for_timeout(200)
    # Sin coincidencia explícita del dni en la fila: se devuelve lo visible.
    return _leer_filas(page)


def _descargar_pdf(page: Page, indice_fila: int, destino: Path, timeout_ms: int, logger: logging.Logger) -> None:
    """Paso 9: btnVerPdf hace submit a target=_blank.

    - headless: Chromium no tiene visor y la respuesta llega como evento download.
    - visible: se abre el visor; se toma el cuerpo de la respuesta application/pdf
      (evita el paso 9.1, el botón 'Descargar' del visor dentro de un shadow DOM).
    """
    context = page.context
    capturado: dict[str, object] = {}

    def on_download(download: Download) -> None:
        capturado.setdefault("download", download)

    def on_response(response: Response) -> None:
        if "download" in capturado or "body" in capturado:
            return
        if "pdf" in (response.headers.get("content-type") or "").lower():
            try:
                capturado["body"] = response.body()
            except Exception:
                pass  # descarga: el cuerpo llega por on_download

    def on_page(nueva: Page) -> None:
        capturado.setdefault("popup", nueva)
        nueva.on("download", on_download)
        nueva.on("response", on_response)

    page.on("download", on_download)
    page.on("response", on_response)
    context.on("page", on_page)
    try:
        boton = page.locator(VISTA["filas"]).nth(indice_fila).locator(VISTA["boton_pdf"]).first
        boton.click(timeout=8000)
        deadline = time.time() + timeout_ms / 1000.0
        while time.time() < deadline and "download" not in capturado and "body" not in capturado:
            page.wait_for_timeout(150)

        destino.parent.mkdir(parents=True, exist_ok=True)
        if "download" in capturado:
            capturado["download"].save_as(str(destino))  # type: ignore[union-attr]
        elif "body" in capturado:
            destino.write_bytes(capturado["body"])  # type: ignore[arg-type]
        else:
            raise RuntimeError(f"No llegó el PDF en {timeout_ms} ms")
    finally:
        page.remove_listener("download", on_download)
        page.remove_listener("response", on_response)
        context.remove_listener("page", on_page)
        popup = capturado.get("popup")
        if popup is not None:
            try:
                popup.close()  # type: ignore[union-attr]
            except Exception:
                pass

    with destino.open("rb") as fh:
        if fh.read(5) != b"%PDF-":
            destino.unlink(missing_ok=True)
            raise RuntimeError("El archivo descargado no es un PDF")
    logger.info("PDF descargado: %s (%s B)", destino.name, destino.stat().st_size)


def consultar_dni(
    page: Page,
    negocio: str,
    dni: str,
    staging: Path,
    descarga_timeout_ms: int,
    logger: logging.Logger,
) -> ResultadoConsulta:
    """Pasos 4–11 del flujo para un DNI. Toma la fila con la emisión más reciente."""
    _asegurar_buscar_por(page, logger)
    _escribir_filtro(page, dni)
    # Se espera la respuesta AJAX de ESTA búsqueda: leer la tabla antes de que llegue
    # daría un falso NO_ENCONTRADO (y un vencido se movería sin haberse consultado).
    with page.expect_response(
        lambda r: r.request.method == "POST" and "impresionForm" in (r.request.post_data or ""),
        timeout=20000,
    ):
        page.locator(VISTA["boton_buscar"]).first.click(timeout=8000)
    wait_primefaces_ajax(page, timeout_ms=10000)
    page.wait_for_timeout(150)  # render del datatable tras la respuesta parcial

    filas = _esperar_resultados(page, dni)
    if not filas:
        logger.info("[%s] %s sin resultados en la bandeja", negocio, dni)
        return ResultadoConsulta(negocio, dni, Estado.NO_ENCONTRADO)

    todas = _leer_filas(page)
    candidatas = [(todas.index(f), parse_emision(f)) for f in filas if f in todas]
    con_fecha = [(i, f) for i, f in candidatas if f]
    if not con_fecha:
        return ResultadoConsulta(negocio, dni, Estado.ERROR, detalle="fila sin fecha de EMISIÓN", filas=len(filas))

    indice, fecha = max(con_fecha, key=lambda x: x[1])
    destino = staging / negocio / nombre_pdf(dni, fecha)
    if not (destino.exists() and destino.stat().st_size > 0):
        _descargar_pdf(page, indice, destino, descarga_timeout_ms, logger)
    return ResultadoConsulta(negocio, dni, Estado.ENCONTRADO, fecha, str(destino), filas=len(filas))
