"""Saneamiento de "CARNÉS ELECTRONICO" guiado por el contenido de cada PDF.

  .venv\\Scripts\\python.exe scripts\\sanear_drive.py              # SIMULACIÓN: descarga y valida, Excel con el plan
  .venv\\Scripts\\python.exe scripts\\sanear_drive.py --aplicar    # mueve/renombra, copias idénticas a la papelera
  .venv\\Scripts\\python.exe scripts\\sanear_drive.py --revertir   # deshace movimientos y nombres

Alcance: carpetas de día duplicadas, PDFs sueltos en carpetas de mes, carpetas sin fecha válida
y PDFs con nombre repetido en su carpeta. Detalle de reglas en src/carnet_flow/drive_sync/saneamiento.py.
Salida en renombrado/: plan_saneamiento.xlsx, pdf_meta.jsonl (caché de lectura), saneamiento.jsonl (reversión).
"""

import argparse
import io
import json
import logging
import sys
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from googleapiclient.discovery import build  # noqa: E402
from googleapiclient.http import MediaIoBaseDownload  # noqa: E402
from openpyxl import Workbook  # noqa: E402
from openpyxl.styles import Font  # noqa: E402

from carnet_flow.config import BASE_DIR, load_settings  # noqa: E402
from carnet_flow.domain import negocio_desde_texto, normalizar_dni, parse_fecha_carpeta, sin_tildes  # noqa: E402
from carnet_flow.drive_sync.client import FOLDER, PDF, DriveClient, Item, SinPermisoDrive  # noqa: E402
from carnet_flow.drive_sync.folders import ResolverCarpetas  # noqa: E402
from carnet_flow.drive_sync.index import Arbol  # noqa: E402
from carnet_flow.drive_sync.renombrado import _DNI_RE, planificar, planificar_carpetas  # noqa: E402
from carnet_flow.drive_sync.saneamiento import Documento, decidir, extraer_datos_pdf, extraer_huella  # noqa: E402

p = argparse.ArgumentParser()
p.add_argument("--aplicar", action="store_true")
p.add_argument("--revertir", action="store_true")
args = p.parse_args()

salida = BASE_DIR / "renombrado"
salida.mkdir(exist_ok=True)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s | %(message)s",
                    handlers=[logging.StreamHandler(), logging.FileHandler(salida / "saneamiento.log", encoding="utf-8")])
log = logging.getLogger("saneamiento")
logging.getLogger("googleapiclient.discovery_cache").setLevel(logging.ERROR)
s = load_settings()
client = DriveClient(s.drive_sa_key, s.drive_oauth_token, log)
registro = salida / "saneamiento.jsonl"

if args.revertir:
    n = 0
    for linea in reversed(registro.read_text(encoding="utf-8").splitlines()):
        e = json.loads(linea)
        if e["tipo"] in ("MOVER", "RENOMBRAR"):
            client.svc.files().update(fileId=e["id"], addParents=e["carpeta_anterior"], removeParents=e["carpeta_nueva"],
                                      body={"name": e["nombre_anterior"]}, supportsAllDrives=True).execute()
            n += 1
    log.info("Revertidos %s (las copias enviadas a la papelera se restauran desde la papelera de Drive)", n)
    sys.exit(0)

# 1. Listado fresco con md5
log.info("Listando Drive...")
def listar(q, campos):
    out, tok = [], None
    while True:
        r = client.svc.files().list(q=q, pageSize=1000, pageToken=tok,
                                    fields=f"nextPageToken,files({campos})").execute(num_retries=6)
        out += r["files"]
        tok = r.get("nextPageToken")
        if not tok:
            return out

arbol = Arbol(s.drive_root_id)
for f in listar(f"mimeType = '{FOLDER}' and trashed = false", "id,name,mimeType,parents"):
    arbol.registrar_carpeta(Item(f["id"], f["name"], f["mimeType"], tuple(f.get("parents") or ())))
crudos = listar(f"mimeType = '{PDF}' and trashed = false", "id,name,mimeType,parents,md5Checksum")
pdfs = [Item(f["id"], f["name"], f["mimeType"], tuple(f.get("parents") or ())) for f in crudos]
md5 = {f["id"]: f.get("md5Checksum", "") for f in crudos}
log.info("Drive: %s carpetas, %s PDFs", len(arbol.carpetas), len(pdfs))

# 2. Alcance: carpetas problemáticas (y sus hermanas canónicas) + PDFs problemáticos
plan_carp = planificar_carpetas(arbol, s.drive_carpeta_vencidos)
carpetas_alcance = {r.file_id for r in plan_carp if r.estado in ("COLISION", "SIN_FECHA")}
for r in plan_carp:
    if r.estado == "COLISION":
        for hermana in arbol.subcarpetas(r.carpeta_id):
            if hermana.name == r.nuevo:
                carpetas_alcance.add(hermana.id)
