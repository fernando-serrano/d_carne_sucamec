from __future__ import annotations

import logging
import multiprocessing as mp
import shutil
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from ..config import BASE_DIR, Settings
from ..domain import (
    Accion,
    Estado,
    NEGOCIOS,
    Operacion,
    Plan,
    construir_plan,
    decidir_sync,
    hoy_lima,
    operaciones_ceses,
)
from ..drive_sync.client import DriveClient
from ..drive_sync.folders import ResolverCarpetas
from ..domain import IndiceDrive
from ..drive_sync.index import cargar_arbol, construir_indice, construir_indice_vencidos
from ..drive_sync.sync import aplicar
from ..minio_sync.reconciliar import aplicar_reconciliacion, filtrar_por_negocio, planificar_reconciliacion
from ..minio_sync.store import crear_almacen
from ..notifications import columnas_corrida, enviar_resumen
from ..reporting import escribir_reporte, escribir_reporte_minio
from ..sources.excel_source import expandir_negocios, leer_registros, resolver_excel
from . import manifest
from .worker import worker_main


@dataclass
class Opciones:
    negocios: tuple[str, ...] = NEGOCIOS
    padron: str = ""
    bajas: str = ""
    ceses: str = ""
    solo_plan: bool = False
    aplicar: bool = False
    depurar_duplicados: bool = False
    omitir_vigentes: bool = False
    reanudar: str = ""
    max_registros: int = 0
    dnis: list[str] = field(default_factory=list)
    hoy: date | None = None


def _logger(run_dir: Path) -> logging.Logger:
    logger = logging.getLogger("coordinador")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s | %(message)s")
    for handler in (logging.FileHandler(run_dir / "logs" / "coordinador.log", encoding="utf-8"), logging.StreamHandler()):
        handler.setFormatter(fmt)
        logger.addHandler(handler)
    return logger


def _avisar_lectura(log: logging.Logger, origen: str, lectura) -> None:
    for fila, valor in lectura.invalidos[:20]:
        log.warning("%s fila %s: documento inválido '%s' (se omite)", origen, fila, valor)
    for fila, texto in lectura.negocio_no_reconocido[:20]:
        log.warning("%s fila %s: razón social no reconocida '%s' (se aplica a JV y SELVA)", origen, fila, texto)
    resto = len(lectura.invalidos) + len(lectura.negocio_no_reconocido) - 40
    if resto > 0:
        log.warning("%s: %s avisos más omitidos del log", origen, resto)


def _preparar_corrida(settings: Settings, reanudar: str) -> tuple[str, Path]:
    run_id = reanudar or datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = settings.runs_dir / run_id
    if reanudar and not run_dir.exists():
        raise FileNotFoundError(f"No existe la corrida a reanudar: {run_dir}")
    for sub in ("logs", "manifest", "staging"):
        (run_dir / sub).mkdir(parents=True, exist_ok=True)
    corridas = sorted(p for p in settings.runs_dir.iterdir() if p.is_dir())
    for vieja in corridas[: max(0, len(corridas) - settings.max_corridas)]:
        if vieja != run_dir:
            shutil.rmtree(vieja, ignore_errors=True)
    return run_id, run_dir


def _consultar(plan: Plan, opciones: Opciones, settings: Settings, run_dir: Path, log: logging.Logger) -> None:
    hechos = manifest.terminados(run_dir / "manifest")
    ctx = mp.get_context("spawn")
    procesos: list = []
    colas: list = []  # mantiene vivo cada proxy: si se libera, el Manager borra la cola antes de que la usen los hijos
    with ctx.Manager() as manager:
        for negocio in opciones.negocios:
            pendientes = [i.dni for i in plan.consultas(negocio, opciones.omitir_vigentes)
                          if (negocio, i.dni) not in hechos]
            limite = opciones.max_registros or settings.max_registros
            if limite:
                pendientes = pendientes[:limite]
            if not pendientes:
                log.info("[%s] Sin DNIs pendientes de consulta", negocio)
                continue
            cola = manager.Queue()
            colas.append(cola)
            for dni in pendientes:
                cola.put(dni)
            n = min(settings.workers(negocio), len(pendientes))
            log.info("[%s] %s DNIs a consultar con %s worker(s)", negocio, len(pendientes), n)
            for i in range(1, n + 1):
                p = ctx.Process(target=worker_main, args=(negocio, i, cola, str(run_dir)), name=f"{negocio}-{i}")
                p.start()
                procesos.append(p)
        for p in procesos:
            p.join()
            if p.exitcode:
                log.warning("Worker %s terminó con código %s", p.name, p.exitcode)


