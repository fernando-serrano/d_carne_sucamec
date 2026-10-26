"""Entrada por Excel local (padrón y bajas/renuncias).

Columnas aceptadas (encabezado en la primera fila con datos, sin importar tildes ni mayúsculas):
  documento: NRO DOCUMENTO | DNI | DOCUMENTO | DNI/CE
  negocio:   NEGOCIO | RAZON SOCIAL | EMPRESA | CLIENTE
Una fila sin negocio reconocible aplica a ambos negocios (JV y SELVA).
La interfaz `leer_registros` es la que reemplazará la fuente GADSO.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from openpyxl import load_workbook

from ..domain import NEGOCIOS, negocio_desde_texto, normalizar_dni, sin_tildes

COLS_DNI = ("NRO DOCUMENTO", "DNI", "DOCUMENTO", "DNI/CE", "NRO DNI", "NUMERO DE DOCUMENTO")
COLS_NEGOCIO = ("NEGOCIO", "RAZON SOCIAL", "RAZON_SOCIAL", "RAZONSOCIAL", "EMPRESA", "CLIENTE", "UNIDAD DE NEGOCIO")


@dataclass
class Lectura:
    registros: list[tuple[str, str | None]] = field(default_factory=list)
    invalidos: list[tuple[int, str]] = field(default_factory=list)  # (fila, valor)
    sin_negocio: int = 0
    negocio_no_reconocido: list[tuple[int, str]] = field(default_factory=list)  # (fila, texto)


def _norm(valor: object) -> str:
    return sin_tildes(str(valor or "")).strip().upper()


def _valor_dni(celda) -> object:
    """Recupera ceros iniciales cuando el número tiene formato '00000000'."""
    valor = celda.value
    formato = str(celda.number_format or "")
    if isinstance(valor, (int, float)) and set(formato) == {"0"}:
        return str(int(valor)).zfill(len(formato))
    return valor


def leer_registros(ruta: str | Path) -> Lectura:
    wb = load_workbook(ruta, read_only=False, data_only=True)
    try:
        lectura = Lectura()
        for ws in wb.worksheets:
            encabezado_fila, col_dni, col_neg = None, None, None
            for fila in ws.iter_rows(min_row=1, max_row=min(ws.max_row, 15)):
                nombres = {_norm(c.value): c.column for c in fila if c.value is not None}
                col_dni = next((nombres[c] for c in COLS_DNI if c in nombres), None)
                if col_dni:
                    col_neg = next((nombres[c] for c in COLS_NEGOCIO if c in nombres), None)
                    encabezado_fila = fila[0].row
                    break
            if not col_dni:
                continue
            # Sin columna de negocio, el nombre de la hoja puede indicarlo ("SELVA").
            negocio_hoja = negocio_desde_texto(ws.title) if not col_neg else None
            for fila in ws.iter_rows(min_row=encabezado_fila + 1):
                celda = fila[col_dni - 1]
                if celda.value is None or str(celda.value).strip() == "":
                    continue
                dni = normalizar_dni(_valor_dni(celda))
                if not dni:
                    lectura.invalidos.append((celda.row, str(celda.value)))
                    continue
                texto_neg = fila[col_neg - 1].value if col_neg else None
                negocio = negocio_desde_texto(texto_neg) if col_neg else negocio_hoja
                if negocio is None:
                    lectura.sin_negocio += 1
                    if texto_neg is not None and str(texto_neg).strip():
                        lectura.negocio_no_reconocido.append((celda.row, str(texto_neg).strip()))
                lectura.registros.append((dni, negocio))
        return lectura
    finally:
        wb.close()


def expandir_negocios(registros: list[tuple[str, str | None]]) -> list[tuple[str, str]]:
    """Padrón: una fila sin negocio se consulta en ambos."""
    out: list[tuple[str, str]] = []
    for dni, negocio in registros:
        out.extend((dni, n) for n in ([negocio] if negocio else NEGOCIOS))
    return out


def resolver_excel(explicito: str, carpeta: Path, prefijo: str) -> Path | None:
    if explicito:
        return Path(explicito)
    if not carpeta.exists():
        return None
    candidatos = [p for p in carpeta.glob("*.xlsx") if not p.name.startswith("~$") and _norm(p.stem).startswith(prefijo)]
    return max(candidatos, key=lambda p: p.stat().st_mtime) if candidatos else None
