from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from ..domain import Accion, Operacion, nombre_pdf
from .client import DriveClient, SinPermisoDrive
from .folders import ResolverCarpetas


@dataclass(frozen=True)
class ResultadoSync:
    operacion: Operacion
    estado: str          # HECHO | SIMULADO | SIN_PERMISO | ERROR | OMITIDO
    destino: str = ""
    detalle: str = ""


def nombre_archivo(dni: str, fecha) -> str:
    return nombre_pdf(dni, fecha)


def _clave(op: Operacion) -> str:
    return f"{op.accion.value}|{op.negocio}|{op.dni}|{op.fecha_emision}|{op.file_id or ''}"


def _resultado_fallido(op: Operacion, exc: Exception, logger: logging.Logger) -> ResultadoSync:
    if isinstance(exc, SinPermisoDrive):
        logger.warning("[SIN_PERMISO] %s %s %s: %s", op.accion.value, op.negocio, op.dni, exc)
        return ResultadoSync(op, "SIN_PERMISO", detalle=str(exc))
    logger.error("[ERROR] %s %s %s", op.accion.value, op.negocio, op.dni, exc_info=exc)
    return ResultadoSync(op, "ERROR", detalle=str(exc))


def aplicar(
    operaciones: list[Operacion],
    client: DriveClient | None,
    resolver: ResolverCarpetas,
    aplicar_cambios: bool,
    logger: logging.Logger,
    registro: Path | None = None,
    hilos: int = 1,
) -> list[ResultadoSync]:
    """`registro`: operaciones ya HECHAS en esta corrida; al reanudar no se repiten (sin duplicar subidas).

    Las carpetas destino se resuelven (y crean) primero, en orden y en un solo hilo, para no
    duplicarlas; luego las subidas/movimientos van en paralelo con `hilos` simultáneos.
    """
    hechas = set(registro.read_text(encoding="utf-8").splitlines()) if registro and registro.exists() else set()
    out: list[ResultadoSync | None] = [None] * len(operaciones)
    pendientes: list[tuple[int, Operacion, str, str]] = []  # (posición, op, carpeta, destino legible)
    for pos, op in enumerate(operaciones):
        if op.accion is Accion.NINGUNA:
            out[pos] = ResultadoSync(op, "OMITIDO", detalle=op.motivo)
            continue
        if _clave(op) in hechas:
            out[pos] = ResultadoSync(op, "HECHO", detalle="aplicada en una ejecución previa de la corrida")
            continue
        try:
            carpeta = destino = ""
            if op.accion is not Accion.ELIMINAR:
                if op.fecha_emision is None:
                    raise ValueError("operación sin fecha de emisión")
                vencidos = op.accion in (Accion.MOVER_A_VENCIDOS, Accion.SUBIR_A_VENCIDOS)
                carpeta = resolver.carpeta_para(op.negocio, op.fecha_emision, vencidos)
                destino = resolver.ruta_legible(carpeta)
            pendientes.append((pos, op, carpeta, destino))
        except Exception as exc:  # una operación fallida no detiene el resto
            out[pos] = _resultado_fallido(op, exc, logger)

    candado = threading.Lock()

    def ejecutar(pos: int, op: Operacion, carpeta: str, destino: str) -> None:
        try:
            if aplicar_cambios:
                if op.accion is Accion.ELIMINAR:
                    client.eliminar(op.file_id)
                elif op.accion is Accion.MOVER_A_VENCIDOS:
                    client.mover(op.file_id, carpeta)
                else:
                    client.subir_pdf(Path(op.pdf), nombre_archivo(op.dni, op.fecha_emision), carpeta, {
                        "dni": op.dni, "negocio": op.negocio,
                        "fecha_emision": op.fecha_emision.isoformat(), "origen": "carnet_sucamec_gadso",
                    })
            estado = "HECHO" if aplicar_cambios else "SIMULADO"
            if aplicar_cambios and registro is not None:
                with candado, registro.open("a", encoding="utf-8") as fh:
                    fh.write(_clave(op) + "\n")
            logger.info("[%s] %s %s %s -> %s", estado, op.accion.value, op.negocio, op.dni, destino or op.file_id)
            out[pos] = ResultadoSync(op, estado, destino, op.motivo)
        except Exception as exc:  # una operación fallida no detiene el resto
            out[pos] = _resultado_fallido(op, exc, logger)

    if hilos <= 1 or not aplicar_cambios:
        for pendiente in pendientes:
            ejecutar(*pendiente)
    else:
        with ThreadPoolExecutor(max_workers=hilos, thread_name_prefix="drive") as pool:
            for pendiente in pendientes:
                pool.submit(ejecutar, *pendiente)
    return out  # type: ignore[return-value]