ids_alcance = {r.file_id for r in planificar(arbol, pdfs, s.drive_carpeta_vencidos)
               if r.estado in ("FECHA_APROXIMADA", "SIN_FECHA", "COLISION")}
vencidos_txt = sin_tildes(s.drive_carpeta_vencidos).upper()
docs_base: list[Documento] = []
for pdf in pdfs:
    if not pdf.parents or (pdf.id not in ids_alcance and pdf.parents[0] not in carpetas_alcance):
        continue
    partes = arbol.ruta(pdf)
    if not partes:
        continue
    venc = sin_tildes(partes[0]).upper() == vencidos_txt
    neg = negocio_desde_texto((partes[1:] if venc else partes)[0] if len(partes) > venc else "")
    if not neg:
        continue
    m = _DNI_RE.match(pdf.name)
    docs_base.append(Documento(pdf.id, pdf.name, pdf.parents[0], "/".join(partes), neg, venc,
                               normalizar_dni(m.group(1)) if m else None, md5.get(pdf.id, "")))
log.info("En alcance: %s PDFs en %s carpetas", len(docs_base), len({d.carpeta_id for d in docs_base}))

# 3. Lectura del contenido (con caché por id+md5)
cache_meta = salida / "pdf_meta.jsonl"
meta: dict[str, tuple] = {}
if cache_meta.exists():
    for linea in cache_meta.read_text(encoding="utf-8").splitlines():
        e = json.loads(linea)
        meta[e["id"] + e["md5"]] = (e["dni"], date.fromisoformat(e["emision"]) if e["emision"] else None)
local = threading.local()
lock = threading.Lock()


def descargar(file_id: str) -> bytes:
    """Descarga con reintentos (Google corta conexiones ociosas: WinError 10054)."""
    for intento in range(1, 5):
        try:
            if not hasattr(local, "svc") or intento > 1:
                local.svc = build("drive", "v3", credentials=client.svc._http.credentials, cache_discovery=False)
            buf = io.BytesIO()
            dl = MediaIoBaseDownload(buf, local.svc.files().get_media(fileId=file_id))
            hecho = False
            while not hecho:
                _, hecho = dl.next_chunk(num_retries=3)
            return buf.getvalue()
        except Exception:
            if intento == 4:
                raise
            time.sleep(2 * intento)

def leer(d: Documento):
    clave = d.file_id + d.md5
    if clave in meta:
        return d, meta[clave]
    dni, em = extraer_datos_pdf(descargar(d.file_id))
    with lock, cache_meta.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"id": d.file_id, "md5": d.md5, "dni": dni, "emision": em.isoformat() if em else None}) + "\n")
    return d, (dni, em)

docs: list[Documento] = []
with ThreadPoolExecutor(max_workers=8) as ex:
    for i, (d, (dni, em)) in enumerate(ex.map(leer, docs_base), start=1):
        docs.append(Documento(**{**d.__dict__, "dni_pdf": dni, "emision_pdf": em}))
        if i % 250 == 0:
            log.info("Leídos %s/%s PDFs", i, len(docs_base))

# 3b. N° de carné solo para grupos en conflicto (mismo DNI y emisión, bytes distintos)
grupos = defaultdict(list)
for d in docs:
    if d.dni_pdf and d.emision_pdf:
        grupos[(d.negocio, d.vencidos, d.dni_pdf, d.emision_pdf)].append(d)
conflicto = {d.file_id for g in grupos.values() if len({x.md5 for x in g}) > 1 for d in g}
cache_nro = salida / "huella_texto.jsonl"
nros: dict[str, str | None] = {}
if cache_nro.exists():
    for linea in cache_nro.read_text(encoding="utf-8").splitlines():
        e = json.loads(linea)
        nros[e["id"] + e["md5"]] = e["nro"]

def leer_nro(d: Documento):
    clave = d.file_id + d.md5
    if clave in nros:
        return d.file_id, nros[clave]
    nro = extraer_huella(descargar(d.file_id))
    with lock, cache_nro.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"id": d.file_id, "md5": d.md5, "nro": nro}) + "\n")
    return d.file_id, nro

log.info("Leyendo N° de carné de %s PDFs en conflicto", len(conflicto))
with ThreadPoolExecutor(max_workers=8) as ex:
    nro_por_id = dict(ex.map(leer_nro, [d for d in docs if d.file_id in conflicto]))
docs = [replace(d, huella=nro_por_id.get(d.file_id)) for d in docs]

# 4. Decisiones (en aplicar, el resolver crea las carpetas de día que falten)
resolver = ResolverCarpetas(arbol, client, s.drive_carpeta_vencidos, crear=args.aplicar)
acciones = decidir(docs, resolver)
conteo = Counter(a.tipo for a in acciones)
log.info("Plan: %s", dict(conteo))

