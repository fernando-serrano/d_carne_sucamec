from __future__ import annotations

import logging
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


def aplicar(
    operaciones: list[Operacion],
    client: DriveClient | None,
    resolver: ResolverCarpetas,
    aplicar_cambios: bool,
    logger: logging.Logger,
    registro: Path | None = None,
) -> list[ResultadoSync]:
    """`registro`: operaciones ya HECHAS en esta corrida; al reanudar no se repiten (sin duplicar subidas)."""
    hechas = set(registro.read_text(encoding="utf-8").splitlines()) if registro and registro.exists() else set()
    out: list[ResultadoSync] = []
    for op in operaciones:
        if op.accion is Accion.NINGUNA:
            out.append(ResultadoSync(op, "OMITIDO", detalle=op.motivo))
            continue
        if _clave(op) in hechas:
            out.append(ResultadoSync(op, "HECHO", detalle="aplicada en una ejecución previa de la corrida"))
            continue
        try:
            destino = ""
            if op.accion is Accion.ELIMINAR:
                if aplicar_cambios:
                    client.eliminar(op.file_id)
            else:
                if op.fecha_emision is None:
                    raise ValueError("operación sin fecha de emisión")
                vencidos = op.accion in (Accion.MOVER_A_VENCIDOS, Accion.SUBIR_A_VENCIDOS)
                carpeta = resolver.carpeta_para(op.negocio, op.fecha_emision, vencidos)
                destino = resolver.ruta_legible(carpeta)
                if aplicar_cambios:
                    if op.accion is Accion.MOVER_A_VENCIDOS:
                        client.mover(op.file_id, carpeta)
                    else:
                        client.subir_pdf(Path(op.pdf), nombre_archivo(op.dni, op.fecha_emision), carpeta, {
                            "dni": op.dni, "negocio": op.negocio,
                            "fecha_emision": op.fecha_emision.isoformat(), "origen": "carnet_sucamec_gadso",
                        })
            estado = "HECHO" if aplicar_cambios else "SIMULADO"
            if aplicar_cambios and registro is not None:
                with registro.open("a", encoding="utf-8") as fh:
                    fh.write(_clave(op) + "\n")
            logger.info("[%s] %s %s %s -> %s", estado, op.accion.value, op.negocio, op.dni, destino or op.file_id)
            out.append(ResultadoSync(op, estado, destino, op.motivo))
        except SinPermisoDrive as exc:
            logger.warning("[SIN_PERMISO] %s %s %s: %s", op.accion.value, op.negocio, op.dni, exc)
            out.append(ResultadoSync(op, "SIN_PERMISO", detalle=str(exc)))
        except Exception as exc:  # una operación fallida no detiene el resto
            logger.exception("[ERROR] %s %s %s", op.accion.value, op.negocio, op.dni)
            out.append(ResultadoSync(op, "ERROR", detalle=str(exc)))
    return out
