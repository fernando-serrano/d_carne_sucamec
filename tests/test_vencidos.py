from datetime import date

from carnet_flow.domain import CarneDrive, IndiceDrive
from carnet_flow.drive_sync.vencidos import planificar_vencidos

HOY = date(2026, 10, 23)


def _c(fid, dni, fecha, aproximada=False):
    return CarneDrive("JV", dni, fid, "ruta", fecha, aproximada)


def test_renovados_y_vencidos_van_a_vencidos_y_vigente_se_queda():
    indice = IndiceDrive()
    for c in [
        _c("a", "1", date(2020, 5, 1)),     # renovado
        _c("b", "1", date(2024, 5, 1)),     # vigente
        _c("c", "2", date(2023, 10, 22)),   # cumplió 3 años
        _c("d", "3", date(2023, 10, 24)),   # aún vigente
        _c("e", "4", None),                 # sin fecha
        _c("f", "5", date(2022, 3, 1), aproximada=True),
    ]:
        indice.agregar(c)
    plan = {s.carne.file_id: s for s in planificar_vencidos(indice, HOY)}
    assert plan["a"].tipo == "MOVER" and "renovado" in plan["a"].motivo
    assert plan["c"].tipo == "MOVER" and "3 años" in plan["c"].motivo
    assert "b" not in plan and "d" not in plan
    assert plan["e"].tipo == "REVISAR" and plan["f"].tipo == "REVISAR"


def test_dos_modalidades_misma_fecha_vigente_se_quedan_ambas():
    indice = IndiceDrive()
    indice.agregar(_c("a", "1", date(2025, 1, 1)))
    indice.agregar(_c("b", "1", date(2025, 1, 1)))
    assert planificar_vencidos(indice, HOY) == []
