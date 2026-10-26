"""Envía a la papelera las carpetas que quedaron vacías tras sanear_drive.py --aplicar.

  .venv\\Scripts\\python.exe eliminar_carpetas_vacias.py            # lista, no borra
  .venv\\Scripts\\python.exe eliminar_carpetas_vacias.py --aplicar  # papelera (recuperable 30 días)

Requiere la versión del Apps Script con la acción 'eliminar_carpeta' (apps_script/Code.gs).
El script vuelve a comprobar que cada carpeta esté vacía antes de eliminarla.
"""

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from carnet_flow.config import BASE_DIR, load_settings  # noqa: E402
from carnet_flow.drive_sync.client import DriveClient  # noqa: E402

p = argparse.ArgumentParser()
p.add_argument("--aplicar", action="store_true")
args = p.parse_args()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s | %(message)s")
log = logging.getLogger("vacias")
s = load_settings()
c = DriveClient(s.drive_sa_key, s.drive_oauth_token, log)
vacias = json.loads((BASE_DIR / "renombrado" / "carpetas_vacias.json").read_text(encoding="utf-8"))
ok = err = 0
for v in vacias:
    if not args.aplicar:
        log.info("[SIMULADO] %s", v["ruta"])
        continue
    try:
        c.eliminar_carpeta_vacia(v["id"])
        ok += 1
        log.info("Papelera: %s", v["ruta"])
    except Exception as exc:
        err += 1
        log.warning("No se eliminó %s: %s", v["ruta"], exc)
log.info("Fin: %s eliminadas, %s errores, %s listadas", ok, err, len(vacias))
