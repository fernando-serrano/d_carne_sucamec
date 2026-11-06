import logging
from dataclasses import replace

from carnet_flow.config import load_settings
from carnet_flow.minio_sync.store import AlmacenMemoria, crear_almacen


def test_subir_y_listar():
    a = AlmacenMemoria()
    a.subir("p/JV/x.pdf", b"PDF", {"dni": "1"})
    assert a.listar("p/") == {"p/JV/x.pdf"}
    assert a.objetos["p/JV/x.pdf"] == (b"PDF", {"dni": "1"})


def test_copiar_conserva_bytes_y_meta():
    a = AlmacenMemoria()
    a.subir("p/a.pdf", b"X", {"dni": "1"})
    a.copiar("p/a.pdf", "p/vencidos/a.pdf")
    assert a.objetos["p/vencidos/a.pdf"] == (b"X", {"dni": "1"})
    assert "p/a.pdf" in a.objetos


def test_eliminar_inexistente_no_lanza():
    AlmacenMemoria().eliminar("p/nada.pdf")


def test_listar_respeta_prefijo():
    a = AlmacenMemoria()
    a.subir("a/1.pdf", b"", {})
    a.subir("b/2.pdf", b"", {})
    assert a.listar("a/") == {"a/1.pdf"}


def test_crear_almacen_desactivado():
    assert crear_almacen(replace(load_settings(), minio_enabled=False), logging.getLogger("t")) is None
