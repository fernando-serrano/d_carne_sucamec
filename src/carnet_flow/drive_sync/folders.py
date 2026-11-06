"""Resolución (y creación) de la carpeta de fecha de emisión de un carné.

Respeta la convención existente:
  JV     <negocio>/<año>/<m.MES>/<dd.mm.aaaa>
  SELVA  <negocio>/<año>/<m.MES>/<SELVA.dd.mm.aaaa>   (2024–2025: día directo bajo el año)
Vencidos repite la misma estructura bajo <raíz>/VENCIDOS/<negocio>/...
Se reutiliza la carpeta existente aunque su nombre sea irregular ("05..10..2023").
"""

from __future__ import annotations

from datetime import date

from ..domain import MESES, negocio_desde_texto, parse_fecha_carpeta, parse_mes_carpeta, sin_tildes
from .client import DriveClient, Item
from .index import Arbol


def nombre_mes(mes: int) -> str:
    return f"{mes}.{MESES[mes]}"


def nombre_dia(negocio: str, fecha: date) -> str:
    base = f"{fecha:%d.%m.%Y}"
    return f"SELVA.{base}" if negocio == "SELVA" else base


class ResolverCarpetas:
    def __init__(self, arbol: Arbol, client: DriveClient | None, carpeta_vencidos: str, crear: bool,
                 hoy: date | None = None, carpeta_ceses: str = "CESES"):
        self.arbol = arbol
        self.client = client
        self.carpeta_vencidos = carpeta_vencidos
        self.crear = crear
        self._seq = 0
        self.hoy = hoy or date.today()
        self.carpeta_ceses = carpeta_ceses

    # ---------------------------------------------------------------- utilidades
    def _hijo(self, parent_id: str, pred) -> Item | None:
        for carpeta in sorted(self.arbol.subcarpetas(parent_id), key=lambda c: c.name):
            if pred(carpeta.name):
                return carpeta
        return None

    def _obtener_o_crear(self, parent_id: str, nombre: str, pred) -> str:
        # Si hay carpetas duplicadas para el mismo día, manda la que ya tiene el nombre canónico.
        existente = self._hijo(parent_id, lambda n: n == nombre) or self._hijo(parent_id, pred)
        if existente:
            return existente.id
        if self.crear and self.client is not None:
            nuevo_id = self.client.crear_carpeta(nombre, parent_id)
        else:
            self._seq += 1
            nuevo_id = f"(nueva-{self._seq}:{nombre})"
        self.arbol.registrar_carpeta(Item(nuevo_id, nombre, "application/vnd.google-apps.folder", (parent_id,)))
        return nuevo_id

    def _raiz_negocio(self, base_id: str, negocio: str, nombre_si_falta: str) -> str:
        return self._obtener_o_crear(base_id, nombre_si_falta, lambda n: negocio_desde_texto(n) == negocio)

    def nombre_negocio(self, negocio: str) -> str:
        carpeta = self._hijo(self.arbol.root_id, lambda n: negocio_desde_texto(n) == negocio)
        if carpeta:
            return carpeta.name
        return "J&V RESGUARDO SELVA SAC" if negocio == "SELVA" else "J&V RESGUARDO SAC"

    # ---------------------------------------------------------------- pública
    def carpeta_para(self, negocio: str, fecha: date, vencidos: bool) -> str:
        base = self.arbol.root_id
        if vencidos:
            objetivo = sin_tildes(self.carpeta_vencidos).upper()
            base = self._obtener_o_crear(base, self.carpeta_vencidos, lambda n: sin_tildes(n).upper() == objetivo)
        neg_id = self._raiz_negocio(base, negocio, self.nombre_negocio(negocio))
        anio_id = self._obtener_o_crear(neg_id, str(fecha.year), lambda n: n.strip() == str(fecha.year))

        hijos = self.arbol.subcarpetas(anio_id)
        usa_meses = any(parse_mes_carpeta(c.name) for c in hijos)
        dias_directos = any(parse_fecha_carpeta(c.name, fecha.year) for c in hijos)
        padre_dia = anio_id
        if usa_meses or not dias_directos:
            padre_dia = self._obtener_o_crear(
                anio_id, nombre_mes(fecha.month), lambda n: parse_mes_carpeta(n) == fecha.month
            )
        return self._obtener_o_crear(
            padre_dia, nombre_dia(negocio, fecha), lambda n: parse_fecha_carpeta(n, fecha.year) == fecha
        )

    def carpeta_ceses_del_dia(self) -> str:
        """<raíz>/CESES/<dd.mm.aaaa>: todo lo retirado ese día, para vaciarlo de golpe."""
        objetivo = sin_tildes(self.carpeta_ceses).upper()
        base = self._obtener_o_crear(self.arbol.root_id, self.carpeta_ceses, lambda n: sin_tildes(n).upper() == objetivo)
        dia = f"{self.hoy:%d.%m.%Y}"
        return self._obtener_o_crear(base, dia, lambda n: n.strip() == dia)

    def ruta_legible(self, folder_id: str) -> str:
        partes: list[str] = []
        actual = folder_id
        while actual and actual != self.arbol.root_id:
            carpeta = self.arbol.carpetas.get(actual)
            if carpeta is None:
                break
            partes.append(carpeta.name)
            actual = carpeta.parents[0] if carpeta.parents else None
        return "/".join(reversed(partes))
