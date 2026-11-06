"""Cliente mínimo de Google Drive.

Credenciales:
- Token OAuth del dueño de la carpeta (secrets/drive_oauth_token.json) si existe:
  permite subir y eliminar.
- Si no, la cuenta de servicio: lee, crea carpetas y mueve, pero NO puede subir
  (cuota 0 en una Mi unidad ajena) ni eliminar (no es dueña).
"""

from __future__ import annotations

import base64
import http.client
import json
import logging
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

from ..config import str_env

FOLDER = "application/vnd.google-apps.folder"
PDF = "application/pdf"
SCOPES = ["https://www.googleapis.com/auth/drive"]


class SinPermisoDrive(RuntimeError):
    """La credencial activa no puede hacer la operación (cuota o propiedad)."""


@dataclass(frozen=True)
class Item:
    id: str
    name: str
    mime: str
    parents: tuple[str, ...]


def _credenciales(sa_key: Path, oauth_token: Path):
    if oauth_token.exists():
        from google.oauth2.credentials import Credentials

        return Credentials.from_authorized_user_file(str(oauth_token), SCOPES), "oauth"
    from google.oauth2 import service_account

    return service_account.Credentials.from_service_account_file(str(sa_key), scopes=SCOPES), "cuenta_servicio"


def _es_falta_de_permiso(exc: HttpError) -> bool:
    texto = str(exc)
    return exc.resp.status in (403, 404) and any(
        marca in texto for marca in ("storageQuotaExceeded", "insufficientFilePermissions", "do not have storage quota",
                                     "cannotDeleteFile", "insufficientPermissions", "forbidden")
    )


# Límites de ritmo de Apps Script/Drive: la operación NO se hizo, se puede reintentar sin duplicar.
MARCAS_LIMITE = ("too many times", "ratemax", "drivewritevolume", "rate limit", "demasiadas veces",
                 "simultaneous invocations", "invocaciones simultáneas")
# Corte de red: NO se sabe si la operación se hizo (p. ej. la subida quedó en Drive pero la respuesta se perdió).
ERRORES_RED = (urllib.error.URLError, ConnectionError, http.client.HTTPException, TimeoutError)


class PuenteAppsScript:
    """Subir y eliminar como el dueño, vía la aplicación web de apps_script/Code.gs."""

    def __init__(self, url: str, clave: str, reintentos: int = 5):
        self.url, self.clave, self.reintentos = url, clave, reintentos
        self.reintentos_por_limite = 0  # métricas para la prueba de carga
        self.reintentos_por_red = 0

    def _enviar(self, cuerpo: bytes) -> dict:
        req = urllib.request.Request(self.url, data=cuerpo, headers={"Content-Type": "application/json"}, method="POST")
        # Apps Script responde con un 302 a googleusercontent.com; urllib lo sigue como GET.
        with urllib.request.urlopen(req, timeout=120) as resp:
            texto = resp.read().decode()
        try:
            return json.loads(texto or "{}")
        except json.JSONDecodeError:  # página HTML de error (p. ej. límite de ejecuciones simultáneas)
            return {"ok": False, "error": texto[:300]}

    def llamar(self, accion: str, idempotente: bool = False, **datos) -> dict:
        """`idempotente`: repetirla no cambia el resultado (papelera, ping), así que se reintenta ante cortes de red."""
        cuerpo = json.dumps({"clave": self.clave, "accion": accion, **datos}).encode()
        for intento in range(1, self.reintentos + 1):
            try:
                out = self._enviar(cuerpo)
            except urllib.error.HTTPError as exc:
                if exc.code != 429:
                    if not (idempotente and exc.code >= 500 and intento < self.reintentos):
                        raise
                    self.reintentos_por_red += 1
                    time.sleep(2 ** intento)
                    continue
                out = {"ok": False, "error": f"HTTP 429 rate limit {exc.reason}"}
            except ERRORES_RED:
                if not idempotente or intento == self.reintentos:
                    raise
                self.reintentos_por_red += 1
                time.sleep(2 ** intento)
                continue
            if out.get("ok"):
                return out
            error = str(out.get("error", "respuesta inválida"))
            if intento < self.reintentos and any(m in error.lower() for m in MARCAS_LIMITE):
                self.reintentos_por_limite += 1
                time.sleep(2 ** intento)  # 2, 4, 8, 16 s
                continue
            raise SinPermisoDrive(f"Apps Script ({accion}): {error}")
        raise AssertionError("inalcanzable")


