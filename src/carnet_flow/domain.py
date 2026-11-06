"""Reglas de negocio puras del bot de carnés (sin navegador ni Drive).

Todo lo que decide QUÉ hacer con cada DNI vive aquí para poder probarlo sin red:
normalización de DNI, lectura de fechas de carpetas de Drive, antigüedad de 3 años,
plan de la corrida y decisión de sincronización tras consultar SUCAMEC.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum

ANIOS_VIGENCIA = 3

NEGOCIOS = ("JV", "SELVA")

MESES = {
    1: "ENERO", 2: "FEBRERO", 3: "MARZO", 4: "ABRIL", 5: "MAYO", 6: "JUNIO",
    7: "JULIO", 8: "AGOSTO", 9: "SETIEMBRE", 10: "OCTUBRE", 11: "NOVIEMBRE", 12: "DICIEMBRE",
}

_DNI_RE = re.compile(r"^\d{8,9}$")
# dd.mm.aa(aa) tolerando puntos repetidos y prefijos ("SELVA.02.10.2023", "05..10..2023").
_FECHA_CARPETA_RE = re.compile(r"(?<!\d)(\d{1,2})\s*[./]+\s*(\d{1,2})\s*[./]+\s*(\d{2,4})(?!\d)")
# "10.Octubre", "1. ENERO", "6Junio", "01.Enero", "10.0CTUBRE" (cero por O)
_MES_CARPETA_RE = re.compile(r"^\s*(\d{1,2})\s*\.?\s*(0?[A-Za-zÁÉÍÓÚáéíóú]+)\s*$")
_FECHA_SIN_ANIO_RE = re.compile(r"(?<!\d)(\d{1,2})\s*\.+\s*(\d{1,2})\s*$")
# "21.082021": falta el punto entre mes y año
_FECHA_PEGADA_RE = re.compile(r"(?<!\d)(\d{1,2})\s*\.+\s*(\d{2})(\d{4})(?!\d)")
_EMISION_RE =re.compile(r"EMISI[OÓ]N\s*:?\s*(\d{1,2})/(\d{1,2})/(\d{4})", re.I)


def sin_tildes(texto: str) -> str:
    base = unicodedata.normalize("NFKD", str(texto or ""))
    return "".join(c for c in base if not unicodedata.combining(c))


def normalizar_dni(valor: object) -> str | None:
    """DNI/CE como texto. Recupera ceros iniciales de DNIs leídos como número (7 dígitos)."""
    if valor is None:
        return None
    if isinstance(valor, float) and valor.is_integer():
        valor = int(valor)
    texto = re.sub(r"\s+", "", str(valor)).strip()
    if texto.endswith(".0") and texto[:-2].isdigit():
        texto = texto[:-2]
    if not texto.isdigit():
        return None
    if len(texto) < 8:
        texto = texto.zfill(8)
    return texto if _DNI_RE.match(texto) else None


RUC_JV = "20100901481"


def negocio_desde_texto(texto: object) -> str | None:
    """Razón social libre -> JV | SELVA | None.

    Se compara sin tildes, espacios ni signos, así 'J & V RESGUARDO', 'J&V RESGUARDO S.A.C.',
    'J Y V RESGUARDO', 'JV' y el RUC de J&V son JV; 'J V SELVA', 'J&V RESGUARDO SELVA SAC',
    'SELVA' son SELVA. Cualquier mención a SELVA manda.
    """
    compacto = re.sub(r"[^A-Z0-9]", "", sin_tildes(str(texto or "")).upper())
    if not compacto:
        return None
    if "SELVA" in compacto:
        return "SELVA"
    if RUC_JV in compacto or "RESGUARDO" in compacto or re.match(r"^J[Y]?V", compacto):
        return "JV"
    return None


def parse_fecha_carpeta(nombre: str, anio_padre: int | None = None) -> date | None:
    """Fecha de una carpeta de día. Si el año escrito no coincide con el año padre, manda el padre."""
    m = _FECHA_CARPETA_RE.search(nombre or "") or _FECHA_PEGADA_RE.search(nombre or "")
    if not m:
        # "SELVA.03.07", "selva.28.08": día y mes sin año -> el año lo da la carpeta padre.
        corto = _FECHA_SIN_ANIO_RE.search(nombre or "")
        if not corto or not anio_padre:
            return None
        try:
            return date(anio_padre, int(corto.group(2)), int(corto.group(1)))
        except ValueError:
            return None
    dia, mes, anio = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if anio < 100:
        anio += 2000
    if anio_padre and anio != anio_padre:
        anio = anio_padre
    try:
        return date(anio, mes, dia)
    except ValueError:
        return None


def parse_mes_carpeta(nombre: str) -> int | None:
    m = _MES_CARPETA_RE.match(nombre or "")
    if not m:
        return None
    mes = int(m.group(1))
    return mes if 1 <= mes <= 12 else None


def parse_emision(texto: str) -> date | None:
    """'Vigencia carné ... EMISIÓN: 19/02/2025' -> date(2025, 2, 19)."""
    m = _EMISION_RE.search(texto or "")
    if not m:
        return None
    try:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None


def nombre_pdf(dni: str, fecha: date) -> str:
    """Convención de nombre: <DNI>_<dd.mm.aaaa>.pdf (sin '/', inválida en Windows)."""
    return f"{dni}_{fecha:%d.%m.%Y}.pdf"


def aniversario(fecha: date, anios: int) -> date:
    try:
        return fecha.replace(year=fecha.year + anios)
    except ValueError:  # 29 de febrero
        return fecha.replace(year=fecha.year + anios, day=28)


def esta_vencido(fecha_emision: date, hoy: date) -> bool:
    return hoy >= aniversario(fecha_emision, ANIOS_VIGENCIA)


def fecha_desde_ruta(partes: list[str]) -> tuple[date | None, bool]:
    """Fecha de emisión a partir de la ruta bajo la carpeta del negocio.

    partes = [año, (mes)?, (día)?, (subdía)?]. Devuelve (fecha, aproximada).
    Se usa la carpeta de día más profunda; sin día, el día 1 del mes (aproximada).
    """
    if not partes or not partes[0].strip().isdigit():
        return None, False
    anio = int(partes[0].strip())
    for nombre in reversed(partes[1:]):
        fecha = parse_fecha_carpeta(nombre, anio)
        if fecha:
            return fecha, False
    for nombre in partes[1:]:
        mes = parse_mes_carpeta(nombre)
        if mes:
            return date(anio, mes, 1), True
    return None, False


# --------------------------------------------------------------------------- índice


@dataclass(frozen=True)
class CarneDrive:
    negocio: str
    dni: str
    file_id: str
    ruta: str
    fecha_emision: date | None
    aproximada: bool = False


@dataclass
class IndiceDrive:
    activos: dict[tuple[str, str], list[CarneDrive]] = field(default_factory=dict)
    ignorados: list[tuple[str, str]] = field(default_factory=list)  # (ruta, nombre)

    def agregar(self, carne: CarneDrive) -> None:
        self.activos.setdefault((carne.negocio, carne.dni), []).append(carne)

    def vigente(self, negocio: str, dni: str) -> CarneDrive | None:
        """El carné más reciente; los sin fecha quedan al final."""
        items = self.activos.get((negocio, dni)) or []
        if not items:
            return None
        return max(items, key=lambda c: (c.fecha_emision is not None, c.fecha_emision or date.min))

    def anteriores(self, negocio: str, dni: str) -> list[CarneDrive]:
        actual = self.vigente(negocio, dni)
        return [c for c in self.activos.get((negocio, dni)) or [] if c is not actual]

    def total(self) -> int:
        return sum(len(v) for v in self.activos.values())


# --------------------------------------------------------------------------- plan


class Motivo(str, Enum):
    NUEVO = "NUEVO"                  # sin PDF en Drive
    SIN_FECHA = "SIN_FECHA"          # PDF en carpeta sin fecha legible
    VENCIDO = "VENCIDO"              # emisión >= 3 años: ¿renovó?
    VIGENTE = "VIGENTE"              # emisión < 3 años: se consulta igual (renovación anticipada)
    BAJA = "BAJA"                    # baja/renuncia: eliminar de Drive


@dataclass(frozen=True)
class ItemPlan:
    negocio: str
    dni: str
    motivo: Motivo
    actual: CarneDrive | None = None

    @property
    def consultar(self) -> bool:
        """Todo el padrón se consulta: SUCAMEC puede renovar antes de los 3 años
        (p. ej. emisión en Drive 23/10/2023, nueva emisión 06/10/2026). Solo las bajas no."""
        return self.motivo is not Motivo.BAJA


@dataclass(frozen=True)
class Plan:
    items: list[ItemPlan]
    eliminar: list[CarneDrive]
    duplicados: list[CarneDrive]

    def consultas(self, negocio: str | None = None, omitir_vigentes: bool = False) -> list[ItemPlan]:
        return [
            i for i in self.items
            if i.consultar and (negocio is None or i.negocio == negocio)
            and not (omitir_vigentes and i.motivo is Motivo.VIGENTE)
        ]

    def resumen(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for i in self.items:
            clave = f"{i.negocio}:{i.motivo.value}"
            out[clave] = out.get(clave, 0) + 1
        out["ELIMINAR_PDF"] = len(self.eliminar)
        out["DUPLICADOS_ANTERIORES"] = len(self.duplicados)
        return out


def construir_plan(
    padron: list[tuple[str, str]],
    bajas: list[tuple[str, str | None]],
    indice: IndiceDrive,
    hoy: date,
) -> Plan:
    """padron/bajas: (dni, negocio). Una baja sin negocio aplica a ambos negocios."""
    dnis_baja: set[tuple[str, str]] = set()
    for dni, negocio in bajas:
        for neg in ([negocio] if negocio else list(NEGOCIOS)):
            dnis_baja.add((neg, dni))

    items: list[ItemPlan] = []
    vistos: set[tuple[str, str]] = set()
    for dni, negocio in padron:
        clave = (negocio, dni)
        if clave in vistos or clave in dnis_baja:
            continue
        vistos.add(clave)
        actual = indice.vigente(negocio, dni)
        if actual is None:
            motivo = Motivo.NUEVO
        elif actual.fecha_emision is None:
            motivo = Motivo.SIN_FECHA
        elif esta_vencido(actual.fecha_emision, hoy):
            motivo = Motivo.VENCIDO
        else:
            motivo = Motivo.VIGENTE
        items.append(ItemPlan(negocio, dni, motivo, actual))

    eliminar: list[CarneDrive] = []
    for clave in sorted(dnis_baja):
        if clave in indice.activos:
            eliminar.extend(indice.activos[clave])
            items.append(ItemPlan(clave[0], clave[1], Motivo.BAJA, indice.vigente(*clave)))

    duplicados = [
        c for (neg, dni) in vistos for c in indice.anteriores(neg, dni)
    ]
    return Plan(items=items, eliminar=eliminar, duplicados=duplicados)


# --------------------------------------------------------------------------- sync


class Estado(str, Enum):
    ENCONTRADO = "ENCONTRADO"
    NO_ENCONTRADO = "NO_ENCONTRADO"
    ERROR = "ERROR"


@dataclass(frozen=True)
class ResultadoConsulta:
    negocio: str
    dni: str
    estado: Estado
    fecha_emision: date | None = None
    pdf: str | None = None
    detalle: str = ""
    filas: int = 0


class Accion(str, Enum):
    SUBIR = "SUBIR"                         # PDF nuevo a la carpeta de su fecha
    SUBIR_A_VENCIDOS = "SUBIR_A_VENCIDOS"   # PDF nuevo que ya nace vencido
    MOVER_A_VENCIDOS = "MOVER_A_VENCIDOS"   # PDF existente pasa a Vencidos
    ELIMINAR = "ELIMINAR"
    MOVER_A_CESES = "MOVER_A_CESES"         # cese: a <raíz>/CESES/<dd.mm.aaaa> (se vacía de golpe)
    NINGUNA = "NINGUNA"


@dataclass(frozen=True)
class Operacion:
    accion: Accion
    negocio: str
    dni: str
    fecha_emision: date | None
    motivo: str
    pdf: str | None = None
    file_id: str | None = None
    vencido: bool = False  # el PDF de file_id está en VENCIDOS (ceses)


def operaciones_ceses(
    ceses: list[tuple[str, str | None]], activos: IndiceDrive, vencidos: IndiceDrive
) -> tuple[list[Operacion], list[tuple[str, str | None]]]:
    """Cese: se retira TODO carné del DNI (activo y vencido) a CESES/. Sin negocio aplica a JV y SELVA.

    Devuelve (operaciones MOVER_A_CESES, ceses sin ningún PDF en Drive).
    """
    ops: list[Operacion] = []
    sin_pdf: list[tuple[str, str | None]] = []
    vistos: set[str] = set()
    for dni, negocio in ceses:
        encontrado = False
        for neg in ([negocio] if negocio else list(NEGOCIOS)):
            for indice, es_vencido in ((activos, False), (vencidos, True)):
                for c in indice.activos.get((neg, dni)) or []:
                    encontrado = True
                    if c.file_id in vistos:
                        continue
                    vistos.add(c.file_id)
                    ops.append(Operacion(Accion.MOVER_A_CESES, neg, dni, c.fecha_emision, "cese",
                                         file_id=c.file_id, vencido=es_vencido))
        if not encontrado:
            sin_pdf.append((dni, negocio))
    return ops, sin_pdf


def decidir_sync(item: ItemPlan, resultado: ResultadoConsulta | None, hoy: date) -> list[Operacion]:
    """Operaciones de Drive para un DNI consultado (o no) en SUCAMEC."""
    actual = item.actual
    neg, dni = item.negocio, item.dni

    def mover_actual(motivo: str) -> Operacion:
        return Operacion(Accion.MOVER_A_VENCIDOS, neg, dni, actual.fecha_emision, motivo, file_id=actual.file_id)

    if resultado is None or resultado.estado is not Estado.ENCONTRADO or not resultado.fecha_emision:
        if item.motivo is Motivo.VENCIDO and resultado is not None and resultado.estado is Estado.NO_ENCONTRADO:
            return [mover_actual("vencido; sin carné en la bandeja de impresión")]
        detalle = resultado.estado.value if resultado else "SIN_CONSULTA"
        return [Operacion(Accion.NINGUNA, neg, dni, actual.fecha_emision if actual else None, detalle)]

    fecha = resultado.fecha_emision
    nueva = Accion.SUBIR_A_VENCIDOS if esta_vencido(fecha, hoy) else Accion.SUBIR

    if actual is None:
        return [Operacion(nueva, neg, dni, fecha, "carné nuevo", pdf=resultado.pdf)]

    if actual.fecha_emision is None:
        return [
            mover_actual("reemplazado: carpeta sin fecha"),
            Operacion(nueva, neg, dni, fecha, "carné con fecha conocida", pdf=resultado.pdf),
        ]

    if fecha > actual.fecha_emision:
        return [
            mover_actual(f"renovado el {fecha:%d/%m/%Y}"),
            Operacion(nueva, neg, dni, fecha, "renovación", pdf=resultado.pdf),
        ]
    if esta_vencido(actual.fecha_emision, hoy):
        return [mover_actual("vencido sin renovación")]
    return [Operacion(Accion.NINGUNA, neg, dni, actual.fecha_emision, "sin cambios")]


def hoy_lima() -> date:
    return datetime.now().date()
