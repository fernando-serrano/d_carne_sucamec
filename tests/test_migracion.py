from carnet_flow.drive_sync.migracion import PASOS, inversas, pasos_a_deshacer

P = {p.clave: p for p in PASOS}


def test_saneamiento_deshace_en_orden_inverso_y_sin_mover_si_misma_carpeta():
    entradas = [
        {"id": "a", "tipo": "MOVER", "carpeta_anterior": "c1", "carpeta_nueva": "c2",
         "nombre_anterior": "1.pdf", "nombre_nuevo": "1_01.01.2026.pdf"},
        {"id": "b", "tipo": "RENOMBRAR", "carpeta_anterior": "c1", "carpeta_nueva": "c1",
         "nombre_anterior": "2.pdf", "nombre_nuevo": "2_01.01.2026.pdf"},
        {"id": "c", "tipo": "PAPELERA_DUPLICADO", "carpeta_anterior": "c1", "carpeta_nueva": None,
         "nombre_anterior": "3.pdf", "nombre_nuevo": None},
    ]
    inv = inversas(P["saneamiento"], entradas)
    assert [i.file_id for i in inv] == ["c", "b", "a"]
    assert inv[0].restaurar
    assert (inv[1].nombre, inv[1].agregar, inv[1].quitar) == ("2.pdf", None, None)
    assert (inv[2].agregar, inv[2].quitar) == ("c1", "c2")


def test_vencidos_y_correcciones():
    v = inversas(P["vencidos"], [{"id": "a", "origen": "o", "destino": "d"}])[0]
    assert (v.agregar, v.quitar, v.nombre) == ("o", "d", None)
    c = inversas(P["correcciones"], [{"id": "a", "accion": "renombrar_mover", "nombre_anterior": "x.pdf",
                                      "carpeta_anterior": ["o"], "carpeta_nueva": "d"}])[0]
    assert (c.nombre, c.agregar, c.quitar) == ("x.pdf", "o", "d")


def test_restaurar_hasta_un_paso_deshace_solo_los_posteriores_en_orden_inverso():
    assert [p.numero for p in pasos_a_deshacer(3)] == [6, 5, 4]
    assert [p.numero for p in pasos_a_deshacer(0)] == [6, 5, 4, 3, 2, 1]
