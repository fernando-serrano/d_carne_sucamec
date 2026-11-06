from datetime import date, datetime
from pathlib import Path

from carnet_flow.domain import Accion, Estado, Operacion, ResultadoConsulta
from carnet_flow.drive_sync.sync import ResultadoSync
from carnet_flow.notifications import columnas_corrida, construir_asunto, construir_cuerpo


def test_asunto_como_estados():
    assert construir_asunto("BOT CARNET-SUCAMEC", "CARNES", "JV", "20261106_120000", 1) == \
        "BOT CARNET-SUCAMEC | Resumen flujo SUCAMEC | CARNES | JV | 1 excel | 20261106_120000"


def test_cuerpo_tabla_horizontal_y_adjuntos():
    html = construir_cuerpo("CARNES", "JV", "R1", [("Encontrados", 3), ("Errores MinIO", 0)],
                            [Path("RB_GADSOCarnetSUCAMEC_R1.xlsx")], simulado=False,
                            ahora=datetime(2026, 11, 6, 12, 30, 5))
    assert "<strong>Grupo:</strong> JV" in html and "<strong>Corrida:</strong> R1" in html
    assert "06/11/2026" in html and "12:30:05 hras" in html
    assert html.index(">Encontrados</th>") < html.index(">Errores MinIO</th>")
    assert ">3</td>" in html and ">0</td>" in html
    assert "<li>RB_GADSOCarnetSUCAMEC_R1.xlsx</li>" in html
    assert "SIMULACI" not in html


def test_cuerpo_marca_simulacion():
    html = construir_cuerpo("CESES", "JV", "R1", [], [], simulado=True, ahora=datetime(2026, 1, 1))
    assert "SIMULACIÓN" in html and "Sin adjuntos disponibles" in html


def test_columnas_corrida():
    f = date(2026, 11, 6)
    resultados = {("JV", "1"): ResultadoConsulta("JV", "1", Estado.ENCONTRADO, f),
                  ("JV", "2"): ResultadoConsulta("JV", "2", Estado.NO_ENCONTRADO),
                  ("JV", "3"): ResultadoConsulta("JV", "3", Estado.ERROR)}
    sync = [ResultadoSync(Operacion(Accion.SUBIR, "JV", "1", f, ""), "HECHO", minio="HECHO"),
            ResultadoSync(Operacion(Accion.MOVER_A_VENCIDOS, "JV", "4", f, ""), "HECHO", minio="MINIO_ERROR"),
            ResultadoSync(Operacion(Accion.ELIMINAR, "JV", "5", f, ""), "SIN_PERMISO"),
            ResultadoSync(Operacion(Accion.NINGUNA, "JV", "6", f, ""), "OMITIDO")]
    assert columnas_corrida(resultados, sync) == [
        ("DNIs consultados", 3), ("Encontrados", 1), ("No encontrados", 1), ("Error consulta", 1),
        ("Carnés subidos", 1), ("Pasados a vencidos", 1), ("Eliminados", 0), ("Errores Drive", 1),
        ("Errores MinIO", 1),
    ]
