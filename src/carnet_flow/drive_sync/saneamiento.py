"""Saneamiento de Drive guiado por el CONTENIDO de cada PDF.

Cada carné trae impresos el DNI y "FECHA DE EMISIÓN: dd/mm/aaaa". Con eso:
- el PDF va a la carpeta de día de su emisión real (se crea si falta) y se nombra <DNI>_<dd.mm.aaaa>.pdf
- copias idénticas (mismo md5) en el mismo destino -> una se conserva, el resto a la papelera
- DNI impreso distinto al del nombre, o PDF sin texto (escaneado) -> REVISAR, no se toca
Alcance: PDFs en carpetas duplicadas de un mismo día, sueltos en carpetas de mes, en carpetas
sin fecha válida, o con nombre repetido en su carpeta.
"""

from __future__ import annotations

import io
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from datetime import date

from ..domain import nombre_pdf, normalizar_dni, parse_emision

_NUM_RE = re.compile(r"(?<!\d)(\d{8,9})(?!\d)")


def extraer_huella(contenido: bytes) -> str | None:
    """Huella del TEXTO completo del carné (nombre, N°, modalidad, fechas, expediente).

    SUCAMEC regenera el PDF en cada descarga (bytes/md5 distintos) pero el texto es el mismo;
    un carné de otra modalidad el mismo día cambia el texto. Así se distingue copia de credencial."""
    import hashlib

    from pypdf import PdfReader

    try:
        texto = " ".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(contenido)).pages)
    except Exception:
        return None
    texto = re.sub(r"\s+", " ", texto).strip()
    return hashlib.sha1(texto.encode("utf-8")).hexdigest() if texto else None


def extraer_datos_pdf(contenido: bytes) -> tuple[str | None, date | None]:
    """(dni, fecha_emision) desde el texto del carné. El DNI es el número de 8–9 dígitos
    más repetido (aparece en el código lateral, la cabecera y el reverso)."""
    from pypdf import PdfReader

    try:
        lector = PdfReader(io.BytesIO(contenido))
        texto = " ".join((p.extract_text() or "") for p in lector.pages)
    except Exception:
        return None, None
    texto = re.sub(r"\s+", " ", texto)
    nums = Counter(_NUM_RE.findall(texto))
    dni = None
    if nums:
        candidato, veces = nums.most_common(1)[0]
        dni = normalizar_dni(candidato) if veces >= 2 else None
    return dni, parse_emision(texto)


@dataclass(frozen=True)
class Documento:
    file_id: str
    nombre: str
    carpeta_id: str
    ruta: str
    negocio: str
    vencidos: bool
    dni_nombre: str | None
    md5: str
    dni_pdf: str | None = None
    emision_pdf: date | None = None
    huella: str | None = None


@dataclass(frozen=True)
class Accion:
    doc: Documento
    tipo: str          # MOVER | RENOMBRAR | OK | PAPELERA_DUPLICADO | REVISAR
    destino_id: str = ""
    destino_ruta: str = ""
    nombre_nuevo: str = ""
    motivo: str = ""


def decidir(docs: list[Documento], resolver) -> list[Accion]:
    acciones: list[Accion] = []
    for d in docs:
        if d.emision_pdf is None or d.dni_pdf is None:
            acciones.append(Accion(d, "REVISAR", motivo="PDF sin texto legible (¿escaneado?)"))
            continue
        if d.dni_nombre and d.dni_nombre != d.dni_pdf:
            acciones.append(Accion(d, "REVISAR", motivo=f"DNI del nombre {d.dni_nombre} ≠ DNI impreso {d.dni_pdf}"))
            continue
        destino = resolver.carpeta_para(d.negocio, d.emision_pdf, d.vencidos)
        nuevo = nombre_pdf(d.dni_pdf, d.emision_pdf)
        if destino != d.carpeta_id:
            tipo = "MOVER"
        elif d.nombre != nuevo:
            tipo = "RENOMBRAR"
        else:
            tipo = "OK"
        acciones.append(Accion(d, tipo, destino, resolver.ruta_legible(destino), nuevo))

    # Copias en el mismo destino con el mismo nombre final
    grupos: dict[tuple[str, str], list[int]] = defaultdict(list)
    for i, a in enumerate(acciones):
        if a.tipo in ("MOVER", "RENOMBRAR", "OK"):
            grupos[(a.destino_id, a.nombre_nuevo)].append(i)
    for indices in grupos.values():
        if len(indices) < 2:
            continue
        # se conserva el que ya está en destino con el nombre correcto, si no el primero
        indices.sort(key=lambda i: (acciones[i].tipo != "OK", acciones[i].tipo != "RENOMBRAR"))
        conservado = acciones[indices[0]]
        for i in indices[1:]:
            a = acciones[i]
            if a.doc.md5 and a.doc.md5 == conservado.doc.md5:
                acciones[i] = replace(a, tipo="PAPELERA_DUPLICADO", motivo=f"copia idéntica de {conservado.doc.ruta}/{conservado.doc.nombre}")
            elif a.doc.huella and a.doc.huella == conservado.doc.huella:
                # SUCAMEC regenera el PDF en cada descarga (bytes distintos) pero el texto es idéntico
                acciones[i] = replace(a, tipo="PAPELERA_DUPLICADO",
                                      motivo=f"mismo carné (texto idéntico, PDF regenerado) que {conservado.doc.ruta}/{conservado.doc.nombre}")
            else:
                acciones[i] = replace(a, tipo="REVISAR", motivo="mismo DNI y fecha que otro PDF pero contenido distinto")
    return acciones
