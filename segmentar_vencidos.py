"""Mueve a VENCIDOS/<negocio>/<año>/<mes>/<día> los carnés no vigentes que ya estaban en Drive.

  .venv\\Scripts\\python.exe segmentar_vencidos.py              # SIMULACIÓN: Excel con el plan, no toca Drive
  .venv\\Scripts\\python.exe segmentar_vencidos.py --aplicar    # mueve (retoma si se corta)
  .venv\\Scripts\\python.exe segmentar_vencidos.py --revertir   # devuelve cada PDF a su carpeta anterior

Regla (drive_sync/vencidos.py): por negocio y DNI solo queda activo el carné más reciente
con menos de 3 años; los renovados y los vencidos sin renovación van a VENCIDOS.
Usa la cuenta de servicio (mover y crear carpetas no requiere ser dueño).
Salida en renombrado/: plan_vencidos.xlsx y vencidos_movidos.jsonl (checkpoint / reversión).
"""

import argparse
import json
import logging
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from openpyxl import Workbook  # noqa: E402
from openpyxl.styles import Font  # noqa: E402

from carnet_flow.config import BASE_DIR, load_settings  # noqa: E402
from carnet_flow.domain import hoy_lima  # noqa: E402
from carnet_flow.drive_sync.client import DriveClient  # noqa: E402
from carnet_flow.drive_sync.folders import ResolverCarpetas  # noqa: E402
from carnet_flow.drive_sync.index import cargar_arbol, construir_indice  # noqa: E402
from carnet_flow.drive_sync.vencidos import planificar_vencidos  # noqa: E402

p = argparse.ArgumentParser()
p.add_argument("--aplicar", action="store_true")
p.add_argument("--revertir", action="store_true")
args = p.parse_args()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s | %(message)s",
                    handlers=[logging.StreamHandler(), logging.FileHandler(BASE_DIR / "logs_vencidos.log", encoding="utf-8")])
log = logging.getLogger("vencidos")
s = load_settings()
salida = BASE_DIR / "renombrado"
salida.mkdir(exist_ok=True)
registro = salida / "vencidos_movidos.jsonl"
client = DriveClient(s.drive_sa_key, s.drive_oauth_token, log)


def _batch_mover(movs: list[tuple[str, str, str]]) -> dict[str, str]:
    """movs = (file_id, destino, origen). Devuelve {file_id: error}."""
    fallidos: dict[str, str] = {}

    def callback(request_id, _resp, exc):
        if exc is not None:
            fallidos[request_id] = str(exc)

    for intento in range(6):
        batch = client.svc.new_batch_http_request(callback=callback)
        for fid, destino, origen in movs:
            batch.add(client.svc.files().update(fileId=fid, addParents=destino, removeParents=origen, fields="id",
                                                supportsAllDrives=True), request_id=fid)
        try:
            batch.execute()
            break
        except (ConnectionError, TimeoutError, OSError) as exc:  # WinError 10054 de la red
            log.warning("Lote interrumpido (%s), reintento %s/6", exc, intento + 1)
            time.sleep(5 * (intento + 1))
            fallidos.clear()
    else:
        fallidos.update({fid: "lote sin respuesta tras 6 reintentos" for fid, _, _ in movs})
    return fallidos


if args.revertir:
    filas = [json.loads(x) for x in registro.read_text(encoding="utf-8").splitlines() if x.strip()]
    for i in range(0, len(filas), 50):
        fallidos = _batch_mover([(f["id"], f["origen"], f["destino"]) for f in filas[i:i + 50]])
        for fid, err in fallidos.items():
            log.warning("No se revirtió %s: %s", fid, err[:200])
    log.info("Revertidos %s movimientos", len(filas))
    sys.exit(0)

log.info("Listando Drive...")
arbol, pdfs = cargar_arbol(client, s.drive_root_id)
indice = construir_indice(arbol, pdfs, s.drive_carpeta_vencidos)
hoy = hoy_lima()
plan = planificar_vencidos(indice, hoy)
conteo = Counter(x.tipo for x in plan)
activos = indice.total() - conteo["MOVER"] - conteo["REVISAR"]
log.info("PDFs activos: %s | a VENCIDOS: %s | revisar: %s | quedan vigentes: %s",
         indice.total(), conteo["MOVER"], conteo["REVISAR"], activos)

resolver = ResolverCarpetas(arbol, client, s.drive_carpeta_vencidos, crear=args.aplicar)
padre = {pdf.id: pdf.parents[0] for pdf in pdfs if pdf.parents}
filas = []
for x in sorted(plan, key=lambda x: (x.carne.negocio, x.carne.fecha_emision or hoy, x.carne.dni)):
    c = x.carne
    destino = resolver.carpeta_para(c.negocio, c.fecha_emision, vencidos=True) if x.tipo == "MOVER" else ""
    filas.append((x, destino))

wb = Workbook()
ws = wb.active
ws.title = "Resumen"
ws.append(["Concepto", "Cantidad"])
for k, v in [("PDFs activos analizados", indice.total()), ("A VENCIDOS", conteo["MOVER"]),
             ("Revisar (sin fecha exacta)", conteo["REVISAR"]), ("Quedan vigentes", activos)]:
    ws.append([k, v])
ws2 = wb.create_sheet("Detalle")
ws2.append(["Tipo", "Negocio", "DNI", "Emisión", "Motivo", "Ruta actual", "Destino"])
for x, destino in filas:
    c = x.carne
    ws2.append([x.tipo, c.negocio, c.dni, c.fecha_emision.strftime("%d.%m.%Y") if c.fecha_emision else "",
                x.motivo, c.ruta, resolver.ruta_legible(destino) if destino else ""])
for hoja in (ws, ws2):
    for celda in hoja[1]:
        celda.font = Font(bold=True)
wb.save(salida / "plan_vencidos.xlsx")
log.info("Plan escrito en %s", salida / "plan_vencidos.xlsx")

if not args.aplicar:
    log.info("SIMULACIÓN: no se movió nada. Use --aplicar.")
    sys.exit(0)

hechos = set()
if registro.exists():
    hechos = {json.loads(x)["id"] for x in registro.read_text(encoding="utf-8").splitlines() if x.strip()}
pendientes = [(x.carne.file_id, destino, padre[x.carne.file_id]) for x, destino in filas
              if x.tipo == "MOVER" and x.carne.file_id not in hechos and x.carne.file_id in padre]
ok = errores = 0
with registro.open("a", encoding="utf-8") as fh:
    for i in range(0, len(pendientes), 50):
        grupo = pendientes[i:i + 50]
        fallidos = _batch_mover(grupo)
        for fid, destino, origen in grupo:
            if fid in fallidos:
                errores += 1
                log.warning("No se movió %s: %s", fid, fallidos[fid][:200])
            else:
                ok += 1
                fh.write(json.dumps({"id": fid, "origen": origen, "destino": destino}) + "\n")
        fh.flush()
        log.info("Movidos %s/%s (errores %s)", ok, len(pendientes), errores)
        if fallidos:
            time.sleep(2)
log.info("Fin: %s movidos a VENCIDOS, %s errores. Reversión: segmentar_vencidos.py --revertir", ok, errores)