class DriveClient:
    def __init__(self, sa_key: Path, oauth_token: Path, logger: logging.Logger):
        self._creds, self.modo = _credenciales(sa_key, oauth_token)
        self._local = threading.local()
        self.logger = logger
        url, clave = str_env("CARNET_APPS_SCRIPT_URL"), str_env("CARNET_APPS_SCRIPT_CLAVE")
        self.puente = PuenteAppsScript(url, clave) if url and clave else None
        if self.puente:
            self.modo += "+apps_script"

    @property
    def svc(self):
        # httplib2 no es seguro entre hilos: un servicio por hilo (la sincronización sube en paralelo).
        if not hasattr(self._local, "svc"):
            self._local.svc = build("drive", "v3", credentials=self._creds, cache_discovery=False)
        return self._local.svc

    def verificar_escritura(self, root_id: str) -> str | None:
        """None si se puede subir y eliminar en la raíz como su dueño; si no, el motivo."""
        raiz = self.svc.files().get(fileId=root_id, fields="owners(emailAddress)", supportsAllDrives=True).execute(num_retries=6)
        duenos = [o["emailAddress"] for o in raiz.get("owners", [])]
        if self.puente:
            try:
                yo = self.puente.llamar("ping", idempotente=True)["usuario"]
            except Exception as exc:
                return f"Apps Script no responde: {exc}"
        elif self.modo == "oauth":
            yo = self.svc.about().get(fields="user(emailAddress)").execute(num_retries=6)["user"]["emailAddress"]
        else:
            return "solo cuenta de servicio: no puede subir ni eliminar (configure CARNET_APPS_SCRIPT_URL/_CLAVE)"
        if yo not in duenos:
            return f"se escribiría como {yo}, pero la carpeta raíz pertenece a {', '.join(duenos)}"
        return None

    def listar(self, q: str) -> list[Item]:
        out: list[Item] = []
        token = None
        while True:
            resp = self.svc.files().list(
                q=q, pageSize=1000, pageToken=token, fields="nextPageToken,files(id,name,mimeType,parents)",
                supportsAllDrives=True, includeItemsFromAllDrives=True,
            ).execute(num_retries=6)
            out += [Item(f["id"], f["name"], f["mimeType"], tuple(f.get("parents") or ())) for f in resp["files"]]
            token = resp.get("nextPageToken")
            if not token:
                return out

    def obtener(self, file_id: str) -> Item:
        f = self.svc.files().get(fileId=file_id, fields="id,name,mimeType,parents", supportsAllDrives=True).execute(num_retries=6)
        return Item(f["id"], f["name"], f["mimeType"], tuple(f.get("parents") or ()))

    def descargar(self, file_id: str) -> bytes:
        """Contenido de un archivo (lectura: basta la cuenta de servicio)."""
        import io

        fh = io.BytesIO()
        descarga = MediaIoBaseDownload(fh, self.svc.files().get_media(fileId=file_id, supportsAllDrives=True))
        terminado = False
        while not terminado:
            _, terminado = descarga.next_chunk(num_retries=6)
        return fh.getvalue()

    def crear_carpeta(self, nombre: str, parent_id: str) -> str:
        body = {"name": nombre, "mimeType": FOLDER, "parents": [parent_id]}
        try:
            return self.svc.files().create(body=body, fields="id", supportsAllDrives=True).execute(num_retries=6)["id"]
        except HttpError as exc:
            if _es_falta_de_permiso(exc):
                raise SinPermisoDrive(f"crear carpeta '{nombre}': {exc.reason}") from exc
            raise

    ESPERA_VERIFICACION_S = 10  # Apps Script puede seguir creando el archivo tras cortarse la conexión

    def _buscar_en_carpeta(self, nombre: str, parent_id: str) -> str | None:
        resp = self.svc.files().list(
            q=f"name = '{nombre}' and '{parent_id}' in parents and trashed = false", fields="files(id)",
            supportsAllDrives=True, includeItemsFromAllDrives=True,
        ).execute(num_retries=6)
        return resp["files"][0]["id"] if resp["files"] else None

    def subir_pdf(self, ruta: Path, nombre: str, parent_id: str, props: dict[str, str]) -> str:
        if self.puente:
            contenido = base64.b64encode(ruta.read_bytes()).decode()
            for intento in range(1, 4):
                try:
                    return self.puente.llamar(
                        "subir", carpeta_id=parent_id, nombre=nombre, contenido_b64=contenido,
                        descripcion=json.dumps(props, ensure_ascii=False),
                    )["id"]
                except ERRORES_RED as exc:
                    # Subir no es idempotente: antes de reintentar se verifica que no haya quedado (sin duplicar).
                    time.sleep(self.ESPERA_VERIFICACION_S)
                    existente = self._buscar_en_carpeta(nombre, parent_id)
                    if existente:
                        self.logger.warning("subir '%s': %s, pero el archivo sí quedó en Drive", nombre, exc)
                        return existente
                    if intento == 3:
                        raise
                    self.logger.warning("subir '%s': %s; no quedó en Drive, reintento %s/2", nombre, exc, intento)
        body = {"name": nombre, "parents": [parent_id], "appProperties": props}
        media = MediaFileUpload(str(ruta), mimetype=PDF, resumable=False)
        try:
            return self.svc.files().create(body=body, media_body=media, fields="id", supportsAllDrives=True).execute(num_retries=6)["id"]
        except HttpError as exc:
            if _es_falta_de_permiso(exc):
                raise SinPermisoDrive(f"subir '{nombre}' ({self.modo}): {exc.reason}") from exc
            raise

    def mover(self, file_id: str, destino_id: str) -> None:
        actual = self.obtener(file_id)
        try:
            self.svc.files().update(
                fileId=file_id, addParents=destino_id, removeParents=",".join(actual.parents),
                fields="id,parents", supportsAllDrives=True,
            ).execute(num_retries=6)
        except HttpError as exc:
            if _es_falta_de_permiso(exc):
                raise SinPermisoDrive(f"mover {actual.name}: {exc.reason}") from exc
            raise

    def eliminar_carpeta_vacia(self, carpeta_id: str) -> None:
        """Papelera para una carpeta vacía (requiere la acción 'eliminar_carpeta' del Apps Script)."""
        if not self.puente:
            raise SinPermisoDrive("eliminar carpetas requiere el Apps Script")
        self.puente.llamar("eliminar_carpeta", idempotente=True, carpeta_id=carpeta_id)

    def restaurar(self, file_id: str) -> None:
        """Saca de la papelera (requiere la acción 'restaurar' del Apps Script)."""
        if not self.puente:
            raise SinPermisoDrive("restaurar desde la papelera requiere el Apps Script")
        self.puente.llamar("restaurar", idempotente=True, file_id=file_id)

    def eliminar(self, file_id: str) -> None:
        """Envía a la papelera (recuperable 30 días); solo el dueño puede."""
        if self.puente:
            self.puente.llamar("eliminar", idempotente=True, file_id=file_id)
            return
        try:
            self.svc.files().update(fileId=file_id, body={"trashed": True}, supportsAllDrives=True).execute(num_retries=6)
        except HttpError as exc:
            if _es_falta_de_permiso(exc):
                raise SinPermisoDrive(f"eliminar ({self.modo}): {exc.reason}") from exc
            raise