def _almacen_o_none(settings: Settings, log: logging.Logger):
    """MinIO es un espejo: si no conecta, la corrida sigue solo con Drive (y se avisa)."""
    try:
        return crear_almacen(settings, log)
    except Exception as exc:
        log.error("MinIO no disponible (%s): se continúa solo con Drive; corra --sync-minio después", exc)
        return None


def _filtrar_indice(indice: IndiceDrive, negocios: tuple[str, ...]) -> IndiceDrive:
    out = IndiceDrive(ignorados=list(indice.ignorados))
    out.activos = {k: v for k, v in indice.activos.items() if k[0] in negocios}
    return out


def _cargar_drive(settings: Settings, log: logging.Logger, run_dir: Path, negocios: tuple[str, ...]):
    """Cliente + índices (activo y VENCIDOS) de Drive filtrados por negocio."""
    client = DriveClient(settings.drive_sa_key, settings.drive_oauth_token, log)
    log.info("Drive: credencial %s", client.modo)
    arbol, pdfs = cargar_arbol(client, settings.drive_root_id, cache=run_dir / "drive_listado.json")
    activos = _filtrar_indice(construir_indice(arbol, pdfs, settings.drive_carpeta_vencidos), negocios)
    vencidos = _filtrar_indice(construir_indice_vencidos(arbol, pdfs, settings.drive_carpeta_vencidos), negocios)
    log.info("Drive: %s PDFs activos y %s en VENCIDOS (%s)", activos.total(), vencidos.total(), ",".join(negocios))
    return client, arbol, activos, vencidos


def ejecutar_sync_minio(settings: Settings, opciones: Opciones) -> int:
    """Reconciliación MinIO <- Drive. La primera vez es la migración inicial."""
    run_id, run_dir = _preparar_corrida(settings, opciones.reanudar)
    log = _logger(run_dir)
    log.info("Corrida %s | sync-minio | negocios=%s | aplicar=%s", run_id, ",".join(opciones.negocios), opciones.aplicar)
    if not settings.minio_activo:
        log.error("MinIO desactivado: configure MINIO_ENABLED=1 y credenciales en .env")
        return 2
    almacen = crear_almacen(settings, log)
    client, _, activos, vencidos = _cargar_drive(settings, log, run_dir, opciones.negocios)
    existentes = filtrar_por_negocio(almacen.listar(settings.minio_prefix.strip("/") + "/"),
                                     settings.minio_prefix, opciones.negocios)
    log.info("MinIO: %s objetos existentes bajo %s", len(existentes), settings.minio_prefix)
    ops, sin_fecha = planificar_reconciliacion(activos, vencidos, existentes, settings.minio_prefix)
    log.info("Plan MinIO: SUBIR %s / ELIMINAR %s / sin fecha (no se migran) %s",
             sum(o.accion == "SUBIR" for o in ops), sum(o.accion == "ELIMINAR" for o in ops), len(sin_fecha))
    resultados = aplicar_reconciliacion(ops, almacen, client.descargar, opciones.aplicar, log, settings.drive_hilos)
    reporte = escribir_reporte_minio(run_dir / f"RB_GADSOCarnetMinIO_{run_id}.xlsx", resultados, sin_fecha,
                                     activos.ignorados)
    log.info("Reporte: %s", reporte)
    return 1 if any(e == "MINIO_ERROR" for _, e in resultados) else 0


