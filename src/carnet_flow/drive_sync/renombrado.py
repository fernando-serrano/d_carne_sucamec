"""Renombrado masivo de los PDFs existentes a <DNI>_<dd.mm.aaaa>.pdf.

La fecha sale del nombre si ya la trae, si no de la carpeta de día. No se inventa:
- carpeta solo de mes (día desconocido) -> FECHA_APROXIMADA, no se renombra
- sin fecha legible (p. ej. DFNUEVOS)  -> SIN_FECHA, no se renombra
- dos PDFs que terminarían con el mismo nombre en la misma carpeta -> COLISION, no se renombran
Incluye la carpeta de vencidos (misma estructura bajo <raíz>/VENCIDOS/<negocio>/...).
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from ..domain import (
    fecha_desde_ruta,
    negocio_desde_texto,
    nombre_pdf,
    normalizar_dni,
    parse_fecha_carpeta,
    parse_mes_carpeta,
    sin_tildes,
)
from .client import DriveClient, Item
from .index import Arbol

_DNI_RE = re.compile(r"^\s*(\d{7,9})(?!\d)[^\\/]*\.pdf\s*$", re.I)


@dataclass(frozen=True)
class Renombre:
    file_id: str
    carpeta_id: str
    ruta: str
    actual: str
    nuevo: str
    estado: str  # RENOMBRAR | YA_OK | SIN_FECHA | FECHA_APROXIMADA | COLISION | NO_RECONOCIDO


def planificar(arbol: Arbol, pdfs: list[Item], carpeta_vencidos: str) -> list[Renombre]:
    vencidos = sin_tildes(carpeta_vencidos).upper()
    out: list[Renombre] = []
    for pdf in pdfs:
        partes = arbol.ruta(pdf)
        if not partes:
            continue
        ruta = "/".join(partes)
        bajo_negocio = partes[1:] if sin_tildes(partes[0]).upper() == vencidos else partes
        padre = pdf.parents[0] if pdf.parents else ""
        m = _DNI_RE.match(pdf.name)
        dni = normalizar_dni(m.group(1)) if m else None
        if not bajo_negocio or not negocio_desde_texto(bajo_negocio[0]) or not dni:
            out.append(Renombre(pdf.id, padre, ruta, pdf.name, "", "NO_RECONOCIDO"))
            continue
        fecha = parse_fecha_carpeta(pdf.name[m.end(1):])
        aproximada = False
        if fecha is None:
            fecha, aproximada = fecha_desde_ruta(bajo_negocio[1:])
        if fecha is None:
            out.append(Renombre(pdf.id, padre, ruta, pdf.name, "", "SIN_FECHA"))
            continue
        nuevo = nombre_pdf(dni, fecha)
        if aproximada:
            out.append(Renombre(pdf.id, padre, ruta, pdf.name, nuevo, "FECHA_APROXIMADA"))
            continue
        out.append(Renombre(pdf.id, padre, ruta, pdf.name, nuevo, "YA_OK" if pdf.name == nuevo else "RENOMBRAR"))

    # Colisiones: mismo nombre final en la misma carpeta (p. ej. dos modalidades del mismo DNI).
    destino: dict[tuple[str, str], list[int]] = defaultdict(list)
    for i, r in enumerate(out):
        if r.estado in ("RENOMBRAR", "YA_OK"):
            destino[(r.carpeta_id, r.nuevo)].append(i)
    for indices in destino.values():
        if len(indices) > 1:
            for i in indices:
                r = out[i]
                if r.estado == "RENOMBRAR":
                    out[i] = Renombre(r.file_id, r.carpeta_id, r.ruta, r.actual, r.nuevo, "COLISION")
    return out


def planificar_carpetas(arbol: Arbol, carpeta_vencidos: str) -> list[Renombre]:
    """Normaliza nombres de carpetas de mes (N.MES) y de día (dd.mm.aaaa / SELVA.dd.mm.aaaa).

    Estados: RENOMBRAR | YA_OK | COLISION (otra carpeta hermana ya tiene o tendría ese nombre) |
    SIN_FECHA (no se reconoce mes ni día: se deja igual, p. ej. '15.15.2026', 'DFNUEVOS').
    """
    from .folders import nombre_dia, nombre_mes  # evita import circular

    vencidos = sin_tildes(carpeta_vencidos).upper()
    out: list[Renombre] = []
    for carpeta in arbol.carpetas.values():
        partes = arbol.ruta(carpeta)  # ruta del PADRE de la carpeta
        if partes is None:
            continue
        rel = partes[1:] if partes and sin_tildes(partes[0]).upper() == vencidos else partes
        # rel = [negocio, año, ...]; se normaliza todo lo que esté por debajo del año
        if len(rel) < 2 or not rel[1].strip().isdigit():
            continue
        negocio = negocio_desde_texto(rel[0])
        if not negocio:
            continue
        anio = int(rel[1].strip())
        padre = carpeta.parents[0] if carpeta.parents else ""
        ruta = "/".join(partes)
        fecha = parse_fecha_carpeta(carpeta.name, anio)
        mes = parse_mes_carpeta(carpeta.name)
        if fecha:
            nuevo = nombre_dia(negocio, fecha)
        elif mes:
            nuevo = nombre_mes(mes)
        else:
            out.append(Renombre(carpeta.id, padre, ruta, carpeta.name, "", "SIN_FECHA"))
            continue
        out.append(Renombre(carpeta.id, padre, ruta, carpeta.name, nuevo,
                            "YA_OK" if carpeta.name == nuevo else "RENOMBRAR"))

    destino: dict[tuple[str, str], list[int]] = defaultdict(list)
    for i, r in enumerate(out):
        if r.estado in ("RENOMBRAR", "YA_OK"):
            destino[(r.carpeta_id, r.nuevo.upper())].append(i)
    for indices in destino.values():
        if len(indices) > 1:
            for i in indices:
                r = out[i]
                if r.estado == "RENOMBRAR":
                    out[i] = Renombre(r.file_id, r.carpeta_id, r.ruta, r.actual, r.nuevo, "COLISION")
    return out


def ejecutar(client: DriveClient, cambios: list[Renombre], registro: Path, logger: logging.Logger,
             lote: int = 50) -> tuple[int, int]:
    """Renombra en lotes (batch HTTP). `registro` (JSONL) guarda id, nombre anterior y nuevo:
    sirve de checkpoint (no repite) y para revertir."""
    hechos: set[str] = set()
    if registro.exists():
        for linea in registro.read_text(encoding="utf-8").splitlines():
            try:
                hechos.add(json.loads(linea)["id"])
            except (ValueError, KeyError):
                continue
    pendientes = [c for c in cambios if c.estado == "RENOMBRAR" and c.file_id not in hechos]
    ok = errores = 0
    with registro.open("a", encoding="utf-8") as fh:
        for inicio in range(0, len(pendientes), lote):
            grupo = pendientes[inicio:inicio + lote]
            fallidos: dict[str, str] = {}

            def callback(request_id, _resp, exc):
                if exc is not None:
                    fallidos[request_id] = str(exc)

            batch = client.svc.new_batch_http_request(callback=callback)
            for c in grupo:
                batch.add(client.svc.files().update(fileId=c.file_id, body={"name": c.nuevo}, fields="id",
                                                    supportsAllDrives=True), request_id=c.file_id)
            batch.execute()
            for c in grupo:
                if c.file_id in fallidos:
                    errores += 1
                    logger.warning("No se renombró %s/%s: %s", c.ruta, c.actual, fallidos[c.file_id][:200])
                else:
                    ok += 1
                    fh.write(json.dumps({"id": c.file_id, "anterior": c.actual, "nuevo": c.nuevo, "ruta": c.ruta},
                                        ensure_ascii=False) + "\n")
            fh.flush()
            logger.info("Renombrados %s/%s (errores %s)", ok + len(hechos), len(pendientes) + len(hechos), errores)
            if fallidos:
                time.sleep(2)  # respiro ante límites de cuota de la API
    return ok, errores


def revertir(client: DriveClient, registro: Path, logger: logging.Logger) -> int:
    n = 0
    for linea in registro.read_text(encoding="utf-8").splitlines():
        e = json.loads(linea)
        client.svc.files().update(fileId=e["id"], body={"name": e["anterior"]}, supportsAllDrives=True).execute()
        n += 1
    logger.info("Revertidos %s nombres", n)
    return n
