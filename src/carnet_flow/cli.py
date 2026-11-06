from __future__ import annotations

import argparse
import logging
import sys

from .config import credentials_for, load_settings
from .domain import NEGOCIOS, normalizar_dni


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="carnet_flow", description="Carnés SUCAMEC -> Google Drive")
    p.add_argument("--grupo", choices=("JV", "SELVA", "TODOS"), default="TODOS")
    p.add_argument("--padron", default="", help="Excel del padrón (por defecto data/entrada/padron*.xlsx)")
    p.add_argument("--bajas", default="", help="Excel de bajas/renuncias (por defecto data/entrada/bajas*.xlsx)")
    p.add_argument("--dni", action="append", default=[], help="Consultar solo estos DNIs (repetible)")
    p.add_argument("--solo-plan", action="store_true", help="Solo índice de Drive + plan; no abre SUCAMEC")
    p.add_argument("--solo-login", action="store_true", help="Prueba login y navegación a la bandeja")
    p.add_argument("--aplicar", action="store_true", help="Escribe en Drive (sin esto, simulación)")
    p.add_argument("--depurar-duplicados", action="store_true", help="Mueve PDFs duplicados anteriores a Vencidos")
    p.add_argument("--omitir-vigentes", action="store_true",
                   help="No consultar DNIs con carné < 3 años en Drive (más rápido; no detecta renovaciones anticipadas)")
    p.add_argument("--solo-ceses", action="store_true",
                   help="Elimina en Drive y MinIO todos los carnés de los DNIs del Excel de ceses; no abre SUCAMEC")
    p.add_argument("--ceses", default="", help="Excel de ceses (por defecto data/entrada/ceses*.xlsx)")
    p.add_argument("--sync-minio", action="store_true",
                   help="Reconcilia MinIO contra Drive (migración inicial); no abre SUCAMEC")
    p.add_argument("--reanudar", default="", help="ID de corrida a retomar (corridas/<id>)")
    p.add_argument("--max", type=int, default=0, help="Máximo de DNIs a consultar por negocio")
    return p


def _solo_login(negocios: tuple[str, ...]) -> int:
    from playwright.sync_api import sync_playwright

    from .impresion_flow.navigation import navigate_to_bandeja_impresion
    from .login_flow.auth import login
    from .login_flow.browser import close_browser, open_browser

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")
    settings = load_settings()
    codigo = 0
    with sync_playwright() as pw:
        for negocio in negocios:
            log = logging.getLogger(f"login_{negocio}")
            browser = context = None
            try:
                browser, context, page = open_browser(pw, settings)
                login(page, settings, credentials_for(negocio), negocio, log)
                navigate_to_bandeja_impresion(page, log)
                log.info("[%s] OK: login y bandeja de impresión", negocio)
            except Exception as exc:
                log.error("[%s] FALLÓ: %s", negocio, exc)
                codigo = 1
            finally:
                close_browser(browser, context, log)
    return codigo


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    negocios = NEGOCIOS if args.grupo == "TODOS" else (args.grupo,)
    if args.solo_login:
        return _solo_login(negocios)

    from .orchestration_flow.runner import Opciones, ejecutar, ejecutar_ceses, ejecutar_sync_minio

    if args.solo_ceses:
        return ejecutar_ceses(load_settings(), Opciones(negocios=negocios, ceses=args.ceses, aplicar=args.aplicar,
                                                        reanudar=args.reanudar))
    if args.sync_minio:
        return ejecutar_sync_minio(load_settings(), Opciones(negocios=negocios, aplicar=args.aplicar,
                                                             reanudar=args.reanudar))
    dnis = []
    for valor in args.dni:
        dni = normalizar_dni(valor)
        if not dni:
            print(f"DNI inválido: {valor}", file=sys.stderr)
            return 2
        dnis.append(dni)
    opciones = Opciones(
        negocios=negocios, padron=args.padron, bajas=args.bajas, solo_plan=args.solo_plan,
        aplicar=args.aplicar, depurar_duplicados=args.depurar_duplicados, reanudar=args.reanudar,
        omitir_vigentes=args.omitir_vigentes,
        max_registros=max(0, args.max), dnis=dnis,
    )
    return ejecutar(load_settings(), opciones)


if __name__ == "__main__":
    sys.exit(main())
