from carnet_flow.drive_sync.client import FOLDER, PDF, Item
from carnet_flow.drive_sync.index import Arbol
from carnet_flow.drive_sync.renombrado import planificar, planificar_carpetas

ROOT = "root"


def _arbol(rutas):
    arbol, pdfs, ids = Arbol(ROOT), [], {}
    for ruta, archivos in rutas.items():
        padre = ROOT
        for i, nombre in enumerate(ruta.split("/")):
            clave = "/".join(ruta.split("/")[: i + 1])
            if clave not in ids:
                ids[clave] = f"f{len(ids)}"
                arbol.registrar_carpeta(Item(ids[clave], nombre, FOLDER, (padre,)))
            padre = ids[clave]
        pdfs += [Item(f"{padre}/{a}", a, PDF, (padre,)) for a in archivos]
    return arbol, pdfs


def test_planificar_carpetas():
    arbol, _ = _arbol({
        "J&V RESGUARDO SAC/2026/3.Marzo/25..03..2026": [],
        "J&V RESGUARDO SAC/2026/1.ENERO/08.01.26": [],
        "J&V RESGUARDO SAC/2025/6Junio/09..06..25": [],
        "J&V RESGUARDO SAC/2021/10.0CTUBRE/05.10.2021": [],
        "J&V RESGUARDO SAC/2023/10. Octubre/05..10..2023": [],
        "J&V RESGUARDO SAC/2023/10. Octubre/05.10.2023": [],
        "J&V RESGUARDO SAC/2026/5.Mayo/15.15.2026": [],
        "J&V RESGUARDO SAC/DFNUEVOS": [],
        "J&V RESGUARDO SELVA SAC/2026/1.ENERO/selva.07.01.26": [],
        "J&V RESGUARDO SELVA SAC/2024/SELVA 19.02.2024": [],
        "J&V RESGUARDO SELVA SAC/2026/7.JULIO/SELVA.03.07": [],
    })
    r = {x.actual: (x.nuevo, x.estado) for x in planificar_carpetas(arbol, "VENCIDOS")}
    assert r["3.Marzo"] == ("3.MARZO", "RENOMBRAR")
    assert r["25..03..2026"] == ("25.03.2026", "RENOMBRAR")
    assert r["08.01.26"] == ("08.01.2026", "RENOMBRAR")
    assert r["6Junio"] == ("6.JUNIO", "RENOMBRAR")
    assert r["10.0CTUBRE"] == ("10.OCTUBRE", "RENOMBRAR")
    assert r["05.10.2021"] == ("05.10.2021", "YA_OK")
    assert r["05..10..2023"][1] == "COLISION"          # su hermana 05.10.2023 ya tiene ese nombre
    assert r["15.15.2026"][1] == "SIN_FECHA"
    assert r["selva.07.01.26"] == ("SELVA.07.01.2026", "RENOMBRAR")
    assert r["SELVA 19.02.2024"] == ("SELVA.19.02.2024", "RENOMBRAR")
    assert r["SELVA.03.07"] == ("SELVA.03.07.2026", "RENOMBRAR")
    assert "DFNUEVOS" not in r and "2026" not in r     # fuera del nivel año/mes/día no se toca


def test_planificar_renombrado():
    arbol, pdfs = _arbol({
        "J&V RESGUARDO SAC/2023/10. Octubre/05..10..2023": ["80000004.pdf", "80000007..pdf"],
        "J&V RESGUARDO SAC/2026/10.OCTUBRE/06.10.2026": ["80000004_06.10.2026.pdf"],
        "J&V RESGUARDO SAC/2021/4. ABRIL": ["80000002.pdf", "80000001 - VIGILANCIA PRIVADA.pdf"],
        "J&V RESGUARDO SAC/DFNUEVOS": ["80000005.pdf"],
        "J&V RESGUARDO SAC/2021/7. JULIO/05.07.2021": ["carneGssp.pdf"],
        "J&V RESGUARDO SELVA SAC/2024/SELVA.01.08.2024": ["00987600.pdf",
                                                         "00987600 - A.pdf", "00987600 - B.pdf"],
        "VENCIDOS/J&V RESGUARDO SAC/2020/1.ENERO/02.01.2020": ["11111111.pdf"],
    })
    r = {(x.ruta.split("/")[-1], x.actual): (x.nuevo, x.estado) for x in planificar(arbol, pdfs, "VENCIDOS")}

    assert r[("05..10..2023", "80000004.pdf")] == ("80000004_05.10.2023.pdf", "RENOMBRAR")
    assert r[("05..10..2023", "80000007..pdf")] == ("80000007_05.10.2023.pdf", "RENOMBRAR")
    assert r[("06.10.2026", "80000004_06.10.2026.pdf")] == ("80000004_06.10.2026.pdf", "YA_OK")
    assert r[("4. ABRIL", "80000002.pdf")][1] == "FECHA_APROXIMADA"
    assert r[("DFNUEVOS", "80000005.pdf")][1] == "SIN_FECHA"
    assert r[("05.07.2021", "carneGssp.pdf")][1] == "NO_RECONOCIDO"
    assert r[("02.01.2020", "11111111.pdf")] == ("11111111_02.01.2020.pdf", "RENOMBRAR")
    # tres PDFs del mismo DNI en la misma carpeta terminarían con el mismo nombre
    assert {r[("SELVA.01.08.2024", n)][1] for n in ("00987600.pdf", "00987600 - A.pdf", "00987600 - B.pdf")} == {"COLISION"}
