from dataclasses import replace
from datetime import date

from carnet_flow.config import load_settings
from carnet_flow.minio_sync.claves import clave_minio, metadatos


def test_clave_activa():
    assert clave_minio("sucamec/carnets", "JV", "12345678", date(2026, 11, 6), False) == \
        "sucamec/carnets/JV/2026/11/06/12345678_06.11.2026.pdf"


def test_clave_vencida_selva():
    assert clave_minio("sucamec/carnets/", "SELVA", "00123456", date(2023, 1, 9), True) == \
        "sucamec/carnets/vencidos/SELVA/2023/01/09/00123456_09.01.2023.pdf"


def test_metadatos():
    assert metadatos("JV", "12345678", date(2026, 11, 6)) == {
        "dni": "12345678", "negocio": "JV", "fecha-emision": "2026-11-06", "origen": "carnet_sucamec_gadso"}


def test_minio_activo_requiere_flag_y_credenciales():
    base = replace(load_settings(), minio_enabled=True, minio_endpoint="s3.x", minio_access_key="a",
                   minio_secret_key="b")
    assert base.minio_activo
    assert not replace(base, minio_enabled=False).minio_activo
    assert not replace(base, minio_secret_key="").minio_activo
