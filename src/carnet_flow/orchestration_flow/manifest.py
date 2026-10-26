"""Manifiesto JSONL por worker: un evento terminal por DNI consultado (checkpoint)."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

from ..domain import Estado, ResultadoConsulta


def escribir(ruta: Path, resultado: ResultadoConsulta, worker: str) -> None:
    evento = {
        "negocio": resultado.negocio,
        "dni": resultado.dni,
        "estado": resultado.estado.value,
        "fecha_emision": resultado.fecha_emision.isoformat() if resultado.fecha_emision else None,
        "pdf": resultado.pdf,
        "detalle": resultado.detalle,
        "filas": resultado.filas,
        "worker": worker,
        "ts": datetime.now().isoformat(timespec="seconds"),
    }
    ruta.parent.mkdir(parents=True, exist_ok=True)
    with ruta.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(evento, ensure_ascii=False) + "\n")
        fh.flush()


def leer(carpeta: Path) -> dict[tuple[str, str], ResultadoConsulta]:
    """Último evento por (negocio, dni). Un ERROR posterior no pisa un ENCONTRADO previo."""
    out: dict[tuple[str, str], ResultadoConsulta] = {}
    for ruta in sorted(carpeta.glob("*.jsonl")):
        for linea in ruta.read_text(encoding="utf-8").splitlines():
            if not linea.strip():
                continue
            try:
                e = json.loads(linea)
            except json.JSONDecodeError:
                continue  # línea truncada por un corte: se reconsulta ese DNI
            r = ResultadoConsulta(
                e["negocio"], e["dni"], Estado(e["estado"]),
                date.fromisoformat(e["fecha_emision"]) if e.get("fecha_emision") else None,
                e.get("pdf"), e.get("detalle", ""), int(e.get("filas") or 0),
            )
            previo = out.get((r.negocio, r.dni))
            if previo and previo.estado is not Estado.ERROR and r.estado is Estado.ERROR:
                continue
            out[(r.negocio, r.dni)] = r
    return out


def terminados(carpeta: Path) -> set[tuple[str, str]]:
    """DNIs que no se vuelven a consultar al reanudar (los ERROR sí se reintentan)."""
    return {k for k, r in leer(carpeta).items() if r.estado is not Estado.ERROR}
