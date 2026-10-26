from datetime import date

import pytest

from carnet_flow.domain import (
    Accion,
    CarneDrive,
    Estado,
    IndiceDrive,
    ItemPlan,
    Motivo,
    ResultadoConsulta,
    construir_plan,
    decidir_sync,
    esta_vencido,
    fecha_desde_ruta,
    negocio_desde_texto,
    normalizar_dni,
    parse_emision,
    parse_fecha_carpeta,
    parse_mes_carpeta,
)

HOY = date(2026, 10, 21)


@pytest.mark.parametrize(
    "valor, esperado",
    [
        ("07654313", "07654313"),
        (7654313, "07654313"),
        (7654313.0, "07654313"),
        ("7654313.0", "07654313"),
        (" 80000009 ", "80000009"),
        ("006474488", "006474488"),
        ("ABC", None),
        ("", None),
        (None, None),
        ("1234567890", None),
    ],
)
def test_normalizar_dni(valor, esperado):
    assert normalizar_dni(valor) == esperado


@pytest.mark.parametrize(
    "texto, esperado",
    [
        ("J&V RESGUARDO SAC", "JV"),
        ("J & V RESGUARDO", "JV"),
        ("  j & v   resguardo ", "JV"),
        ("J&V RESGUARDO S.A.C.", "JV"),
        ("J Y V RESGUARDO", "JV"),
        ("J.V. RESGUARDO", "JV"),
        ("JV", "JV"),
        ("jv", "JV"),
        ("20100901481", "JV"),
        (20100901481, "JV"),
        ("J&V RESGUARDO SELVA SAC", "SELVA"),
        ("J V SELVA", "SELVA"),
        ("J&V SELVA", "SELVA"),
        ("JV-SELVA", "SELVA"),
        ("Selva", "SELVA"),
        ("SELVA S.A.C.", "SELVA"),
        ("OTRA EMPRESA", None),
        ("JAVIER VILLANUEVA", None),
        ("", None),
        (None, None),
    ],
)
def test_negocio_desde_texto(texto, esperado):
    assert negocio_desde_texto(texto) == esperado


@pytest.mark.parametrize(
    "nombre, anio_padre, esperado",
    [
        ("01.10.2021", None, date(2021, 10, 1)),
        ("05..10..2023", None, date(2023, 10, 5)),
        ("08....11....2023", None, date(2023, 11, 8)),
        ("01.10.25", None, date(2025, 10, 1)),
        ("19.11..25", None, date(2025, 11, 19)),
        ("SELVA.02.10.2023", None, date(2023, 10, 2)),
        ("selva.07.01.26", None, date(2026, 1, 7)),
        ("SELVA 19.02.2024", None, date(2024, 2, 19)),
        ("04.01.2023", 2024, date(2024, 1, 4)),  # error de tipeo: manda el año padre
        ("SELVA.03.07", 2026, date(2026, 7, 3)),
        ("selva.28.08", 2025, date(2025, 8, 28)),
        ("SELVA.25.08", None, None),        # sin año padre no se inventa
        ("21.082021", None, date(2021, 8, 21)),
        ("12/09/2022", None, date(2022, 9, 12)),
        ("11..8.2026", None, date(2026, 8, 11)),
        ("15.15.2026", 2026, None),         # mes imposible: no se adivina
        ("31.02.2024", None, None),
        ("10.Octubre", None, None),
        ("DFNUEVOS", None, None),
    ],
)
def test_parse_fecha_carpeta(nombre, anio_padre, esperado):
    assert parse_fecha_carpeta(nombre, anio_padre) == esperado


@pytest.mark.parametrize(
    "nombre, esperado",
    [("10.Octubre", 10), ("1. ENERO", 1), ("6Junio", 6), ("01.Enero", 1), ("10.0CTUBRE", 10), ("13.X", None), ("2021", None)],
)
def test_parse_mes_carpeta(nombre, esperado):
    assert parse_mes_carpeta(nombre) == esperado


def test_fecha_desde_ruta():
    assert fecha_desde_ruta(["2021", "10.0CTUBRE", "01.10.2021"]) == (date(2021, 10, 1), False)
    assert fecha_desde_ruta(["2023", "11. Noviembre", "06.11.2023", "24..11..2023"]) == (date(2023, 11, 24), False)
    assert fecha_desde_ruta(["2021", "1. ENERO"]) == (date(2021, 1, 1), True)
    assert fecha_desde_ruta(["2024", "SELVA.01.08.2024"]) == (date(2024, 8, 1), False)
    assert fecha_desde_ruta(["DFNUEVOS"]) == (None, False)


def test_parse_emision():
    celda = 'Vigencia carné\n"EMISIÓN: "\n19/02/2025\n'
    assert parse_emision(celda.replace('"', "").replace("\n", " ")) == date(2025, 2, 19)
    assert parse_emision("EMISION: 1/2/2024 VENCE: 1/2/2027") == date(2024, 2, 1)
    assert parse_emision("sin fecha") is None


def test_esta_vencido_aniversario_exacto_y_bisiesto():
    assert esta_vencido(date(2023, 10, 21), HOY)
    assert not esta_vencido(date(2023, 10, 22), HOY)
    assert esta_vencido(date(2024, 2, 29), date(2027, 2, 28))
    assert not esta_vencido(date(2024, 2, 29), date(2027, 2, 27))


def _carne(dni, fecha, negocio="JV", fid=None, ruta="x"):
    return CarneDrive(negocio, dni, fid or f"id-{dni}-{fecha}", ruta, fecha)


