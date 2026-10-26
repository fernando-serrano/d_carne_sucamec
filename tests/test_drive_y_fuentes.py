import logging
from datetime import date
from pathlib import Path

import pytest
from openpyxl import Workbook

from carnet_flow.domain import Accion, Estado, Operacion, ResultadoConsulta
from carnet_flow.drive_sync.client import FOLDER, PDF, Item, SinPermisoDrive
from carnet_flow.drive_sync.folders import ResolverCarpetas
from carnet_flow.drive_sync.index import Arbol, construir_indice
from carnet_flow.drive_sync.sync import aplicar
from carnet_flow.orchestration_flow import manifest
from carnet_flow.sources.excel_source import expandir_negocios, leer_registros

LOG = logging.getLogger("test")
ROOT = "root"


def _arbol(rutas_pdf: dict[str, list[str]]):
    """rutas_pdf: {'J&V RESGUARDO SAC/2024/1.Enero/03.01.2024': ['111.pdf', ...]}"""
    arbol, pdfs, ids = Arbol(ROOT), [], {}
    for ruta, archivos in rutas_pdf.items():
        padre = ROOT
        for i, nombre in enumerate(ruta.split("/")):
            clave = "/".join(ruta.split("/")[: i + 1])
            if clave not in ids:
                ids[clave] = f"f{len(ids)}"
                arbol.registrar_carpeta(Item(ids[clave], nombre, FOLDER, (padre,)))
            padre = ids[clave]
        pdfs += [Item(f"p-{padre}-{a}", a, PDF, (padre,)) for a in archivos]
    return arbol, pdfs


ESTRUCTURA = {
    "J&V RESGUARDO SAC/2021/10.0CTUBRE/05..10..2021": ["80000011.pdf"],
    "J&V RESGUARDO SAC/2024/01.Enero/03.01.2024": ["80000011.pdf", "006474488.pdf", "notas.pdf"],
    "J&V RESGUARDO SAC/DFNUEVOS": ["80000005.pdf"],
    "J&V RESGUARDO SELVA SAC/2024/SELVA.01.08.2024": ["80000006.pdf"],
    "VENCIDOS/J&V RESGUARDO SAC/2020/1.ENERO/02.01.2020": ["11111111.pdf"],
}


def test_indice_desde_estructura_real():
    arbol, pdfs = _arbol(ESTRUCTURA)
    pdfs.append(Item("fuera", "99999999.pdf", PDF, ("otra-raiz",)))
    idx = construir_indice(arbol, pdfs, "VENCIDOS")

    assert idx.vigente("JV", "80000011").fecha_emision == date(2024, 1, 3)
    assert [c.fecha_emision for c in idx.anteriores("JV", "80000011")] == [date(2021, 10, 5)]
    assert idx.vigente("JV", "006474488") is not None
    assert idx.vigente("JV", "80000005").fecha_emision is None          # DFNUEVOS
    assert idx.vigente("SELVA", "80000006").fecha_emision == date(2024, 8, 1)
    assert ("JV", "11111111") not in idx.activos                         # vencidos no es activo
    assert ("JV", "99999999") not in idx.activos                         # fuera de la raíz
    assert idx.ignorados == [("J&V RESGUARDO SAC/2024/01.Enero/03.01.2024", "notas.pdf")]


def test_resolver_reutiliza_carpetas_irregulares_y_crea_las_que_faltan():
    arbol, _ = _arbol(ESTRUCTURA)
    r = ResolverCarpetas(arbol, None, "VENCIDOS", crear=False)

    existente = r.carpeta_para("JV", date(2021, 10, 5), vencidos=False)
    assert r.ruta_legible(existente) == "J&V RESGUARDO SAC/2021/10.0CTUBRE/05..10..2021"

    nueva = r.carpeta_para("JV", date(2026, 10, 21), vencidos=False)
    assert r.ruta_legible(nueva) == "J&V RESGUARDO SAC/2026/10.OCTUBRE/21.10.2026"

    # SELVA 2024 tiene días directo bajo el año: se respeta
    selva = r.carpeta_para("SELVA", date(2024, 9, 2), vencidos=False)
    assert r.ruta_legible(selva) == "J&V RESGUARDO SELVA SAC/2024/SELVA.02.09.2024"

    venc = r.carpeta_para("JV", date(2020, 1, 2), vencidos=True)
    assert r.ruta_legible(venc) == "VENCIDOS/J&V RESGUARDO SAC/2020/1.ENERO/02.01.2020"
    venc_nueva = r.carpeta_para("SELVA", date(2022, 3, 4), vencidos=True)
    assert r.ruta_legible(venc_nueva) == "VENCIDOS/J&V RESGUARDO SELVA SAC/2022/3.MARZO/SELVA.04.03.2022"


