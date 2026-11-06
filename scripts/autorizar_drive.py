"""Autoriza al bot a escribir en Drive en nombre del DUEÑO de la carpeta (una sola vez).

Por qué: la cuenta de servicio tiene cuota 0 y no es dueña de los PDFs, así que no
puede subir ni eliminar en una Mi unidad personal. Con este token el bot actúa como
el dueño. Requisitos:
  1. En el proyecto GCP de la cuenta de servicio: habilitar Drive API y crear un
     "ID de cliente OAuth" de tipo "App de escritorio"; descargar el JSON como
     secrets/oauth_client.json
  2. Ejecutar:  .venv\\Scripts\\python.exe scripts\\autorizar_drive.py
     e iniciar sesión con la cuenta DUEÑA de "CARNÉS ELECTRONICO".
Resultado: secrets/drive_oauth_token.json (el bot lo usa automáticamente).
"""

from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

BASE = Path(__file__).resolve().parents[1]
CLIENTE = BASE / "secrets" / "oauth_client.json"
TOKEN = BASE / "secrets" / "drive_oauth_token.json"

if not CLIENTE.exists():
    raise SystemExit(f"Falta {CLIENTE}. Ver instrucciones al inicio de este archivo.")
flow = InstalledAppFlow.from_client_secrets_file(str(CLIENTE), ["https://www.googleapis.com/auth/drive"])
# Sin abrir navegador: con varios navegadores abiertos, se copia el enlace en el que tenga la cuenta dueña.
creds = flow.run_local_server(port=0, prompt="consent", open_browser=False,
                              authorization_prompt_message="Abra este enlace con la cuenta duena de la carpeta:\n{url}")
TOKEN.write_text(creds.to_json(), encoding="utf-8")
print(f"Token guardado en {TOKEN}")
