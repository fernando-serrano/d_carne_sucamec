"""Resumen de la corrida por Microsoft Graph (client_credentials). Un fallo nunca invalida la corrida.

Formato alineado con estados-gadso: asunto con grupo/adjuntos/corrida, tabla horizontal de resumen
y lista de archivos adjuntos.
"""

from __future__ import annotations

import base64
import html
import json
import logging
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

from .config import bool_env, str_env
from .domain import Accion, Estado

_TH = "border:1px solid #d0d7de;padding:8px;background:#f6f8fa;text-align:center;"
_TD = "border:1px solid #d0d7de;padding:8px;text-align:center;"
_RESALTE = "background:#fff3b0;padding:2px 6px;border-radius:4px;"
_HECHAS = ("HECHO", "SIMULADO")


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


def columnas_corrida(resultados, sync) -> list[tuple[str, int]]:
    """Tabla de resumen de la corrida normal (consulta SUCAMEC + Drive + MinIO)."""
    estados = [r.estado for r in resultados.values()]

    def hechas(*acciones: Accion) -> int:
        return sum(1 for s in sync if s.operacion.accion in acciones and s.estado in _HECHAS)

    return [
        ("DNIs consultados", len(estados)),
        ("Encontrados", estados.count(Estado.ENCONTRADO)),
        ("No encontrados", estados.count(Estado.NO_ENCONTRADO)),
        ("Error consulta", estados.count(Estado.ERROR)),
        ("Carnés subidos", hechas(Accion.SUBIR, Accion.SUBIR_A_VENCIDOS)),
        ("Pasados a vencidos", hechas(Accion.MOVER_A_VENCIDOS)),
        ("Eliminados", hechas(Accion.ELIMINAR)),
        ("Errores Drive", sum(1 for s in sync if s.estado in ("ERROR", "SIN_PERMISO"))),
        ("Errores MinIO", sum(1 for s in sync if s.minio == "MINIO_ERROR")),
    ]


def construir_asunto(prefijo: str, flujo: str, grupo: str, run_id: str, n_adjuntos: int) -> str:
    piezas = [prefijo, "Resumen flujo SUCAMEC", flujo, grupo, f"{n_adjuntos} excel" if n_adjuntos else "", run_id]
    return " | ".join(p for p in piezas if p)


def construir_cuerpo(flujo: str, grupo: str, run_id: str, columnas: list[tuple[str, int]], adjuntos: list[Path],
                     simulado: bool, ahora: datetime | None = None) -> str:
    ahora = ahora or datetime.now()
    cabecera = "".join(f"<th style='{_TH}'>{html.escape(k)}</th>" for k, _ in columnas)
    valores = "".join(f"<td style='{_TD}'>{v}</td>" for _, v in columnas)
    lista = "".join(f"<li>{html.escape(p.name)}</li>" for p in adjuntos) or "<li>Sin adjuntos disponibles</li>"
    modo = ("<p><strong>Modo:</strong> <span style='background:#ffd8a8;padding:2px 6px;border-radius:4px;'>"
            "SIMULACIÓN</span> (sin cambios en Drive ni MinIO)</p>") if simulado else ""
    return (
        "<p>Saludos &#129302;</p>"
        f"<p>Se completó la corrida del flujo <strong>SUCAMEC - {html.escape(flujo)}</strong> "
        "y se adjuntan los Excel generados.</p>"
        "<p>"
        f"<strong>Grupo:</strong> {html.escape(grupo)}<br>"
        f"<strong>Corrida:</strong> {html.escape(run_id)}<br>"
        f"<strong>Generado:</strong> <span style='{_RESALTE}'>{ahora:%d/%m/%Y}</span> "
        f"<span style='{_RESALTE}'>{ahora:%H:%M:%S} hras</span>"
        "</p>"
        f"{modo}"
        "<p><strong>Resumen operativo:</strong></p>"
        "<table style='border-collapse:collapse;border:1px solid #d0d7de;'>"
        f"<thead><tr>{cabecera}</tr></thead><tbody><tr>{valores}</tr></tbody></table>"
        "<p><strong>Archivos adjuntos:</strong></p>"
        f"<ul>{lista}</ul>"
        "<p>Correo emitido automáticamente por el bot.</p>"
    )


def enviar_resumen(run_id: str, flujo: str, grupo: str, columnas: list[tuple[str, int]], adjuntos: list[Path],
                   simulado: bool, logger: logging.Logger) -> bool:
    if not bool_env("MS_GRAPH_MAIL_ENABLED", False):
        logger.info("Correo deshabilitado (MS_GRAPH_MAIL_ENABLED)")
        return False
    para, sender = _destinatarios("MS_GRAPH_TO"), str_env("MS_GRAPH_SENDER")
    if not para or not sender:
        logger.warning("Correo omitido: falta MS_GRAPH_TO o MS_GRAPH_SENDER")
        return False
    adjuntos = [p for p in adjuntos if p and p.exists()]
    prefijo = str_env("MS_GRAPH_SUBJECT_PREFIX", "BOT CARNET-SUCAMEC")
    mensaje = {
        "message": {
            "subject": construir_asunto(prefijo, flujo, grupo, run_id, len(adjuntos)),
            "body": {"contentType": "HTML",
                     "content": construir_cuerpo(flujo, grupo, run_id, columnas, adjuntos, simulado)},
            "toRecipients": [{"emailAddress": {"address": d}} for d in para],
            "ccRecipients": [{"emailAddress": {"address": d}} for d in _destinatarios("MS_GRAPH_CC")],
            "attachments": [{
                "@odata.type": "#microsoft.graph.fileAttachment", "name": p.name,
                "contentType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "contentBytes": base64.b64encode(p.read_bytes()).decode(),
            } for p in adjuntos],
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
        logger.info("Correo de resumen enviado a %s destinatario(s) | adjuntos=%s", len(para),
                    ",".join(p.name for p in adjuntos) or "-")
        return True
    except Exception as exc:
        logger.warning("No se pudo enviar el correo de resumen: %s", exc)
        return False
