"""Cliente mínimo de Google Drive.

Credenciales:
- Token OAuth del dueño de la carpeta (secrets/drive_oauth_token.json) si existe:
  permite subir y eliminar.
- Si no, la cuenta de servicio: lee, crea carpetas y mueve, pero NO puede subir
  (cuota 0 en una Mi unidad ajena) ni eliminar (no es dueña).
"""

from __future__ import annotations

import base64
import json
import logging
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

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


class PuenteAppsScript:
    """Subir y eliminar como el dueño, vía la aplicación web de apps_script/Code.gs."""

    def __init__(self, url: str, clave: str):
        self.url, self.clave = url, clave

    def llamar(self, accion: str, **datos) -> dict:
        cuerpo = json.dumps({"clave": self.clave, "accion": accion, **datos}).encode()
        req = urllib.request.Request(self.url, data=cuerpo, headers={"Content-Type": "application/json"}, method="POST")
        # Apps Script responde con un 302 a googleusercontent.com; urllib lo sigue como GET.
        with urllib.request.urlopen(req, timeout=120) as resp:
            out = json.loads(resp.read().decode() or "{}")
        if not out.get("ok"):
            raise SinPermisoDrive(f"Apps Script ({accion}): {out.get('error', 'respuesta inválida')}")
        return out


class DriveClient:
    def __init__(self, sa_key: Path, oauth_token: Path, logger: logging.Logger):
        creds, self.modo = _credenciales(sa_key, oauth_token)
        self.svc = build("drive", "v3", credentials=creds, cache_discovery=False)
        self.logger = logger
        url, clave = str_env("CARNET_APPS_SCRIPT_URL"), str_env("CARNET_APPS_SCRIPT_CLAVE")
        self.puente = PuenteAppsScript(url, clave) if url and clave else None
        if self.puente:
            self.modo += "+apps_script"

    def verificar_escritura(self, root_id: str) -> str | None:
        """None si se puede subir y eliminar en la raíz como su dueño; si no, el motivo."""
        raiz = self.svc.files().get(fileId=root_id, fields="owners(emailAddress)", supportsAllDrives=True).execute(num_retries=6)
        duenos = [o["emailAddress"] for o in raiz.get("owners", [])]
        if self.puente:
            try:
                yo = self.puente.llamar("ping")["usuario"]
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

    def crear_carpeta(self, nombre: str, parent_id: str) -> str:
        body = {"name": nombre, "mimeType": FOLDER, "parents": [parent_id]}
        try:
            return self.svc.files().create(body=body, fields="id", supportsAllDrives=True).execute(num_retries=6)["id"]
        except HttpError as exc:
            if _es_falta_de_permiso(exc):
                raise SinPermisoDrive(f"crear carpeta '{nombre}': {exc.reason}") from exc
            raise

    def subir_pdf(self, ruta: Path, nombre: str, parent_id: str, props: dict[str, str]) -> str:
        if self.puente:
            return self.puente.llamar(
                "subir", carpeta_id=parent_id, nombre=nombre,
                contenido_b64=base64.b64encode(ruta.read_bytes()).decode(),
                descripcion=json.dumps(props, ensure_ascii=False),
            )["id"]
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
        self.puente.llamar("eliminar_carpeta", carpeta_id=carpeta_id)

    def restaurar(self, file_id: str) -> None:
        """Saca de la papelera (requiere la acción 'restaurar' del Apps Script)."""
        if not self.puente:
            raise SinPermisoDrive("restaurar desde la papelera requiere el Apps Script")
        self.puente.llamar("restaurar", file_id=file_id)

    def eliminar(self, file_id: str) -> None:
        """Envía a la papelera (recuperable 30 días); solo el dueño puede."""
        if self.puente:
            self.puente.llamar("eliminar", file_id=file_id)
            return
        try:
            self.svc.files().update(fileId=file_id, body={"trashed": True}, supportsAllDrives=True).execute(num_retries=6)
        except HttpError as exc:
            if _es_falta_de_permiso(exc):
                raise SinPermisoDrive(f"eliminar ({self.modo}): {exc.reason}") from exc
            raise