# Diferencia entre la fecha de la carpeta actual y la impresa (para el reporte)
def fecha_carpeta(a):
    partes = a.doc.ruta.split("/")
    anio = next((int(x) for x in partes if x.strip().isdigit()), None)
    return parse_fecha_carpeta(partes[-1], anio)

wb = Workbook()
ws = wb.active
ws.title = "Resumen"
ws.append(["Acción", "PDFs"])
for k, v in conteo.most_common():
    ws.append([k, v])
ws.append([])
ws.append(["Fecha impresa distinta a la carpeta actual", sum(1 for a in acciones if fecha_carpeta(a) and a.doc.emision_pdf and fecha_carpeta(a) != a.doc.emision_pdf)])
for tipo in ("MOVER", "RENOMBRAR", "PAPELERA_DUPLICADO", "REVISAR", "OK"):
    filas = [a for a in acciones if a.tipo == tipo]
    if not filas:
        continue
    h = wb.create_sheet(tipo)
    h.append(["Carpeta actual", "Nombre actual", "DNI impreso", "Emisión impresa", "Carpeta destino", "Nombre nuevo", "Motivo", "Enlace"])
    for a in filas:
        h.append([a.doc.ruta, a.doc.nombre, a.doc.dni_pdf, a.doc.emision_pdf.strftime("%d/%m/%Y") if a.doc.emision_pdf else "",
                  a.destino_ruta, a.nombre_nuevo, a.motivo, f"https://drive.google.com/file/d/{a.doc.file_id}/view"])
    for col, w in zip("ABCDEFGH", (55, 30, 12, 14, 55, 28, 50, 60)):
        h.column_dimensions[col].width = w
for h in wb.worksheets:
    for c in h[1]:
        c.font = Font(bold=True)
plan_xlsx = salida / "plan_saneamiento.xlsx"
wb.save(plan_xlsx)
log.info("Plan escrito en %s", plan_xlsx)

if not args.aplicar:
    log.info("SIMULACIÓN: no se modificó Drive. Use --aplicar.")
    sys.exit(0)

# 5. Aplicar
hechos = set()
if registro.exists():
    hechos = {json.loads(l)["id"] for l in registro.read_text(encoding="utf-8").splitlines()}
ok = err = 0
with registro.open("a", encoding="utf-8") as fh:
    for a in acciones:
        if a.tipo not in ("MOVER", "RENOMBRAR", "PAPELERA_DUPLICADO") or a.doc.file_id in hechos:
            continue
        try:
            if a.tipo == "PAPELERA_DUPLICADO":
                client.eliminar(a.doc.file_id)
            elif a.tipo == "MOVER":
                client.svc.files().update(fileId=a.doc.file_id, addParents=a.destino_id, removeParents=a.doc.carpeta_id,
                                          body={"name": a.nombre_nuevo}, supportsAllDrives=True).execute(num_retries=6)
            else:
                client.svc.files().update(fileId=a.doc.file_id, body={"name": a.nombre_nuevo},
                                          supportsAllDrives=True).execute(num_retries=6)
            fh.write(json.dumps({"id": a.doc.file_id, "tipo": a.tipo, "carpeta_anterior": a.doc.carpeta_id,
                                 "carpeta_nueva": a.destino_id, "nombre_anterior": a.doc.nombre,
                                 "nombre_nuevo": a.nombre_nuevo}, ensure_ascii=False) + "\n")
            fh.flush()
            ok += 1
            if ok % 200 == 0:
                log.info("Aplicadas %s", ok)
        except (SinPermisoDrive, Exception) as exc:  # una falla no detiene el resto
            err += 1
            log.warning("No se aplicó %s %s/%s: %s", a.tipo, a.doc.ruta, a.doc.nombre, str(exc)[:200])
log.info("Fin: %s aplicadas, %s errores", ok, err)

# 6. Carpetas que quedaron vacías (para eliminarlas)
ocupadas = defaultdict(int)
for pdf in pdfs:
    if pdf.parents:
        ocupadas[pdf.parents[0]] += 1
for linea in registro.read_text(encoding="utf-8").splitlines():
    e = json.loads(linea)
    if e["tipo"] in ("MOVER", "PAPELERA_DUPLICADO"):
        ocupadas[e["carpeta_anterior"]] -= 1
vacias = [cid for cid in carpetas_alcance if ocupadas.get(cid, 0) <= 0 and not arbol.subcarpetas(cid)]
(salida / "carpetas_vacias.json").write_text(json.dumps(
    [{"id": c, "ruta": "/".join((arbol.ruta(arbol.carpetas[c]) or []) + [arbol.carpetas[c].name])} for c in vacias],
    ensure_ascii=False, indent=1), encoding="utf-8")
log.info("Carpetas vacías tras el saneamiento: %s (renombrado/carpetas_vacias.json)", len(vacias))
sys.exit(1 if err else 0)
