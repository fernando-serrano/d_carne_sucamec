import logging
from datetime import date

from carnet_flow.domain import CarneDrive, IndiceDrive
from carnet_flow.minio_sync.reconciliar import OpMinio, aplicar_reconciliacion, planificar_reconciliacion
from carnet_flow.minio_sync.store import AlmacenMemoria

P = "sucamec/carnets"
A = "sucamec/carnets/JV/2026/11/06/11111111_06.11.2026.pdf"
V = "sucamec/carnets/vencidos/JV/2020/01/02/22222222_02.01.2020.pdf"


def _idx(*carnes):
    i = IndiceDrive()
    for c in carnes:
        i.agregar(c)
    return i


ACT = CarneDrive("JV", "11111111", "FA", "r", date(2026, 11, 6))
VEN = CarneDrive("JV", "22222222", "FV", "r", date(2020, 1, 2))


def _claves(ops, accion):
    return sorted(o.clave for o in ops if o.accion == accion)


def test_migracion_inicial():
    ops, sin_fecha = planificar_reconciliacion(_idx(ACT), _idx(VEN), set(), P)
    assert _claves(ops, "SUBIR") == sorted([A, V]) and _claves(ops, "ELIMINAR") == [] and sin_fecha == []
    assert next(o for o in ops if o.clave == A).file_id == "FA"


def test_idempotente():
    ops, _ = planificar_reconciliacion(_idx(ACT), _idx(VEN), {A, V}, P)
    assert ops == []


def test_mal_ubicado():
    mal = "sucamec/carnets/vencidos/JV/2026/11/06/11111111_06.11.2026.pdf"
    ops, _ = planificar_reconciliacion(_idx(ACT), _idx(), {mal}, P)
    assert _claves(ops, "SUBIR") == [A] and _claves(ops, "ELIMINAR") == [mal]


def test_huerfano():
    ops, _ = planificar_reconciliacion(_idx(), _idx(), {A}, P)
    assert _claves(ops, "ELIMINAR") == [A]


def test_no_toca_fuera_de_prefijo():
    ops, _ = planificar_reconciliacion(_idx(), _idx(), {"sucamec/tramites/x.pdf", "sucamec/carnetsX/y.pdf"}, P)
    assert ops == []


def test_duplicado_misma_clave():
    dup = CarneDrive("JV", "11111111", "FB", "r2", date(2026, 11, 6))
    ops, _ = planificar_reconciliacion(_idx(ACT, dup), _idx(), set(), P)
    assert _claves(ops, "SUBIR") == [A]


def test_sin_fecha():
    sf = CarneDrive("JV", "33333333", "FS", "DFNUEVOS", None)
    ops, sin_fecha = planificar_reconciliacion(_idx(sf), _idx(), set(), P)
    assert ops == [] and sin_fecha == [sf]


def test_filtrar_por_negocio_protege_al_otro():
    from carnet_flow.minio_sync.reconciliar import filtrar_por_negocio

    selva = "sucamec/carnets/SELVA/2026/11/06/9_06.11.2026.pdf"
    selva_v = "sucamec/carnets/vencidos/SELVA/2020/01/02/9_02.01.2020.pdf"
    assert filtrar_por_negocio({A, V, selva, selva_v}, P, ("JV",)) == {A, V}
    ops, _ = planificar_reconciliacion(_idx(ACT), _idx(VEN), filtrar_por_negocio({A, V, selva}, P, ("JV",)), P)
    assert ops == []


def test_aplicar_sube_y_elimina():
    a = AlmacenMemoria()
    a.subir("sucamec/carnets/JV/viejo.pdf", b"", {})
    ops = [OpMinio("SUBIR", A, "FA", "JV", "11111111", date(2026, 11, 6)),
           OpMinio("ELIMINAR", "sucamec/carnets/JV/viejo.pdf")]
    res = aplicar_reconciliacion(ops, a, lambda fid: b"PDF-" + fid.encode(), True, logging.getLogger("t"), 2)
    assert [e for _, e in res] == ["HECHO", "HECHO"]
    assert set(a.objetos) == {A} and a.objetos[A][0] == b"PDF-FA" and a.objetos[A][1]["dni"] == "11111111"


def test_aplicar_simulado_no_escribe():
    a = AlmacenMemoria()
    res = aplicar_reconciliacion([OpMinio("SUBIR", A, "FA", "JV", "11111111", date(2026, 11, 6))], a,
                                 lambda f: b"", False, logging.getLogger("t"), 1)
    assert a.objetos == {} and res[0][1] == "SIMULADO"