def _indice(*carnes):
    idx = IndiceDrive()
    for c in carnes:
        idx.agregar(c)
    return idx


def test_plan_clasifica_y_excluye_bajas():
    idx = _indice(
        _carne("11111111", date(2025, 1, 1)),                 # vigente
        _carne("22222222", date(2022, 1, 1)),                 # vencido
        _carne("33333333", None),                             # sin fecha
        _carne("44444444", date(2025, 5, 5)),                 # baja con PDF
        _carne("55555555", date(2020, 1, 1)),                 # duplicado anterior
        _carne("55555555", date(2024, 1, 1)),
    )
    padron = [(d, "JV") for d in ("11111111", "22222222", "33333333", "44444444", "55555555", "66666666", "11111111")]
    plan = construir_plan(padron, [("44444444", "JV")], idx, HOY)

    motivos = {i.dni: i.motivo for i in plan.items}
    assert motivos == {
        "11111111": Motivo.VIGENTE,
        "22222222": Motivo.VENCIDO,
        "33333333": Motivo.SIN_FECHA,
        "44444444": Motivo.BAJA,
        "55555555": Motivo.VIGENTE,
        "66666666": Motivo.NUEVO,
    }
    assert [c.dni for c in plan.eliminar] == ["44444444"]
    assert [c.fecha_emision for c in plan.duplicados] == [date(2020, 1, 1)]
    # todo el padrón se consulta (renovación anticipada), salvo las bajas
    assert {i.dni for i in plan.consultas()} == {"11111111", "22222222", "33333333", "55555555", "66666666"}
    assert {i.dni for i in plan.consultas(omitir_vigentes=True)} == {"22222222", "33333333", "66666666"}


def test_plan_baja_sin_negocio_aplica_a_ambos():
    idx = _indice(_carne("77777777", date(2025, 1, 1), "JV"), _carne("77777777", date(2025, 1, 1), "SELVA"))
    plan = construir_plan([("77777777", "JV")], [("77777777", None)], idx, HOY)
    assert len(plan.eliminar) == 2
    assert not plan.consultas()


def _res(fecha, estado=Estado.ENCONTRADO):
    return ResultadoConsulta("JV", "1", estado, fecha, "x.pdf" if fecha else None)


def test_sync_nuevo():
    item = ItemPlan("JV", "1", Motivo.NUEVO)
    assert [o.accion for o in decidir_sync(item, _res(date(2025, 1, 1)), HOY)] == [Accion.SUBIR]
    assert [o.accion for o in decidir_sync(item, _res(date(2022, 1, 1)), HOY)] == [Accion.SUBIR_A_VENCIDOS]
    assert [o.accion for o in decidir_sync(item, _res(None, Estado.NO_ENCONTRADO), HOY)] == [Accion.NINGUNA]


def test_sync_renovado_mueve_anterior_y_sube_nuevo():
    item = ItemPlan("JV", "1", Motivo.VENCIDO, _carne("1", date(2022, 1, 1)))
    ops = decidir_sync(item, _res(date(2025, 3, 1)), HOY)
    assert [o.accion for o in ops] == [Accion.MOVER_A_VENCIDOS, Accion.SUBIR]
    assert ops[0].file_id == item.actual.file_id


def test_sync_vencido_sin_renovacion():
    item = ItemPlan("JV", "1", Motivo.VENCIDO, _carne("1", date(2022, 1, 1)))
    assert [o.accion for o in decidir_sync(item, _res(date(2022, 1, 1)), HOY)] == [Accion.MOVER_A_VENCIDOS]
    assert [o.accion for o in decidir_sync(item, _res(None, Estado.NO_ENCONTRADO), HOY)] == [Accion.MOVER_A_VENCIDOS]
    # un error de consulta no mueve nada: se reintenta en la siguiente corrida
    assert [o.accion for o in decidir_sync(item, _res(None, Estado.ERROR), HOY)] == [Accion.NINGUNA]


def test_sync_sin_fecha_reemplaza():
    item = ItemPlan("JV", "1", Motivo.SIN_FECHA, _carne("1", None, ruta="DFNUEVOS"))
    assert [o.accion for o in decidir_sync(item, _res(date(2025, 3, 1)), HOY)] == [Accion.MOVER_A_VENCIDOS, Accion.SUBIR]


def test_sync_renovacion_anticipada_antes_de_3_anios():
    # Drive 23/10/2023 (vence 23/10/2026); SUCAMEC ya emitió el 06/10/2026
    item = ItemPlan("JV", "80000004", Motivo.VIGENTE, _carne("80000004", date(2023, 10, 23)))
    ops = decidir_sync(item, _res(date(2026, 10, 6)), date(2026, 10, 21))
    assert [o.accion for o in ops] == [Accion.MOVER_A_VENCIDOS, Accion.SUBIR]


def test_sync_vigente_no_encontrado_no_se_toca():
    item = ItemPlan("JV", "1", Motivo.VIGENTE, _carne("1", date(2025, 1, 1)))
    assert [o.accion for o in decidir_sync(item, _res(None, Estado.NO_ENCONTRADO), HOY)] == [Accion.NINGUNA]


def test_sync_idempotente_misma_fecha_vigente():
    item = ItemPlan("JV", "1", Motivo.VIGENTE, _carne("1", date(2025, 1, 1)))
    assert [o.accion for o in decidir_sync(item, _res(date(2025, 1, 1)), HOY)] == [Accion.NINGUNA]
