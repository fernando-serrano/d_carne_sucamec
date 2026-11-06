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


def test_indice_vencidos_solo_rama_vencidos():
    from carnet_flow.drive_sync.index import construir_indice_vencidos

    arbol, pdfs = _arbol(ESTRUCTURA)
    venc = construir_indice_vencidos(arbol, pdfs, "VENCIDOS")
    assert list(venc.activos) == [("JV", "11111111")]
    assert venc.vigente("JV", "11111111").fecha_emision == date(2020, 1, 2)
    # El índice activo no cambia.
    assert ("JV", "11111111") not in construir_indice(arbol, pdfs, "VENCIDOS").activos


def test_descargar_devuelve_bytes(monkeypatch):
    from carnet_flow.drive_sync import client as mod

    class _Req:
        pass

    class _Files:
        def get_media(self, fileId, supportsAllDrives=True):
            assert fileId == "f1"
            return _Req()

    class _Svc:
        def files(self):
            return _Files()

    class _Descarga:
        def __init__(self, fh, req):
            self.fh = fh

        def next_chunk(self, num_retries=0):
            self.fh.write(b"%PDF-1.4")
            return None, True

    monkeypatch.setattr(mod, "MediaIoBaseDownload", _Descarga)
    cli = object.__new__(mod.DriveClient)
    import threading
    cli._local = threading.local()
    cli._local.svc = _Svc()
    assert cli.descargar("f1") == b"%PDF-1.4"


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


def test_sync_en_paralelo_conserva_orden_registro_y_carpetas_unicas(tmp_path: Path):
    arbol, _ = _arbol(ESTRUCTURA)
    registro = tmp_path / "drive_hechas.txt"
    ops = [Operacion(Accion.SUBIR, "JV", f"7{i:07d}", date(2026, 11, 1 + i % 3), "nuevo", pdf="x.pdf")
           for i in range(30)] + _ops()
    drive = DriveFalso()
    res = aplicar(ops, drive, ResolverCarpetas(arbol, drive, "VENCIDOS", crear=True), True, LOG, registro, hilos=8)
    assert [r.operacion for r in res] == ops
    assert [r.estado for r in res] == ["HECHO"] * 33 + ["OMITIDO"]
    assert len(registro.read_text(encoding="utf-8").splitlines()) == 33
    arbol_sec, _ = _arbol(ESTRUCTURA)
    secuencial = DriveFalso()
    aplicar(ops, secuencial, ResolverCarpetas(arbol_sec, secuencial, "VENCIDOS", crear=True), True, LOG)
    # mismas carpetas creadas, en el mismo orden: la concurrencia no las duplica
    assert [c for c in drive.llamadas if c[0] == "carpeta"] == [c for c in secuencial.llamadas if c[0] == "carpeta"]


def test_puente_reintenta_solo_limites_de_ritmo(monkeypatch):
    from carnet_flow.drive_sync import client as mod

    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    puente = mod.PuenteAppsScript("u", "c")
    respuestas = iter([{"ok": False, "error": "Service invoked too many times in a short time: driveWriteVolume rateMax"},
                       {"ok": True, "id": "X"}])
    monkeypatch.setattr(puente, "_enviar", lambda cuerpo: next(respuestas))
    assert puente.llamar("subir")["id"] == "X"
    assert puente.reintentos_por_limite == 1

    monkeypatch.setattr(puente, "_enviar", lambda cuerpo: {"ok": False, "error": "carpeta fuera de la raíz"})
    with pytest.raises(SinPermisoDrive):
        puente.llamar("subir")
    assert puente.reintentos_por_limite == 1


class PuenteCortado:
    """Corta la conexión en las primeras `cortes` llamadas; `quedo` indica si Drive igual creó el archivo."""

    def __init__(self, cortes, quedo):
        self.cortes, self.quedo, self.llamadas = cortes, quedo, 0

    def llamar(self, accion, **datos):
        self.llamadas += 1
        if self.llamadas <= self.cortes:
            raise ConnectionResetError(10054, "conexión cortada por el host remoto")
        return {"ok": True, "id": "NUEVO"}


def _cliente_con(puente, existente, monkeypatch, tmp_path):
    from carnet_flow.drive_sync import client as mod

    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    c = object.__new__(mod.DriveClient)
    c.puente, c.logger = puente, LOG
    c._buscar_en_carpeta = lambda nombre, parent: existente
    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"%PDF")
    return c, pdf


def test_subida_cortada_que_si_quedo_no_se_duplica(monkeypatch, tmp_path):
    puente = PuenteCortado(cortes=1, quedo=True)
    c, pdf = _cliente_con(puente, "YA_EXISTE", monkeypatch, tmp_path)
    assert c.subir_pdf(pdf, "47514202_21.10.2026.pdf", "P", {}) == "YA_EXISTE"
    assert puente.llamadas == 1  # no se volvió a subir


def test_subida_cortada_que_no_quedo_se_reintenta(monkeypatch, tmp_path):
    puente = PuenteCortado(cortes=1, quedo=False)
    c, pdf = _cliente_con(puente, None, monkeypatch, tmp_path)
    assert c.subir_pdf(pdf, "n.pdf", "P", {}) == "NUEVO"
    assert puente.llamadas == 2

    puente = PuenteCortado(cortes=9, quedo=False)
    c, pdf = _cliente_con(puente, None, monkeypatch, tmp_path)
    with pytest.raises(ConnectionResetError):
        c.subir_pdf(pdf, "n.pdf", "P", {})
    assert puente.llamadas == 3


def test_puente_reintenta_cortes_de_red_solo_si_es_idempotente(monkeypatch):
    from carnet_flow.drive_sync import client as mod

    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    puente = mod.PuenteAppsScript("u", "c")
    respuestas = iter([ConnectionResetError(10054, "corte"), {"ok": True}])

    def enviar(cuerpo):
        r = next(respuestas)
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(puente, "_enviar", enviar)
    assert puente.llamar("eliminar", idempotente=True, file_id="F") == {"ok": True}
    monkeypatch.setattr(puente, "_enviar", lambda cuerpo: (_ for _ in ()).throw(ConnectionResetError(10054, "corte")))
    with pytest.raises(ConnectionResetError):
        puente.llamar("subir")


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