def ejecutar_ceses(settings: Settings, opciones: Opciones) -> int:
    """Ceses: elimina en Drive y MinIO todos los carnés (activos y vencidos) de los DNIs del Excel.

    No abre SUCAMEC. Sin --aplicar es simulación.
    """
    run_id, run_dir = _preparar_corrida(settings, opciones.reanudar)
    log = _logger(run_dir)
    log.info("Corrida %s | ceses | negocios=%s | aplicar=%s", run_id, ",".join(opciones.negocios), opciones.aplicar)
    ruta = resolver_excel(opciones.ceses or settings.ceses_excel, BASE_DIR / "data" / "entrada", "CESES")
    if not ruta or not ruta.exists():
        log.error("Sin Excel de ceses: use --ceses <ruta>, CARNET_CESES_EXCEL o data/entrada/ceses*.xlsx")
        return 2
    lectura = leer_registros(ruta)
    _avisar_lectura(log, "Ceses", lectura)
    ceses = [(d, n) for d, n in lectura.registros if n is None or n in opciones.negocios]
    log.info("Ceses %s: %s registros (%s inválidos)", ruta.name, len(ceses), len(lectura.invalidos))

    # Un cese no puede dejar copias: si MinIO está activado pero no conecta, no se toca Drive.
    almacen = None
    if settings.minio_activo:
        try:
            almacen = crear_almacen(settings, log)
        except Exception as exc:
            log.error("MinIO activado pero no disponible (%s): ceses abortados sin tocar Drive", exc)
            return 4
    else:
        log.info("MinIO desactivado: ceses solo en Drive")

    # Los PDFs se MUEVEN a CESES/<dd.mm.aaaa> con la cuenta de servicio (sin Apps Script ni dueño);
    # esa carpeta se vacía después de golpe.
    client, arbol, activos, vencidos = _cargar_drive(settings, log, run_dir, opciones.negocios)
    operaciones, sin_pdf = operaciones_ceses(ceses, activos, vencidos)
    for dni, negocio in sin_pdf:
        log.info("[CESE] %s (%s): sin carnés en Drive", dni, negocio or "JV/SELVA")
    log.info("Ceses: %s PDFs a mover a CESES/, %s DNIs sin carné", len(operaciones), len(sin_pdf))

    resolver = ResolverCarpetas(arbol, client, settings.drive_carpeta_vencidos, crear=opciones.aplicar,
                                hoy=hoy_lima())
    sync = aplicar(operaciones, client, resolver, opciones.aplicar, log, registro=run_dir / "drive_hechas.txt",
                   hilos=settings.drive_hilos, almacen=almacen,
                   prefijo_minio=settings.minio_prefix, registro_minio=run_dir / "minio_hechas.txt")
    reporte = escribir_reporte(run_dir / f"RB_GADSOCarnetCeses_{run_id}.xlsx", Plan([], [], []), {}, sync,
                               activos.ignorados)
    resumen: dict[str, int] = {"Ceses sin carné": len(sin_pdf)}
    for s in sync:
        for clave in (f"{s.operacion.accion.value} {s.estado}", f"MinIO {s.minio}" if s.minio else ""):
            if clave:
                resumen[clave] = resumen.get(clave, 0) + 1
    log.info("Resumen: %s", resumen)
    log.info("Reporte: %s", reporte)
    columnas = [("Ceses en Excel", len(ceses)), ("Sin carné en Drive", len(sin_pdf)),
                ("Movidos a CESES", sum(1 for s in sync if s.operacion.accion is Accion.MOVER_A_CESES
                                        and s.estado in ("HECHO", "SIMULADO")))] + [
        c for c in columnas_corrida({}, sync) if c[0] in ("Errores Drive", "Errores MinIO")]
    enviar_resumen(run_id, "CESES DE CARNÉS", ",".join(opciones.negocios), columnas, [reporte],
                   not opciones.aplicar, log)
    return 1 if any(s.estado == "ERROR" or s.minio == "MINIO_ERROR" for s in sync) else 0


