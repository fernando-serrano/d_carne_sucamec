"""Reconciliación MinIO <- Drive (migración inicial y reparación de divergencias).

MinIO debe contener exactamente lo que hay en Drive (activos + VENCIDOS) bajo el prefijo.
Nunca se toca nada fuera de `<prefijo>/`.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date
from typing import Callable, Literal

from ..domain import CarneDrive, IndiceDrive
from .claves import clave_minio, metadatos
from .store import AlmacenCarnets


@dataclass(frozen=True)
class OpMinio:
    accion: Literal["SUBIR", "ELIMINAR"]
    clave: str
    file_id: str = ""
    negocio: str = ""
    dni: str = ""
    fecha: date | None = None


def filtrar_por_negocio(existentes: set[str], prefijo: str, negocios: tuple[str, ...]) -> set[str]:
    """Al reconciliar solo algunos negocios, los objetos de los demás no deben verse como huérfanos."""
    raiz = prefijo.strip("/")
    ramas = tuple(f"{raiz}/{n}/" for n in negocios) + tuple(f"{raiz}/vencidos/{n}/" for n in negocios)
    return {k for k in existentes if k.startswith(ramas)}


def planificar_reconciliacion(
    activos: IndiceDrive, vencidos: IndiceDrive, existentes: set[str], prefijo: str
) -> tuple[list[OpMinio], list[CarneDrive]]:
    raiz = prefijo.strip("/") + "/"
    deseadas: dict[str, CarneDrive] = {}
    sin_fecha: list[CarneDrive] = []
    for indice, es_vencido in ((activos, False), (vencidos, True)):
        for carnes in indice.activos.values():
            for c in carnes:
                if c.fecha_emision is None:
                    sin_fecha.append(c)
                    continue
                # Duplicados (mismo DNI y fecha) caen en la misma clave: se conserva el primero.
                deseadas.setdefault(clave_minio(prefijo, c.negocio, c.dni, c.fecha_emision, es_vencido), c)
    propias = {k for k in existentes if k.startswith(raiz)}
    ops = [OpMinio("SUBIR", k, c.file_id, c.negocio, c.dni, c.fecha_emision)
           for k, c in sorted(deseadas.items()) if k not in propias]
    ops += [OpMinio("ELIMINAR", k) for k in sorted(propias - set(deseadas))]
    return ops, sin_fecha


def aplicar_reconciliacion(
    ops: list[OpMinio],
    almacen: AlmacenCarnets,
    descargar: Callable[[str], bytes],
    aplicar_cambios: bool,
    logger: logging.Logger,
    hilos: int,
) -> list[tuple[OpMinio, str]]:
    """Estado por operación: HECHO | SIMULADO | MINIO_ERROR. Un fallo no detiene el resto."""
    out: list[tuple[OpMinio, str] | None] = [None] * len(ops)

    def ejecutar(pos: int, op: OpMinio) -> None:
        if not aplicar_cambios:
            out[pos] = (op, "SIMULADO")
            return
        try:
            if op.accion == "SUBIR":
                almacen.subir(op.clave, descargar(op.file_id), metadatos(op.negocio, op.dni, op.fecha))
            else:
                almacen.eliminar(op.clave)
            logger.info("[MINIO] %s %s", op.accion, op.clave)
            out[pos] = (op, "HECHO")
        except Exception as exc:
            logger.warning("[MINIO_ERROR] %s %s: %s", op.accion, op.clave, exc)
            out[pos] = (op, "MINIO_ERROR")

    if hilos <= 1 or not aplicar_cambios:
        for pos, op in enumerate(ops):
            ejecutar(pos, op)
    else:
        with ThreadPoolExecutor(max_workers=hilos, thread_name_prefix="minio") as pool:
            for pos, op in enumerate(ops):
                pool.submit(ejecutar, pos, op)
    return out  # type: ignore[return-value]
