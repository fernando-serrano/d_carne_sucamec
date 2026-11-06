import logging
from datetime import date
from pathlib import Path

from carnet_flow.domain import Accion, Operacion
from carnet_flow.drive_sync.folders import ResolverCarpetas
from carnet_flow.drive_sync.sync import aplicar
from carnet_flow.minio_sync.espejo import replicar
from carnet_flow.minio_sync.store import AlmacenMemoria
from test_drive_y_fuentes import ESTRUCTURA, DriveFalso, _arbol

LOG = logging.getLogger("test")
P = "sucamec/carnets"
ACTIVA = "sucamec/carnets/JV/2021/10/05/80000011_05.10.2021.pdf"
VENCIDA = "sucamec/carnets/vencidos/JV/2021/10/05/80000011_05.10.2021.pdf"


def _sin_descarga(fid):
    raise AssertionError("no debía descargar")


def test_subir_activo(tmp_path: Path):
    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"PDF")
    a = AlmacenMemoria()
    op = Operacion(Accion.SUBIR, "JV", "12345678", date(2026, 11, 6), "nuevo", pdf=str(pdf))
    assert replicar(op, a, P, _sin_descarga) == "HECHO"
    data, meta = a.objetos["sucamec/carnets/JV/2026/11/06/12345678_06.11.2026.pdf"]
    assert data == b"PDF" and meta["dni"] == "12345678" and meta["fecha-emision"] == "2026-11-06"


def test_subir_a_vencidos(tmp_path: Path):
    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"V")
    a = AlmacenMemoria()
    replicar(Operacion(Accion.SUBIR_A_VENCIDOS, "JV", "80000011", date(2021, 10, 5), "x", pdf=str(pdf)),
             a, P, _sin_descarga)
    assert set(a.objetos) == {VENCIDA}


def test_mover_con_activo_existente():
    a = AlmacenMemoria()
    a.subir(ACTIVA, b"X", {"dni": "80000011"})
    op = Operacion(Accion.MOVER_A_VENCIDOS, "JV", "80000011", date(2021, 10, 5), "vencido", file_id="F1")
    assert replicar(op, a, P, _sin_descarga) == "HECHO"
    assert set(a.objetos) == {VENCIDA} and a.objetos[VENCIDA][0] == b"X"


def test_mover_sin_activo_descarga_drive():
    a = AlmacenMemoria()
    op = Operacion(Accion.MOVER_A_VENCIDOS, "JV", "80000011", date(2021, 10, 5), "vencido", file_id="F1")
    assert replicar(op, a, P, lambda fid: b"PDF") == "HECHO"
    assert a.objetos[VENCIDA][0] == b"PDF"


def test_eliminar_idempotente():
    a = AlmacenMemoria()
    op = Operacion(Accion.ELIMINAR, "JV", "80000011", date(2021, 10, 5), "baja", file_id="F2")
    assert replicar(op, a, P, _sin_descarga) == "HECHO"


def test_eliminar_vencido_borra_rama_vencidos():
    a = AlmacenMemoria()
    a.subir(VENCIDA, b"X", {})
    a.subir(ACTIVA, b"Y", {})
    op = Operacion(Accion.ELIMINAR, "JV", "80000011", date(2021, 10, 5), "cese", file_id="F", vencido=True)
    replicar(op, a, P, _sin_descarga)
    assert set(a.objetos) == {ACTIVA}


def test_sin_fecha_omitido():
    op = Operacion(Accion.MOVER_A_VENCIDOS, "JV", "80000005", None, "sin fecha", file_id="F")
    assert replicar(op, AlmacenMemoria(), P, _sin_descarga) == "OMITIDO"


class _AlmacenRoto(AlmacenMemoria):
    def subir(self, *a, **k):
        raise ConnectionError("minio caido")


def test_aplicar_minio_falla_drive_hecho():
    arbol, _ = _arbol(ESTRUCTURA)
    drive = DriveFalso()
    drive.descargar = lambda fid: b"PDF"
    op = Operacion(Accion.MOVER_A_VENCIDOS, "JV", "80000011", date(2021, 10, 5), "vencido", file_id="F1")
    res = aplicar([op], drive, ResolverCarpetas(arbol, drive, "VENCIDOS", crear=True), True, LOG,
                  almacen=_AlmacenRoto(), prefijo_minio=P)
    assert res[0].estado == "HECHO" and res[0].minio == "MINIO_ERROR"


def test_aplicar_simulacion_no_escribe_minio():
    arbol, _ = _arbol(ESTRUCTURA)
    drive, a = DriveFalso(), AlmacenMemoria()
    op = Operacion(Accion.MOVER_A_VENCIDOS, "JV", "80000011", date(2021, 10, 5), "vencido", file_id="F1")
    res = aplicar([op], drive, ResolverCarpetas(arbol, drive, "VENCIDOS", crear=False), False, LOG,
                  almacen=a, prefijo_minio=P)
    assert a.objetos == {} and res[0].minio == "SIMULADO"


def test_reanudar_replica_minio_pendiente(tmp_path: Path):
    """Drive ya hecho en una ejecución previa, MinIO no: se replica solo MinIO."""
    arbol, _ = _arbol(ESTRUCTURA)
    op = Operacion(Accion.MOVER_A_VENCIDOS, "JV", "80000011", date(2021, 10, 5), "vencido", file_id="F1")
    reg_drive, reg_minio = tmp_path / "drive_hechas.txt", tmp_path / "minio_hechas.txt"
    d1 = DriveFalso()
    aplicar([op], d1, ResolverCarpetas(arbol, d1, "VENCIDOS", crear=True), True, LOG, reg_drive)  # sin MinIO
    d2, a = DriveFalso(), AlmacenMemoria()
    d2.descargar = lambda fid: b"PDF"
    res = aplicar([op], d2, ResolverCarpetas(arbol, d2, "VENCIDOS", crear=True), True, LOG, reg_drive,
                  almacen=a, prefijo_minio=P, registro_minio=reg_minio)
    assert not any(c[0] == "mover" for c in d2.llamadas)
    assert res[0].minio == "HECHO" and VENCIDA in a.objetos
    # Tercera vez: nada que hacer en ninguno de los dos.
    a2 = AlmacenMemoria()
    res = aplicar([op], d2, ResolverCarpetas(arbol, d2, "VENCIDOS", crear=True), True, LOG, reg_drive,
                  almacen=a2, prefijo_minio=P, registro_minio=reg_minio)
    assert a2.objetos == {} and res[0].minio == "HECHO"
