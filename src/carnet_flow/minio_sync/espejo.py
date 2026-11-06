"""Replica en MinIO una operación ya aplicada en Drive (Drive es la base)."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from ..domain import Accion, Operacion
from .claves import clave_minio, metadatos
from .store import AlmacenCarnets


def replicar(op: Operacion, almacen: AlmacenCarnets, prefijo: str, descargar: Callable[[str], bytes]) -> str:
    """HECHO | OMITIDO. Lanza si falla el almacén (quien llama lo marca MINIO_ERROR)."""
    if op.accion is Accion.NINGUNA or op.fecha_emision is None:
        return "OMITIDO"  # sin fecha no hay clave: nunca se migró a MinIO
    fecha = op.fecha_emision
    activa = clave_minio(prefijo, op.negocio, op.dni, fecha, vencido=False)
    vencida = clave_minio(prefijo, op.negocio, op.dni, fecha, vencido=True)
    meta = metadatos(op.negocio, op.dni, fecha)

    if op.accion in (Accion.SUBIR, Accion.SUBIR_A_VENCIDOS):
        destino = vencida if op.accion is Accion.SUBIR_A_VENCIDOS else activa
        almacen.subir(destino, Path(op.pdf).read_bytes(), meta)
    elif op.accion is Accion.MOVER_A_VENCIDOS:
        if activa in almacen.listar(activa):
            almacen.copiar(activa, vencida)
            almacen.eliminar(activa)
        else:  # aún no migrado: se toma de Drive
            almacen.subir(vencida, descargar(op.file_id), meta)
    elif op.accion is Accion.ELIMINAR:
        almacen.eliminar(vencida if op.vencido else activa)
    return "HECHO"
