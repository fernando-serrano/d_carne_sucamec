"""Bitácora de la migración del Drive: estado por paso y restauración hasta un punto.

Cada paso dejó un registro con un cambio por línea (renombrado/*.jsonl, carpetas_vacias.json).
Restaurar "hasta N" deshace, en orden inverso, los pasos posteriores a N y deja el Drive
como estaba al terminar el paso N (hasta 0 = estado original).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Paso:
    numero: int
    clave: str
    descripcion: str
    registro: str  # relativo a renombrado/


# Orden cronológico real de la migración (2026-10-21 a 2026-10-23).
PASOS = [
    Paso(1, "renombrado_prueba", "Prueba de renombrado de PDFs a DNI_dd.mm.aaaa", "renombrados.jsonl"),
    Paso(2, "carpetas", "Normalización de nombres de carpetas", "carpetas_renombradas.jsonl"),
    Paso(3, "saneamiento", "Saneamiento: mover a su fecha real, renombrar, duplicados a papelera", "saneamiento.jsonl"),
    Paso(4, "correcciones", "Corrección de 7 DNI mal escritos", "correcciones.jsonl"),
    Paso(5, "carpetas_vacias", "Carpetas vacías a la papelera", "carpetas_vacias.json"),
    Paso(6, "vencidos", "Segmentación de carnés vencidos a VENCIDOS", "vencidos_movidos.jsonl"),
]


@dataclass
class Inversa:
    """Operación que deshace un cambio. `restaurar` = sacar de la papelera (requiere al dueño)."""
    file_id: str
    nombre: str | None = None
    agregar: str | None = None
    quitar: str | None = None
    restaurar: bool = False
    detalle: dict = field(default_factory=dict)


def leer_registro(ruta: Path) -> list[dict]:
    if not ruta.exists():
        return []
    texto = ruta.read_text(encoding="utf-8")
    if ruta.suffix == ".json":
        return json.loads(texto)
    return [json.loads(x) for x in texto.splitlines() if x.strip()]


def _carpetas(anterior, nueva) -> tuple[str | None, str | None]:
    anterior = ",".join(anterior) if isinstance(anterior, list) else anterior
    if not anterior or not nueva or anterior == nueva:
        return None, None
    return anterior, nueva


def inversas(paso: Paso, entradas: list[dict]) -> list[Inversa]:
    """Inversas en orden inverso al aplicado (el último cambio se deshace primero)."""
    out: list[Inversa] = []
    for e in reversed(entradas):
        if paso.clave in ("renombrado_prueba", "carpetas"):
            out.append(Inversa(e["id"], nombre=e["anterior"]))
        elif paso.clave == "saneamiento":
            if e["tipo"] == "PAPELERA_DUPLICADO":
                out.append(Inversa(e["id"], restaurar=True))
            else:
                agregar, quitar = _carpetas(e["carpeta_anterior"], e["carpeta_nueva"])
                out.append(Inversa(e["id"], nombre=e["nombre_anterior"], agregar=agregar, quitar=quitar))
        elif paso.clave == "correcciones":
            if e["accion"] == "papelera":
                out.append(Inversa(e["id"], restaurar=True))
            else:
                agregar, quitar = _carpetas(e["carpeta_anterior"], e["carpeta_nueva"])
                out.append(Inversa(e["id"], nombre=e["nombre_anterior"], agregar=agregar, quitar=quitar))
        elif paso.clave == "carpetas_vacias":
            out.append(Inversa(e["id"], restaurar=True))
        elif paso.clave == "vencidos":
            out.append(Inversa(e["id"], agregar=e["origen"], quitar=e["destino"]))
    return out


def pasos_a_deshacer(hasta: int) -> list[Paso]:
    return [p for p in reversed(PASOS) if p.numero > hasta]
