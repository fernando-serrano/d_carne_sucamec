"""Resumen de la corrida por Microsoft Graph (client_credentials). Un fallo nunca invalida la corrida."""

from __future__ import annotations

import base64
import html
import json
import logging
import urllib.parse
import urllib.request
from pathlib import Path

from .config import bool_env, str_env


def _destinatarios(nombre: str) -> list[str]:
    return [d.strip() for d in str_env(nombre).replace(",", ";").split(";") if d.strip()]


def _token() -> str:
    url = f"https://login.microsoftonline.com/{str_env('MS_GRAPH_TENANT_ID')}/oauth2/v2.0/token"
    data = urllib.parse.urlencode({
        "client_id": str_env("MS_GRAPH_CLIENT_ID"), "client_secret": str_env("MS_GRAPH_CLIENT_SECRET"),
        "scope": "https://graph.microsoft.com/.default", "grant_type": "client_credentials",
    }).encode()
    with urllib.request.urlopen(urllib.request.Request(url, data=data, method="POST"), timeout=60) as resp:
        return json.loads(resp.read().decode() or "{}").get("access_token", "")


def enviar_resumen(run_id: str, resumen: dict[str, int], adjunto: Path, simulado: bool, logger: logging.Logger) -> bool:
    if not bool_env("MS_GRAPH_MAIL_ENABLED", False):
        logger.info("Correo deshabilitado (MS_GRAPH_MAIL_ENABLED)")
        return False
    para, sender = _destinatarios("MS_GRAPH_TO"), str_env("MS_GRAPH_SENDER")
    if not para or not sender:
        logger.warning("Correo omitido: falta MS_GRAPH_TO o MS_GRAPH_SENDER")
        return False
    filas = "".join(
        f"<tr><td style='padding:4px 10px;border:1px solid #ddd'>{html.escape(k)}</td>"
        f"<td style='padding:4px 10px;border:1px solid #ddd;text-align:right'>{v}</td></tr>"
        for k, v in sorted(resumen.items())
    )
    modo = "SIMULACIÓN (sin cambios en Drive)" if simulado else "Cambios aplicados en Drive"
    cuerpo = (f"<p>Saludos,</p><p>Resumen de la corrida <b>{run_id}</b> — {modo}.</p>"
              f"<table style='border-collapse:collapse'>{filas}</table><p>Detalle en el Excel adjunto.</p>")
    mensaje = {
        "message": {
            "subject": f"{str_env('MS_GRAPH_SUBJECT_PREFIX', 'BOT CARNET-SUCAMEC')} - Carnés SUCAMEC {run_id}",
            "body": {"contentType": "HTML", "content": cuerpo},
            "toRecipients": [{"emailAddress": {"address": d}} for d in para],
            "ccRecipients": [{"emailAddress": {"address": d}} for d in _destinatarios("MS_GRAPH_CC")],
            "attachments": [{
                "@odata.type": "#microsoft.graph.fileAttachment", "name": adjunto.name,
                "contentType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "contentBytes": base64.b64encode(adjunto.read_bytes()).decode(),
            }],
        },
        "saveToSentItems": True,
    }
    try:
        req = urllib.request.Request(
            f"https://graph.microsoft.com/v1.0/users/{sender}/sendMail", data=json.dumps(mensaje).encode(),
            headers={"Authorization": f"Bearer {_token()}", "Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(req, timeout=120):
            pass
        logger.info("Correo de resumen enviado a %s destinatario(s)", len(para))
        return True
    except Exception as exc:
        logger.warning("No se pudo enviar el correo de resumen: %s", exc)
        return False
