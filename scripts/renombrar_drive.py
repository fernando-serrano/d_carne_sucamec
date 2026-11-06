"""Renombra los PDFs de "CARNÉS ELECTRONICO" a <DNI>_<dd.mm.aaaa>.pdf.

  .venv\\Scripts\\python.exe scripts\\renombrar_drive.py                 # SIMULACIÓN: Excel con el plan, no toca Drive
  .venv\\Scripts\\python.exe scripts\\renombrar_drive.py --aplicar       # renombra (retoma si se corta)
  .venv\\Scripts\\python.exe scripts\\renombrar_drive.py --revertir      # vuelve a los nombres anteriores

Usa la cuenta de servicio (renombrar no requiere ser dueño ni ocupa cuota).
Salida en renombrado/: plan_renombrado.xlsx, listado_drive.json y renombrados.jsonl (checkpoint / reversión).
"""

import argparse
import logging
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from openpyxl import Workbook  # noqa: E402
from openpyxl.styles import Font  # noqa: E402

from carnet_flow.config import BASE_DIR, load_settings  # noqa: E402
from carnet_flow.drive_sync.client import DriveClient  # noqa: E402
from carnet_flow.drive_sync.index import cargar_arbol  # noqa: E402
from carnet_flow.drive_sync.renombrado import ejecutar, planificar, planificar_carpetas, revertir  # noqa: E402

p = argparse.ArgumentParser()
p.add_argument("--aplicar", action="store_true")
p.add_argument("--revertir", action="store_true")
p.add_argument("--relistar", action="store_true", help="Volver a listar Drive (ignora listado_drive.json)")
p.add_argument("--carpetas", action="store_true",
               help="Normalizar nombres de CARPETAS de mes (N.MES) y día (dd.mm.aaaa / SELVA.dd.mm.aaaa) en vez de PDFs")
args = p.parse_args()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s | %(message)s",
                    handlers=[logging.StreamHandler(), logging.FileHandler(BASE_DIR / "logs" / "renombrado.log", encoding="utf-8")])
log = logging.getLogger("renombrado")
s = load_settings()
salida = BASE_DIR / "renombrado"
salida.mkdir(exist_ok=True)
registro = salida / ("carpetas_renombradas.jsonl" if args.carpetas else "renombrados.jsonl")
client = DriveClient(s.drive_sa_key, s.drive_oauth_token, log)

if args.revertir:
    revertir(client, registro, log)
    sys.exit(0)

cache = salida / "listado_drive.json"
if args.relistar:
    cache.unlink(missing_ok=True)
log.info("Listando Drive...")
arbol, pdfs = cargar_arbol(client, s.drive_root_id, cache=cache)
plan = (planificar_carpetas(arbol, s.drive_carpeta_vencidos) if args.carpetas
        else planificar(arbol, pdfs, s.drive_carpeta_vencidos))
conteo = Counter(r.estado for r in plan)
log.info("Plan: %s", dict(conteo))

wb = Workbook()
ws = wb.active
ws.title = "Resumen"
ws.append(["Estado", "Cantidad", "Qué significa"])
significado = {
    "RENOMBRAR": "Se renombra a DNI_dd.mm.aaaa.pdf",
    "YA_OK": "Ya tiene el formato",
    "FECHA_APROXIMADA": "Carpeta de mes sin día: no se renombra (no se inventa el día)",
    "SIN_FECHA": "Carpeta sin fecha (p. ej. DFNUEVOS): no se renombra",
    "COLISION": "Quedaría con el mismo nombre que otro elemento en la misma carpeta: no se renombra",
    "NO_RECONOCIDO": "Nombre sin DNI o fuera de las carpetas de negocio: no se toca",
}
for estado, n in conteo.most_common():
    ws.append([estado, n, significado.get(estado, "")])
for estado in ["RENOMBRAR", "FECHA_APROXIMADA", "SIN_FECHA", "COLISION", "NO_RECONOCIDO"]:
    filas = [r for r in plan if r.estado == estado]
    if not filas:
        continue
    h = wb.create_sheet(estado[:31])
    h.append(["Carpeta", "Nombre actual", "Nombre nuevo", "Enlace"])
    for r in filas:
        h.append([r.ruta, r.actual, r.nuevo, f"https://drive.google.com/file/d/{r.file_id}/view"])
    h.column_dimensions["A"].width = 60
    h.column_dimensions["B"].width = 34
    h.column_dimensions["C"].width = 28
for h in wb.worksheets:
    for c in h[1]:
        c.font = Font(bold=True)
destino = salida / ("plan_carpetas.xlsx" if args.carpetas else "plan_renombrado.xlsx")
wb.save(destino)
log.info("Plan escrito en %s", destino)

if not args.aplicar:
    log.info("SIMULACIÓN: no se renombró nada. Use --aplicar para ejecutar.")
    sys.exit(0)

ok, errores = ejecutar(client, plan, registro, log)
log.info("Fin: %s renombrados, %s errores. Reversión: renombrar_drive.py --revertir", ok, errores)
sys.exit(1 if errores else 0)
