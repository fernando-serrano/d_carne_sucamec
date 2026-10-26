from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parents[2]
load_dotenv(BASE_DIR / ".env")


def bool_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "si", "sí", "on"}


def int_env(name: str, default: int) -> int:
    try:
        return int(str(os.getenv(name, default)).strip())
    except Exception:
        return default


def str_env(name: str, default: str = "") -> str:
    return str(os.getenv(name, default) or "").strip()


@dataclass(frozen=True)
class Credentials:
    tipo_documento_valor: str
    numero_documento: str
    usuario: str
    contrasena: str

    def validate(self, grupo: str) -> None:
        faltan = [n for n, v in (("numero_documento", self.numero_documento), ("usuario", self.usuario),
                                 ("contrasena", self.contrasena)) if not v]
        if faltan:
            raise ValueError(f"Faltan credenciales SUCAMEC para {grupo}: {', '.join(faltan)}")


@dataclass(frozen=True)
class Settings:
    login_url: str
    headless: bool
    ocr_max_intentos: int
    captcha_solve_timeout_ms: int
    login_captcha_retries: int
    login_validation_timeout_ms: int
    server_error_retries: int
    server_error_wait_ms: int
    intentos_por_dni: int
    descarga_timeout_ms: int
    workers_jv: int
    workers_selva: int
    max_registros: int
    padron_excel: str
    bajas_excel: str
    drive_root_id: str
    drive_sa_key: Path
    drive_oauth_token: Path
    drive_carpeta_vencidos: str
    logs_dir: Path
    runs_dir: Path
    max_corridas: int

    def workers(self, negocio: str) -> int:
        return self.workers_selva if negocio == "SELVA" else self.workers_jv


def load_settings() -> Settings:
    return Settings(
        login_url=str_env("SUCAMEC_URL_LOGIN", "https://www.sucamec.gob.pe/sel/faces/login.xhtml?faces-redirect=true"),
        headless=bool_env("CARNET_HEADLESS", True),
        ocr_max_intentos=max(1, int_env("CARNET_OCR_MAX_INTENTOS", 6)),
        captcha_solve_timeout_ms=max(5000, int_env("SUCAMEC_CAPTCHA_SOLVE_TIMEOUT_MS", 120000)),
        login_captcha_retries=max(1, int_env("SUCAMEC_LOGIN_CAPTCHA_RETRIES", 4)),
        login_validation_timeout_ms=max(1000, int_env("SUCAMEC_LOGIN_VALIDATION_TIMEOUT_MS", 12000)),
        server_error_retries=max(1, int_env("SUCAMEC_SERVER_ERROR_RETRIES", 3)),
        server_error_wait_ms=max(0, int_env("SUCAMEC_SERVER_ERROR_WAIT_MS", 4000)),
        intentos_por_dni=max(1, int_env("CARNET_INTENTOS_POR_DNI", 3)),
        descarga_timeout_ms=max(5000, int_env("CARNET_DESCARGA_TIMEOUT_MS", 30000)),
        workers_jv=max(1, int_env("CARNET_WORKERS_JV", 3)),
        workers_selva=max(1, int_env("CARNET_WORKERS_SELVA", 1)),
        max_registros=max(0, int_env("CARNET_MAX_REGISTROS", 0)),
        padron_excel=str_env("CARNET_PADRON_EXCEL", ""),
        bajas_excel=str_env("CARNET_BAJAS_EXCEL", ""),
        drive_root_id=str_env("CARNET_DRIVE_ROOT_ID"),
        drive_sa_key=BASE_DIR / str_env("CARNET_DRIVE_SA_KEY", "secrets/bots-sucamec-419d9f901d1e.json"),
        drive_oauth_token=BASE_DIR / str_env("CARNET_DRIVE_OAUTH_TOKEN", "secrets/drive_oauth_token.json"),
        drive_carpeta_vencidos=str_env("CARNET_DRIVE_CARPETA_VENCIDOS", "VENCIDOS"),
        logs_dir=BASE_DIR / "logs",
        runs_dir=BASE_DIR / "corridas",
        max_corridas=max(1, int_env("CARNET_MAX_CORRIDAS", 15)),
    )


def credentials_for(negocio: str) -> Credentials:
    if negocio == "SELVA":
        return Credentials(str_env("SELVA_TIPO_DOC", "RUC"), str_env("SELVA_NUMERO_DOCUMENTO"),
                           str_env("SELVA_USUARIO_SEL"), str_env("SELVA_CLAVE_SEL"))
    return Credentials(str_env("TIPO_DOC", "RUC"), str_env("NUMERO_DOCUMENTO"),
                       str_env("USUARIO_SEL"), str_env("CLAVE_SEL"))