def test_crea_anio_mes_y_dia_nuevos_con_formato_canonico():
    arbol, _ = _arbol(ESTRUCTURA)
    drive = DriveFalso()
    r = ResolverCarpetas(arbol, drive, "VENCIDOS", crear=True)
    jv = r.carpeta_para("JV", date(2027, 1, 5), vencidos=False)              # año que no existe
    assert r.ruta_legible(jv) == "J&V RESGUARDO SAC/2027/1.ENERO/05.01.2027"
    selva = r.carpeta_para("SELVA", date(2027, 2, 9), vencidos=False)
    assert r.ruta_legible(selva) == "J&V RESGUARDO SELVA SAC/2027/2.FEBRERO/SELVA.09.02.2027"
    assert [c[1] for c in drive.llamadas] == ["2027", "1.ENERO", "05.01.2027", "2027", "2.FEBRERO", "SELVA.09.02.2027"]
    # segunda vez el mismo día: reutiliza, no crea de nuevo
    r.carpeta_para("JV", date(2027, 1, 5), vencidos=False)
    assert len(drive.llamadas) == 6


class DriveFalso:
    modo = "falso"

    def __init__(self, falla_subir=False):
        self.llamadas, self.falla_subir = [], falla_subir

    def crear_carpeta(self, nombre, parent):
        self.llamadas.append(("carpeta", nombre))
        return f"c-{nombre}"

    def subir_pdf(self, ruta, nombre, parent, props):
        if self.falla_subir:
            raise SinPermisoDrive("cuota 0")
        self.llamadas.append(("subir", nombre, parent, props["fecha_emision"]))

    def mover(self, fid, destino):
        self.llamadas.append(("mover", fid, destino))

    def eliminar(self, fid):
        self.llamadas.append(("eliminar", fid))


def _ops():
    return [
        Operacion(Accion.SUBIR, "JV", "12345678", date(2026, 10, 1), "nuevo", pdf="x.pdf"),
        Operacion(Accion.MOVER_A_VENCIDOS, "JV", "80000011", date(2021, 10, 5), "vencido", file_id="F1"),
        Operacion(Accion.ELIMINAR, "JV", "80000005", None, "baja", file_id="F2"),
        Operacion(Accion.NINGUNA, "JV", "1", None, "sin cambios"),
    ]


def test_sync_simulado_no_toca_drive():
    arbol, _ = _arbol(ESTRUCTURA)
    drive = DriveFalso()
    res = aplicar(_ops(), drive, ResolverCarpetas(arbol, drive, "VENCIDOS", crear=False), False, LOG)
    assert drive.llamadas == []
    assert [r.estado for r in res] == ["SIMULADO", "SIMULADO", "SIMULADO", "OMITIDO"]
    assert res[1].destino == "VENCIDOS/J&V RESGUARDO SAC/2021/10.OCTUBRE/05.10.2021"


def test_sync_aplicado_y_sin_permiso_no_detiene_el_resto():
    arbol, _ = _arbol(ESTRUCTURA)
    drive = DriveFalso(falla_subir=True)
    res = aplicar(_ops(), drive, ResolverCarpetas(arbol, drive, "VENCIDOS", crear=True), True, LOG)
    assert [r.estado for r in res] == ["SIN_PERMISO", "HECHO", "HECHO", "OMITIDO"]
    assert ("eliminar", "F2") in drive.llamadas
    assert any(c[0] == "mover" and c[1] == "F1" for c in drive.llamadas)


def test_reanudar_no_repite_operaciones_hechas(tmp_path: Path):
    arbol, _ = _arbol(ESTRUCTURA)
    registro = tmp_path / "drive_hechas.txt"
    primera = DriveFalso()
    aplicar(_ops(), primera, ResolverCarpetas(arbol, primera, "VENCIDOS", crear=True), True, LOG, registro)
    segunda = DriveFalso()
    res = aplicar(_ops(), segunda, ResolverCarpetas(arbol, segunda, "VENCIDOS", crear=True), True, LOG, registro)
    assert [c for c in segunda.llamadas if c[0] != "carpeta"] == []
    assert [r.estado for r in res] == ["HECHO", "HECHO", "HECHO", "OMITIDO"]


