"""Segmentación del Drive existente: los carnés no vigentes pasan a VENCIDOS/<misma estructura>.

Por (negocio, DNI) se queda activo solo el carné con la emisión más reciente, y solo si
no cumplió 3 años. Los anteriores (renovados) y el más reciente vencido van a VENCIDOS.
Sin fecha o con fecha aproximada (solo mes) no se mueven: se reportan para revisión.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from ..domain import CarneDrive, IndiceDrive, esta_vencido


@dataclass(frozen=True)
class Segmento:
    carne: CarneDrive
    tipo: str  # MOVER | REVISAR
    motivo: str


def planificar_vencidos(indice: IndiceDrive, hoy: date) -> list[Segmento]:
    out: list[Segmento] = []
    for carnes in indice.activos.values():
        fechados = [c for c in carnes if c.fecha_emision and not c.aproximada]
        for c in carnes:
            if c not in fechados:
                out.append(Segmento(c, "REVISAR", "sin fecha de emisión exacta"))
        if not fechados:
            continue
        ultima = max(c.fecha_emision for c in fechados)
        for c in fechados:
            if c.fecha_emision < ultima:
                out.append(Segmento(c, "MOVER", f"renovado el {ultima:%d.%m.%Y}"))
            elif esta_vencido(c.fecha_emision, hoy):
                out.append(Segmento(c, "MOVER", "más de 3 años sin renovación"))
    return out
