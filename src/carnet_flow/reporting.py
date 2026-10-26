from __future__ import annotations

from collections import Counter
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font

from .domain import Plan, ResultadoConsulta
from .drive_sync.sync import ResultadoSync


def _hoja(wb: Workbook, titulo: str, encabezado: list[str], filas: list[list[object]]) -> None:
    ws = wb.create_sheet(titulo)
    ws.append(encabezado)
    for c in ws[1]:
        c.font = Font(bold=True)
    for fila in filas:
        ws.append([("" if v is None else str(v)) for v in fila])
    for col in ws.columns:
        ancho = max(len(str(c.value or "")) for c in col)
        ws.column_dimensions[col[0].column_letter].width = min(60, max(10, ancho + 2))
    ws.freeze_panes = "A2"


def escribir_reporte(
    ruta: Path,
    plan: Plan,
    resultados: dict[tuple[str, str], ResultadoConsulta],
    sync: list[ResultadoSync],
    ignorados: list[tuple[str, str]],
) -> Path:
    wb = Workbook()
    wb.remove(wb.active)
    resumen = Counter()
    for k, v in plan.resumen().items():
        resumen[f"plan {k}"] = v
    for r in resultados.values():
        resumen[f"consulta {r.negocio}:{r.estado.value}"] += 1
    for s in sync:
        resumen[f"drive {s.operacion.accion.value}:{s.estado}"] += 1
    _hoja(wb, "Resumen", ["Concepto", "Cantidad"], [[k, v] for k, v in sorted(resumen.items())])
    _hoja(wb, "Plan", ["Negocio", "DNI", "Motivo", "Emisión en Drive", "Ruta en Drive"], [
        [i.negocio, i.dni, i.motivo.value,
         i.actual.fecha_emision.strftime("%d/%m/%Y") if i.actual and i.actual.fecha_emision else "",
         i.actual.ruta if i.actual else ""] for i in plan.items
    ])
    _hoja(wb, "Consultas SUCAMEC", ["Negocio", "DNI", "Estado", "Emisión SUCAMEC", "Filas", "Detalle"], [
        [r.negocio, r.dni, r.estado.value, r.fecha_emision.strftime("%d/%m/%Y") if r.fecha_emision else "",
         r.filas, r.detalle] for r in resultados.values()
    ])
    _hoja(wb, "Drive", ["Acción", "Estado", "Negocio", "DNI", "Emisión", "Destino", "Detalle"], [
        [s.operacion.accion.value, s.estado, s.operacion.negocio, s.operacion.dni,
         s.operacion.fecha_emision.strftime("%d/%m/%Y") if s.operacion.fecha_emision else "", s.destino, s.detalle]
        for s in sync
    ])
    _hoja(wb, "Duplicados en Drive", ["Negocio", "DNI", "Emisión", "Ruta"], [
        [c.negocio, c.dni, c.fecha_emision.strftime("%d/%m/%Y") if c.fecha_emision else "", c.ruta]
        for c in plan.duplicados
    ])
    _hoja(wb, "Archivos no reconocidos", ["Ruta", "Nombre"], [list(x) for x in ignorados])
    ruta.parent.mkdir(parents=True, exist_ok=True)
    wb.save(ruta)
    return ruta