def test_cache_del_listado(tmp_path: Path):
    from carnet_flow.drive_sync.index import cargar_arbol

    class Lister:
        llamadas = 0

        def listar(self, q):
            Lister.llamadas += 1
            return [Item("f1", "J&V RESGUARDO SAC", FOLDER, (ROOT,))] if "folder" in q else [
                Item("p1", "12345678.pdf", PDF, ("f1",))]

    cache = tmp_path / "listado.json"
    a1, p1 = cargar_arbol(Lister(), ROOT, cache)
    a2, p2 = cargar_arbol(Lister(), ROOT, cache)
    assert Lister.llamadas == 2 and p1 == p2 and a2.ruta(p2[0]) == ["J&V RESGUARDO SAC"]


def test_fecha_del_nombre_manda_sobre_la_carpeta():
    arbol, pdfs = _arbol({"J&V RESGUARDO SAC/2026/10.OCTUBRE/21.10.2026": ["80000004_06.10.2026.pdf", "11111111.pdf"]})
    idx = construir_indice(arbol, pdfs, "VENCIDOS")
    assert idx.vigente("JV", "80000004").fecha_emision == date(2026, 10, 6)
    assert idx.vigente("JV", "11111111").fecha_emision == date(2026, 10, 21)


def test_nombre_pdf_y_subida_usan_el_formato():
    from carnet_flow.domain import nombre_pdf
    assert nombre_pdf("07654313", date(2026, 10, 6)) == "07654313_06.10.2026.pdf"
    arbol, _ = _arbol(ESTRUCTURA)
    drive = DriveFalso()
    aplicar([_ops()[0]], drive, ResolverCarpetas(arbol, drive, "VENCIDOS", crear=True), True, LOG)
    assert drive.llamadas[-1][1] == "12345678_01.10.2026.pdf"


def test_nombres_irregulares_de_pdf():
    arbol, pdfs = _arbol({"J&V RESGUARDO SAC/2021/4. ABRIL": ["80000007..pdf", "80000010_.pdf", "80000001 - VIGILANCIA PRIVADA.pdf", "carneGssp.pdf"]})
    idx = construir_indice(arbol, pdfs, "VENCIDOS")
    assert {d for (_, d) in idx.activos} == {"80000007", "80000010", "80000001"}
    assert [n for _, n in idx.ignorados] == ["carneGssp.pdf"]


def test_excel_padron_y_bajas(tmp_path: Path):
    wb = Workbook()
    ws = wb.active
    ws.title = "Hoja1"
    ws.append(["Reporte de personal"])
    ws.append(["N°", "DNI", "Razón Social"])
    ws.append([1, "07654313", "J&V RESGUARDO SAC"])
    ws.append([2, 7654314, "J&V RESGUARDO SELVA SAC"])
    ws.cell(row=4, column=2).number_format = "00000000"
    ws.append([3, "ABC", "J&V RESGUARDO SAC"])
    ws.append([4, "80000009", ""])
    ruta = tmp_path / "padron.xlsx"
    wb.save(ruta)

    ws.append([5, "11111111", "J & V RESGUARDO"])
    ws.append([6, "22222222", "J V SELVA"])
    ws.append([7, "33333333", "OTRA EMPRESA"])
    wb.save(ruta)

    lectura = leer_registros(ruta)
    assert lectura.registros == [("07654313", "JV"), ("07654314", "SELVA"), ("80000009", None),
                                 ("11111111", "JV"), ("22222222", "SELVA"), ("33333333", None)]
    assert lectura.invalidos == [(5, "ABC")]
    assert lectura.negocio_no_reconocido == [(9, "OTRA EMPRESA")]
    assert ("80000009", "JV") in expandir_negocios(lectura.registros)
    assert ("80000009", "SELVA") in expandir_negocios(lectura.registros)


def test_manifest_checkpoint(tmp_path: Path):
    ruta = tmp_path / "w1.jsonl"
    manifest.escribir(ruta, ResultadoConsulta("JV", "1", Estado.ENCONTRADO, date(2025, 1, 1), "a.pdf"), "w1")
    manifest.escribir(ruta, ResultadoConsulta("JV", "1", Estado.ERROR, detalle="x"), "w1")
    manifest.escribir(ruta, ResultadoConsulta("JV", "2", Estado.ERROR, detalle="x"), "w1")
    with ruta.open("a", encoding="utf-8") as fh:
        fh.write('{"negocio": "JV", "dni": "3", "est')  # línea truncada por un corte
    leidos = manifest.leer(tmp_path)
    assert leidos[("JV", "1")].estado is Estado.ENCONTRADO
    assert manifest.terminados(tmp_path) == {("JV", "1")}
