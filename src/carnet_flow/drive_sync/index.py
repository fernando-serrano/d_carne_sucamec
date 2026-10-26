"""Índice de carnés en Drive: DNI -> PDF(s) con fecha de emisión tomada de la ruta.

Estructura real (verificada 2026-10-21):
  <raíz>/<J&V RESGUARDO SAC | J&V RESGUARDO SELVA SAC>/<año>/<mes>?/<día>/<DNI>.pdf
con nombres de mes y día irregulares ("10.0CTUBRE", "05..10..2023", "SELVA.02.10.2023").
La carpeta de vencidos (<raíz>/VENCIDOS/...) no forma parte del índice activo.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..domain import (
    CarneDrive,
    IndiceDrive,
    fecha_desde_ruta,
    negocio_desde_texto,
    normalizar_dni,
    parse_fecha_carpeta,
    sin_tildes,
)
from .client import FOLDER, PDF, DriveClient, Item

# "80000011.pdf", "80000007..pdf", "80000010_.pdf", "80000001 - VIGILANCIA PRIVADA.pdf"
_NOMBRE_PDF_RE = re.compile(r"^\s*(\d{7,9})(?!\d)[^\\/]*\.pdf\s*$", re.I)


@dataclass
class Arbol:
    root_id: str
    carpetas: dict[str, Item] = field(default_factory=dict)
    hijos: dict[str, list[Item]] = field(default_factory=dict)

    def ruta(self, item: Item) -> list[str] | None:
        """Nombres de carpetas desde la raíz (exclusive) hasta el padre del item."""
        partes: list[str] = []
        actual = item.parents[0] if item.parents else None
        saltos = 0
        while actual and actual != self.root_id:
            carpeta = self.carpetas.get(actual)
            if carpeta is None or saltos > 12:
                return None  # fuera del árbol de la raíz
            partes.append(carpeta.name)
            actual = carpeta.parents[0] if carpeta.parents else None
            saltos += 1
        return list(reversed(partes)) if actual == self.root_id else None

    def subcarpetas(self, parent_id: str) -> list[Item]:
        return [i for i in self.hijos.get(parent_id, []) if i.mime == FOLDER]

    def registrar_carpeta(self, item: Item) -> None:
        self.carpetas[item.id] = item
        for p in item.parents:
            self.hijos.setdefault(p, []).append(item)


def cargar_arbol(client: DriveClient, root_id: str, cache: Path | None = None) -> tuple[Arbol, list[Item]]:
    """Dos consultas masivas (carpetas y PDFs) en lugar de recorrer carpeta por carpeta.

    Con `cache`, el listado se guarda en la corrida y se reutiliza al reanudarla.
    """
    if cache is not None and cache.exists():
        datos = json.loads(cache.read_text(encoding="utf-8"))
        carpetas = [Item(*c[:3], tuple(c[3])) for c in datos["carpetas"]]
        pdfs = [Item(*p[:3], tuple(p[3])) for p in datos["pdfs"]]
    else:
        carpetas = client.listar(f"mimeType = '{FOLDER}' and trashed = false")
        pdfs = client.listar(f"mimeType = '{PDF}' and trashed = false")
        if cache is not None:
            cache.write_text(json.dumps({
                "carpetas": [[i.id, i.name, i.mime, list(i.parents)] for i in carpetas],
                "pdfs": [[i.id, i.name, i.mime, list(i.parents)] for i in pdfs],
            }), encoding="utf-8")
    arbol = Arbol(root_id)
    for carpeta in carpetas:
        arbol.registrar_carpeta(carpeta)
    return arbol, pdfs


def negocio_de_carpeta(nombre: str) -> str | None:
    return negocio_desde_texto(nombre)


def construir_indice(arbol: Arbol, pdfs: list[Item], carpeta_vencidos: str) -> IndiceDrive:
    indice = IndiceDrive()
    vencidos = sin_tildes(carpeta_vencidos).upper()
    for pdf in pdfs:
        partes = arbol.ruta(pdf)
        if partes is None:
            continue
        ruta_txt = "/".join(partes)
        if not partes or sin_tildes(partes[0]).upper() == vencidos:
            continue
        negocio = negocio_de_carpeta(partes[0])
        m = _NOMBRE_PDF_RE.match(pdf.name)
        dni = normalizar_dni(m.group(1)) if m else None
        if not negocio or not dni:
            indice.ignorados.append((ruta_txt, pdf.name))
            continue
        # <DNI>_<dd.mm.aaaa>.pdf: la fecha del nombre manda sobre la de la carpeta.
        fecha = parse_fecha_carpeta(pdf.name[m.end(1):])
        aproximada = False
        if fecha is None:
            fecha, aproximada = fecha_desde_ruta(partes[1:])
        indice.agregar(CarneDrive(negocio, dni, pdf.id, ruta_txt, fecha, aproximada))
    return indice
