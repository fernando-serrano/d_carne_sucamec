"""Claves (rutas) de los carnés en MinIO.

  <prefijo>/<NEG>/<aaaa>/<mm>/<dd>/<DNI>_<dd.mm.aaaa>.pdf
  <prefijo>/vencidos/<NEG>/<aaaa>/<mm>/<dd>/<DNI>_<dd.mm.aaaa>.pdf
La fecha es la de emisión; el nombre es el mismo que en Drive.
"""

from __future__ import annotations

from datetime import date

from ..domain import nombre_pdf

ORIGEN = "carnet_sucamec_gadso"


def clave_minio(prefijo: str, negocio: str, dni: str, fecha: date, vencido: bool) -> str:
    base = prefijo.strip("/")
    rama = f"{base}/vencidos" if vencido else base
    return f"{rama}/{negocio}/{fecha:%Y/%m/%d}/{nombre_pdf(dni, fecha)}"


def metadatos(negocio: str, dni: str, fecha: date) -> dict[str, str]:
    return {"dni": dni, "negocio": negocio, "fecha-emision": fecha.isoformat(), "origen": ORIGEN}