def ejecutar(settings: Settings, opciones: Opciones) -> int:
    run_id, run_dir = _preparar_corrida(settings, opciones.reanudar)
    log = _logger(run_dir)
    hoy = opciones.hoy or hoy_lima()
    log.info("Corrida %s | negocios=%s | aplicar=%s | solo_plan=%s", run_id, ",".join(opciones.negocios),
             opciones.aplicar, opciones.solo_plan)

    # 1. Entradas
    entrada = BASE_DIR / "data" / "entrada"
    ruta_padron = resolver_excel(opciones.padron or settings.padron_excel, entrada, "PADRON")
    ruta_bajas = resolver_excel(opciones.bajas or settings.bajas_excel, entrada, "BAJAS")
    padron: list[tuple[str, str]] = [(d, n) for d in opciones.dnis for n in opciones.negocios]
    if ruta_padron and not opciones.dnis:
        lectura = leer_registros(ruta_padron)
        padron = expandir_negocios(lectura.registros)
        log.info("Padrón %s: %s registros, %s inválidos, %s sin negocio", ruta_padron.name,
                 len(lectura.registros), len(lectura.invalidos), lectura.sin_negocio)
        _avisar_lectura(log, "Padrón", lectura)
    bajas = []
    if ruta_bajas:
        lectura_bajas = leer_registros(ruta_bajas)
        bajas = lectura_bajas.registros
        _avisar_lectura(log, "Bajas", lectura_bajas)
    log.info("Bajas/renuncias: %s (%s)", len(bajas), ruta_bajas.name if ruta_bajas else "sin archivo")
    padron = [(d, n) for d, n in padron if n in opciones.negocios]
    if not padron and not bajas:
        log.error("Sin padrón ni bajas: coloque los Excel en %s o use --padron/--bajas/--dni", entrada)
        return 2

    # 2. Índice de Drive y plan
    client = DriveClient(settings.drive_sa_key, settings.drive_oauth_token, log)
    log.info("Drive: credencial %s", client.modo)
    if opciones.aplicar:
        motivo = client.verificar_escritura(settings.drive_root_id)
        if motivo:
            log.error("No se puede aplicar en Drive: %s. Corra sin --aplicar o autorice con scripts/autorizar_drive.py", motivo)
            return 3
    # El listado se congela por corrida: al reanudar se decide sobre el mismo Drive que se planificó.
    arbol, pdfs = cargar_arbol(client, settings.drive_root_id, cache=run_dir / "drive_listado.json")
    indice = construir_indice(arbol, pdfs, settings.drive_carpeta_vencidos)
    log.info("Drive: %s PDFs activos indexados (%s DNIs), %s no reconocidos", indice.total(),
             len(indice.activos), len(indice.ignorados))
    plan = construir_plan(padron, bajas, indice, hoy)
    log.info("Plan: %s", plan.resumen())

    # 3. Consulta SUCAMEC
    if not opciones.solo_plan and plan.consultas():
        _consultar(plan, opciones, settings, run_dir, log)
    resultados = manifest.leer(run_dir / "manifest")

    # 4. Decisiones y sincronización
    operaciones: list[Operacion] = []
    for item in plan.items:
        if item.consultar:
            operaciones += decidir_sync(item, resultados.get((item.negocio, item.dni)), hoy)
    operaciones += [Operacion(Accion.ELIMINAR, c.negocio, c.dni, c.fecha_emision, "baja/renuncia", file_id=c.file_id)
                    for c in plan.eliminar]
    if opciones.depurar_duplicados:
        operaciones += [Operacion(Accion.MOVER_A_VENCIDOS, c.negocio, c.dni, c.fecha_emision,
                                  "duplicado anterior", file_id=c.file_id)
                        for c in plan.duplicados if c.fecha_emision]
    resolver = ResolverCarpetas(arbol, client, settings.drive_carpeta_vencidos, crear=opciones.aplicar)
    almacen = _almacen_o_none(settings, log)
    sync = aplicar(operaciones, client, resolver, opciones.aplicar, log, registro=run_dir / "drive_hechas.txt",
                   hilos=settings.drive_hilos, almacen=almacen, prefijo_minio=settings.minio_prefix,
                   registro_minio=run_dir / "minio_hechas.txt")

    # 5. Reporte y correo
    reporte = escribir_reporte(run_dir / f"RB_GADSOCarnetSUCAMEC_{run_id}.xlsx", plan, resultados, sync, indice.ignorados)
    resumen: dict[str, int] = {}
    for s in sync:
        clave = f"{s.operacion.accion.value} {s.estado}"
        resumen[clave] = resumen.get(clave, 0) + 1
    for r in resultados.values():
        clave = f"Consulta {r.estado.value}"
        resumen[clave] = resumen.get(clave, 0) + 1
    log.info("Resumen: %s", resumen)
    log.info("Reporte: %s", reporte)
    enviar_resumen(run_id, "CARNÉS (BANDEJA DE IMPRESIÓN)", ",".join(opciones.negocios),
                   columnas_corrida(resultados, sync), [reporte], not opciones.aplicar, log)

    errores = sum(1 for r in resultados.values() if r.estado is Estado.ERROR) + sum(1 for s in sync if s.estado == "ERROR")
    return 1 if errores else 0
