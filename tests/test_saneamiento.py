from datetime import date

from carnet_flow.drive_sync.client import FOLDER, Item
from carnet_flow.drive_sync.folders import ResolverCarpetas
from carnet_flow.drive_sync.index import Arbol
from carnet_flow.drive_sync.saneamiento import Documento, decidir

ROOT = "root"


def _arbol(rutas):
    arbol, ids = Arbol(ROOT), {}
    for ruta in rutas:
        padre = ROOT
        for i, nombre in enumerate(ruta.split("/")):
            clave = "/".join(ruta.split("/")[: i + 1])
            if clave not in ids:
                ids[clave] = clave
                arbol.registrar_carpeta(Item(clave, nombre, FOLDER, (padre,)))
            padre = clave
    return arbol


ARBOL = [
    "J&V RESGUARDO SAC/2026/3.MARZO/05.03.2026",
    "J&V RESGUARDO SAC/2026/3.MARZO/05...03...2026",
    "J&V RESGUARDO SAC/2026/5.MAYO/15.15.2026",
    "J&V RESGUARDO SAC/2021/4.ABRIL",
]


def _doc(fid, carpeta, nombre, dni_pdf, emision, md5="m", dni_nombre=None):
    return Documento(fid, nombre, carpeta, carpeta, "JV", False, dni_nombre or nombre[:8], md5, dni_pdf, emision)


def test_decidir_fusion_fecha_real_duplicados_y_revisar():
    arbol = _arbol(ARBOL)
    r = ResolverCarpetas(arbol, None, "VENCIDOS", crear=False)
    canon = "J&V RESGUARDO SAC/2026/3.MARZO/05.03.2026"
    dup = "J&V RESGUARDO SAC/2026/3.MARZO/05...03...2026"
    docs = [
        _doc("a", dup, "11111111.pdf", "11111111", date(2026, 3, 5)),                     # fusión: mover
        _doc("b", canon, "22222222_05.03.2026.pdf", "22222222", date(2026, 3, 5), "x"),   # ya está bien
        _doc("c", dup, "22222222.pdf", "22222222", date(2026, 3, 5), "x"),                # copia idéntica
        _doc("d", "J&V RESGUARDO SAC/2026/5.MAYO/15.15.2026", "80000008.pdf", "80000008", date(2026, 5, 15)),
        _doc("e", "J&V RESGUARDO SAC/2021/4.ABRIL", "80000003.pdf", "80000003", date(2021, 4, 28)),
        _doc("f", dup, "33333333.pdf", "44444444", date(2026, 3, 5)),                     # DNI no coincide
        _doc("g", dup, "55555555.pdf", None, None),                                       # sin texto
        _doc("h", canon, "66666666.pdf", "66666666", date(2026, 3, 5)),                    # solo renombrar
    ]
    a = {x.doc.file_id: x for x in decidir(docs, r)}
    assert (a["a"].tipo, a["a"].destino_id, a["a"].nombre_nuevo) == ("MOVER", canon, "11111111_05.03.2026.pdf")
    assert a["b"].tipo == "OK"
    assert a["c"].tipo == "PAPELERA_DUPLICADO"
    assert a["d"].tipo == "MOVER" and a["d"].destino_ruta == "J&V RESGUARDO SAC/2026/5.MAYO/15.05.2026"
    assert a["e"].tipo == "MOVER" and a["e"].destino_ruta == "J&V RESGUARDO SAC/2021/4.ABRIL/28.04.2021"
    assert a["f"].tipo == "REVISAR" and "≠" in a["f"].motivo
    assert a["g"].tipo == "REVISAR"
    assert (a["h"].tipo, a["h"].nombre_nuevo) == ("RENOMBRAR", "66666666_05.03.2026.pdf")


def test_misma_credencial_regenerada_va_a_papelera_y_distinta_modalidad_se_revisa():
    arbol = _arbol(ARBOL)
    r = ResolverCarpetas(arbol, None, "VENCIDOS", crear=False)
    canon = "J&V RESGUARDO SAC/2026/3.MARZO/05.03.2026"
    base = dict(negocio="JV", vencidos=False, dni_nombre="80000007", dni_pdf="80000007", emision_pdf=date(2026, 3, 5))
    docs = [
        Documento("a", "80000007.pdf", canon, canon, md5="m1", huella="texto-A", **base),
        Documento("b", "80000007..pdf", canon, canon, md5="m2", huella="texto-A", **base),     # regenerado
        Documento("c", "80000007 - PP.pdf", canon, canon, md5="m3", huella="texto-B", **base),  # otra modalidad
    ]
    a = {x.doc.file_id: x for x in decidir(docs, r)}
    assert a["b"].tipo == "PAPELERA_DUPLICADO" and "texto idéntico" in a["b"].motivo
    assert a["c"].tipo == "REVISAR"


def test_extraer_datos_pdf_de_un_carne_real():
    from pathlib import Path

    import pytest

    from carnet_flow.drive_sync.saneamiento import extraer_datos_pdf

    muestras = sorted(Path("corridas").glob("*/staging/JV/09876502_*.pdf"))
    if not muestras:
        pytest.skip("sin PDF real descargado en corridas/")
    assert extraer_datos_pdf(muestras[0].read_bytes()) == ("09876502", date(2026, 10, 19))
