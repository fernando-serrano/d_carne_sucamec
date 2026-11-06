"""Bitácora y punto de restauración de la migración del Drive "CARNÉS ELECTRONICO".

  .venv\\Scripts\\python.exe scripts\\migracion.py estado                        # cambios por paso y avance
  .venv\\Scripts\\python.exe scripts\\migracion.py restaurar --hasta 3           # SIMULACIÓN: qué se desharía
  .venv\\Scripts\\python.exe scripts\\migracion.py restaurar --hasta 3 --aplicar # deshace los pasos 6, 5 y 4

`--hasta N` deja el Drive como quedó al terminar el paso N (0 = estado original).
Sacar de la papelera requiere la acción 'restaurar' del Apps Script (apps_script/Code.gs).
Todo queda en logs/migracion.log; la restauración se registra en renombrado/restauracion_<N>.jsonl
y se puede retomar si se corta.
"""

import argparse
import json
import logging
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from carnet_flow.config import BASE_DIR, load_settings  # noqa: E402
from carnet_flow.drive_sync.client import DriveClient  # noqa: E402
from carnet_flow.drive_sync.migracion import PASOS, inversas, leer_registro, pasos_a_deshacer  # noqa: E402

p = argparse.ArgumentParser()
sub = p.add_subparsers(dest="cmd", required=True)
sub.add_parser("estado")
r = sub.add_parser("restaurar")
r.add_argument("--hasta", type=int, required=True, choices=range(0, len(PASOS)))
r.add_argument("--aplicar", action="store_true")
args = p.parse_args()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s | %(message)s",
                    handlers=[logging.StreamHandler(), logging.FileHandler(BASE_DIR / "logs" / "migracion.log", encoding="utf-8")])
log = logging.getLogger("migracion")
logging.getLogger("googleapiclient.discovery_cache").setLevel(logging.ERROR)
carpeta = BASE_DIR / "renombrado"


def _ultima(log_path: Path, marca: str) -> str:
    if not log_path.exists():
        return ""
    lineas = [x for x in log_path.read_text(encoding="utf-8", errors="replace").splitlines() if marca in x]
    return lineas[-1] if lineas else ""


if args.cmd == "estado":
    for paso in PASOS:
        entradas = leer_registro(carpeta / paso.registro)
        tipos = Counter(e.get("tipo") or e.get("accion") or "cambio" for e in entradas)
        log.info("Paso %s | %-62s | %6s cambios %s", paso.numero, paso.descripcion, len(entradas),
                 dict(tipos) if len(tipos) > 1 else "")
    avance = _ultima(BASE_DIR / "logs" / "vencidos.log", "Movidos") or "sin iniciar"
    fin = _ultima(BASE_DIR / "logs" / "vencidos.log", "Fin:")
    log.info("VENCIDOS: %s", fin or f"EN CURSO -> {avance}")
    sys.exit(0)

s = load_settings()
client = DriveClient(s.drive_sa_key, s.drive_oauth_token, log)
registro = carpeta / f"restauracion_{args.hasta}.jsonl"
hechos = {json.loads(x)["id"] + json.loads(x)["paso"] for x in registro.read_text(encoding="utf-8").splitlines()
          if x.strip()} if registro.exists() else set()

total = ok = err = 0
with registro.open("a", encoding="utf-8") as fh:
    for paso in pasos_a_deshacer(args.hasta):
        ops = [i for i in inversas(paso, leer_registro(carpeta / paso.registro)) if i.file_id + paso.clave not in hechos]
        log.info("Paso %s (%s): %s cambios a deshacer", paso.numero, paso.descripcion, len(ops))
        total += len(ops)
        if not args.aplicar:
            continue
        for i, op in enumerate(ops, 1):
            try:
                if op.restaurar:
                    client.restaurar(op.file_id)
                else:
                    kw = {"addParents": op.agregar, "removeParents": op.quitar} if op.agregar else {}
                    body = {"name": op.nombre} if op.nombre else {}
                    client.svc.files().update(fileId=op.file_id, body=body, fields="id", supportsAllDrives=True,
                                              **kw).execute(num_retries=6)
                fh.write(json.dumps({"id": op.file_id, "paso": paso.clave}) + "\n")
                ok += 1
            except Exception as exc:
                err += 1
                log.warning("No se deshizo %s (paso %s): %s", op.file_id, paso.numero, str(exc)[:200])
            if i % 200 == 0:
                fh.flush()
                log.info("Paso %s: %s/%s (errores %s)", paso.numero, i, len(ops), err)
                time.sleep(0.5)
        fh.flush()

if args.aplicar:
    log.info("Fin restauración hasta el paso %s: %s deshechos, %s errores", args.hasta, ok, err)
else:
    log.info("SIMULACIÓN: se desharían %s cambios. Use --aplicar.", total)
