from datetime import date

from carnet_flow.domain import Accion, CarneDrive, IndiceDrive, operaciones_ceses


def _idx(*carnes):
    i = IndiceDrive()
    for c in carnes:
        i.agregar(c)
    return i


def test_cese_borra_activos_y_vencidos():
    act = CarneDrive("JV", "11111111", "FA", "r", date(2025, 3, 1))
    ven = CarneDrive("JV", "11111111", "FV", "r", date(2021, 3, 1))
    ops, sin_pdf = operaciones_ceses([("11111111", "JV")], _idx(act), _idx(ven))
    assert sorted((o.file_id, o.vencido) for o in ops) == [("FA", False), ("FV", True)]
    assert all(o.accion is Accion.ELIMINAR and o.motivo == "cese" for o in ops)
    assert {o.fecha_emision for o in ops} == {date(2025, 3, 1), date(2021, 3, 1)}
    assert sin_pdf == []


def test_cese_sin_negocio_aplica_a_ambos():
    jv = CarneDrive("JV", "22222222", "F1", "r", date(2025, 1, 1))
    selva = CarneDrive("SELVA", "22222222", "F2", "r", date(2025, 1, 1))
    ops, _ = operaciones_ceses([("22222222", None)], _idx(jv, selva), _idx())
    assert sorted(o.negocio for o in ops) == ["JV", "SELVA"]


def test_cese_respeta_negocio_indicado():
    jv = CarneDrive("JV", "22222222", "F1", "r", date(2025, 1, 1))
    selva = CarneDrive("SELVA", "22222222", "F2", "r", date(2025, 1, 1))
    ops, _ = operaciones_ceses([("22222222", "SELVA")], _idx(jv, selva), _idx())
    assert [o.file_id for o in ops] == ["F2"]


def test_cese_dni_inexistente():
    ops, sin_pdf = operaciones_ceses([("99999999", "JV")], _idx(), _idx())
    assert ops == [] and sin_pdf == [("99999999", "JV")]


def test_ceses_abortan_si_minio_activo_no_conecta(monkeypatch, tmp_path):
    """Un cese no puede dejar copias: si MinIO está activado y no conecta, no se toca Drive."""
    from dataclasses import replace

    from openpyxl import Workbook

    from carnet_flow.config import load_settings
    from carnet_flow.orchestration_flow import runner

    xlsx = tmp_path / "ceses.xlsx"
    wb = Workbook()
    wb.active.append(["DNI", "RAZON SOCIAL"])
    wb.active.append(["11111111", "J&V RESGUARDO SAC"])
    wb.save(xlsx)
    settings = replace(load_settings(), runs_dir=tmp_path / "corridas", minio_enabled=True, minio_endpoint="x",
                       minio_access_key="a", minio_secret_key="b")
    (tmp_path / "corridas").mkdir()

    def _no_conecta(*a, **k):
        raise ConnectionError("minio caido")

    def _no_debe_tocar_drive(*a, **k):
        raise AssertionError("no debía tocar Drive")

    monkeypatch.setattr(runner, "crear_almacen", _no_conecta)
    monkeypatch.setattr(runner, "_cargar_drive", _no_debe_tocar_drive)
    assert runner.ejecutar_ceses(settings, runner.Opciones(negocios=("JV",), ceses=str(xlsx), aplicar=True)) == 4


def test_cese_duplicado_en_excel_no_duplica_ops():
    act = CarneDrive("JV", "11111111", "FA", "r", date(2025, 3, 1))
    ops, _ = operaciones_ceses([("11111111", "JV"), ("11111111", None)], _idx(act), _idx())
    assert [o.file_id for o in ops] == ["FA"]
