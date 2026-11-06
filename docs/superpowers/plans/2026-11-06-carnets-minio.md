# Carnés SUCAMEC en MinIO + ceses — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replicar en MinIO (`mga-gadso/sucamec/carnets/`) los carnés que el bot gestiona en Drive, migrar lo existente y agregar un subflujo de ceses que borra en ambos.

**Architecture:** Drive sigue siendo la base de decisión. Un módulo nuevo `minio_sync/` calcula las claves a partir de (negocio, DNI, fecha) y replica cada operación ya aplicada en Drive. `--sync-minio` reconcilia MinIO contra Drive (activos + VENCIDOS). `--solo-ceses` elimina en Drive y MinIO sin consultar SUCAMEC.

**Tech Stack:** Python 3, librería `minio` (la misma de GADSO), google-api-python-client, openpyxl, pytest.

**Spec:** `docs/superpowers/specs/2026-11-06-carnets-minio-design.md`

## Global Constraints

- Bucket `mga-gadso`, prefijo `sucamec/carnets` (configurable con `MINIO_PREFIX`).
- Clave activa: `<prefijo>/<NEG>/<aaaa>/<mm>/<dd>/<DNI>_<dd.mm.aaaa>.pdf`; clave vencida: `<prefijo>/vencidos/<NEG>/<aaaa>/<mm>/<dd>/<DNI>_<dd.mm.aaaa>.pdf`; `<NEG>` ∈ {`JV`,`SELVA`}; nombre = `domain.nombre_pdf`.
- Metadatos: `dni`, `negocio`, `fecha-emision` (ISO), `origen=carnet_sucamec_gadso`.
- Drive es la base: MinIO nunca se lee para decidir el plan; ante diferencia gana Drive.
- Falla de MinIO no aborta Drive ni el resto de operaciones; se reporta `MINIO_ERROR`.
- Sin `--aplicar` = simulación (no escribe en Drive ni en MinIO).
- `MINIO_ENABLED=0` o credenciales vacías ⇒ comportamiento idéntico al actual.
- Credenciales solo en `.env` (no versionado). Comentarios/logs en español.
- Tests sin red: MinIO y Drive se simulan con dobles en memoria.

## Review Focus

- Carné de Drive **sin fecha de emisión** (carpeta irregular): no tiene clave MinIO; replicación y reconciliación deben omitirlo (estado `OMITIDO`), nunca lanzar.
- **Dos PDFs del mismo DNI y fecha** en Drive (duplicados): colisionan en la misma clave; la reconciliación no debe borrar/re-subir en bucle; se sube una vez.
- **`MOVER_A_VENCIDOS` sin objeto activo en MinIO** (aún no migrado): se sube a vencidos descargando desde Drive, sin error.
- **Ceses con DNI que no existe** en Drive: no genera operaciones ni errores; se informa en el log.
- **Reconciliación nunca borra fuera de `<prefijo>/`**: solo lista y elimina bajo el prefijo configurado.

---

### Task 1: Configuración y cálculo de claves

**Files:**
- Create: `src/carnet_flow/minio_sync/__init__.py`, `src/carnet_flow/minio_sync/claves.py`
- Modify: `src/carnet_flow/config.py` (Settings + load_settings), `requirements.txt` (+`minio`), `.env.example` (bloque MinIO + `CARNET_CESES_EXCEL`)
- Test: `tests/test_minio_claves.py`

**Interfaces:**
- Produces: `clave_minio(prefijo: str, negocio: str, dni: str, fecha: date, vencido: bool) -> str`; `metadatos(negocio: str, dni: str, fecha: date) -> dict[str, str]`; Settings: `minio_enabled: bool, minio_endpoint: str, minio_access_key: str, minio_secret_key: str, minio_bucket: str, minio_use_ssl: bool, minio_prefix: str, ceses_excel: str` y propiedad `minio_activo -> bool` (enabled y endpoint/keys no vacíos).

- [ ] **Step 1: Write the failing test**
```python
def test_clave_activa():
    assert clave_minio("sucamec/carnets", "JV", "12345678", date(2026, 11, 6), False) == \
        "sucamec/carnets/JV/2026/11/06/12345678_06.11.2026.pdf"
def test_clave_vencida_selva():
    assert clave_minio("sucamec/carnets/", "SELVA", "00123456", date(2023, 1, 9), True) == \
        "sucamec/carnets/vencidos/SELVA/2023/01/09/00123456_09.01.2023.pdf"
def test_metadatos():
    assert metadatos("JV", "12345678", date(2026, 11, 6)) == {
        "dni": "12345678", "negocio": "JV", "fecha-emision": "2026-11-06", "origen": "carnet_sucamec_gadso"}
```
- [ ] **Step 2:** `.venv\Scripts\python.exe -m pytest tests/test_minio_claves.py -v` → FAIL (módulo inexistente).
- [ ] **Step 3:** Implementar `claves.py` (normaliza barra final del prefijo; usa `nombre_pdf`). Añadir campos a Settings con `MINIO_ENABLED` (default 0), `MINIO_BUCKET` (default `mga-gadso`), `MINIO_USE_SSL` (default 1), `MINIO_PREFIX` (default `sucamec/carnets`), `CARNET_CESES_EXCEL`.
- [ ] **Step 4:** pytest de nuevo → PASS; correr suite completa `pytest -q` → todo PASS.
- [ ] **Step 5:** Commit `feat(minio): configuracion y claves de carnets en MinIO`.

### Task 2: Cliente de almacenamiento MinIO

**Files:**
- Create: `src/carnet_flow/minio_sync/store.py`
- Test: `tests/test_minio_store.py`

**Interfaces:**
- Produces: `Protocol AlmacenCarnets` con `subir(clave: str, data: bytes, meta: dict[str,str]) -> None`, `copiar(origen: str, destino: str) -> None`, `eliminar(clave: str) -> None` (idempotente), `listar(prefijo: str) -> set[str]`; `MinioCarnets(settings, logger)` que lo implementa con `minio.Minio` (`put_object` con `content_type="application/pdf"`, `copy_object` con `CopySource`, `remove_object`, `list_objects(recursive=True)`); `AlmacenMemoria` (doble en memoria, dict clave→(bytes, meta)) en el mismo módulo para tests; `crear_almacen(settings, logger) -> AlmacenCarnets | None` (None si `not settings.minio_activo`).

- [ ] **Step 1: Write the failing test** — sobre `AlmacenMemoria`: `subir`+`listar` devuelve la clave; `copiar` conserva bytes y meta; `eliminar` de clave inexistente no lanza; `listar("a/")` no devuelve claves de `b/`. Y `crear_almacen` con `minio_enabled=False` → `None`.
- [ ] **Step 2:** pytest → FAIL.
- [ ] **Step 3:** Implementar. `MinioCarnets` no crea el bucket (ya existe en GADSO); si no existe, lanza error claro al construir.
- [ ] **Step 4:** pytest → PASS.
- [ ] **Step 5:** Commit `feat(minio): cliente de almacenamiento de carnets`.

### Task 3: Drive — descarga e índice de VENCIDOS

**Files:**
- Modify: `src/carnet_flow/drive_sync/client.py` (método nuevo), `src/carnet_flow/drive_sync/index.py` (función nueva)
- Test: `tests/test_drive_y_fuentes.py` (añadir casos)

**Interfaces:**
- Produces: `DriveClient.descargar(file_id: str) -> bytes` (`files().get_media` + `MediaIoBaseDownload`, mismo manejo de reintentos/permisos que el resto); `construir_indice_vencidos(arbol: Arbol, pdfs: list[Item], carpeta_vencidos: str) -> IndiceDrive` — indexa SOLO lo que cuelga de `<raíz>/VENCIDOS/<negocio>/...` con la misma lectura de negocio/DNI/fecha que `construir_indice` (extraer la lectura común a un helper privado; `construir_indice` no cambia de comportamiento).

- [ ] **Step 1: Write the failing test** — árbol falso con `J&V RESGUARDO SAC/2026/11.NOVIEMBRE/06.11.2026/111.pdf` y `VENCIDOS/J&V RESGUARDO SAC/2023/10.OCTUBRE/23.10.2023/222.pdf` (DNIs de 8 dígitos): `construir_indice` contiene solo el activo (regresión) y `construir_indice_vencidos` solo el vencido con `fecha_emision == date(2023,10,23)` y negocio `JV`.
- [ ] **Step 2:** pytest → FAIL.
- [ ] **Step 3:** Implementar.
- [ ] **Step 4:** pytest completo → PASS.
- [ ] **Step 5:** Commit `feat(drive): descarga de PDFs e indice de vencidos`.

### Task 4: Espejo en línea durante la corrida

**Files:**
- Create: `src/carnet_flow/minio_sync/espejo.py`
- Modify: `src/carnet_flow/drive_sync/sync.py` (`ResultadoSync` + hook), `src/carnet_flow/orchestration_flow/runner.py` (crear almacén y pasarlo), `src/carnet_flow/reporting.py` (columna `MINIO`)
- Test: `tests/test_minio_espejo.py`

**Interfaces:**
- Consumes: `clave_minio`, `metadatos` (T1); `AlmacenCarnets`, `AlmacenMemoria` (T2); `DriveClient.descargar` (T3).
- Produces: `replicar(op: Operacion, almacen: AlmacenCarnets, prefijo: str, descargar: Callable[[str], bytes]) -> str` que devuelve `HECHO | OMITIDO`, y lanza ante fallo de almacén; `ResultadoSync.minio: str = ""` (`HECHO|SIMULADO|MINIO_ERROR|OMITIDO|""`); `aplicar(..., almacen: AlmacenCarnets | None = None, prefijo_minio: str = "", registro_minio: Path | None = None)`.

Reglas de `replicar` (de la spec):
- `SUBIR` / `SUBIR_A_VENCIDOS`: `subir(clave(vencido=accion is SUBIR_A_VENCIDOS), bytes de op.pdf, metadatos)`.
- `MOVER_A_VENCIDOS`: si la clave activa está en `listar(clave activa)`, `copiar` a la vencida y `eliminar` la activa; si no, `subir` a la vencida con `descargar(op.file_id)`.
- `ELIMINAR`: `eliminar(clave activa)` (espejo exacto de lo borrado en Drive). En ceses (T6) se pasa una op por cada PDF, activo o vencido, ver allí.
- `op.fecha_emision is None` o `NINGUNA` → `OMITIDO`.

En `aplicar`: tras `HECHO` en Drive, si hay almacén → `replicar` dentro de try; excepción → `minio="MINIO_ERROR"` y log warning, Drive queda `HECHO`. Si `not aplicar_cambios` y hay almacén → `minio="SIMULADO"`. Registro de reanudación `minio_hechas.txt` con la misma `_clave(op)`. Una op ya en `drive_hechas.txt` pero no en `minio_hechas.txt` se replica igual al reanudar.

- [ ] **Step 1: Write the failing tests**
  - `test_subir_activo`: op SUBIR JV 12345678 2026-11-06 con PDF temporal → clave activa existe con meta correcta.
  - `test_mover_con_activo_existente`: activo presente → queda solo la vencida, mismos bytes.
  - `test_mover_sin_activo_descarga_drive`: almacén vacío, `descargar=lambda fid: b"PDF"` → vencida con `b"PDF"`.
  - `test_eliminar_idempotente`: ELIMINAR sin objeto → `HECHO`, sin excepción.
  - `test_sin_fecha_omitido`: MOVER con `fecha_emision=None` → `OMITIDO`.
  - `test_aplicar_minio_falla_drive_hecho`: almacén cuyo `subir` lanza; con un `DriveClient` falso → `ResultadoSync.estado == "HECHO"` y `.minio == "MINIO_ERROR"`.
  - `test_aplicar_simulacion`: `aplicar_cambios=False` → almacén sin escrituras, `.minio == "SIMULADO"`.
- [ ] **Step 2:** pytest → FAIL.
- [ ] **Step 3:** Implementar `espejo.py`, hook en `sync.aplicar`, en `runner.ejecutar` crear `almacen = crear_almacen(settings, log)` (log informativo si está desactivado) y pasarlo con `prefijo_minio=settings.minio_prefix`, `registro_minio=run_dir/"minio_hechas.txt"`; añadir columna `MINIO` en la hoja de operaciones del reporte.
- [ ] **Step 4:** pytest completo → PASS.
- [ ] **Step 5:** Commit `feat(minio): replica en MinIO cada operacion aplicada en Drive`.

### Task 5: Reconciliación / migración (`--sync-minio`)

**Files:**
- Create: `src/carnet_flow/minio_sync/reconciliar.py`
- Modify: `src/carnet_flow/cli.py` (flag), `src/carnet_flow/orchestration_flow/runner.py` (`ejecutar_sync_minio(settings, opciones) -> int`)
- Test: `tests/test_minio_reconciliar.py`

**Interfaces:**
- Consumes: T1, T2, T3 (`construir_indice`, `construir_indice_vencidos`, `descargar`).
- Produces: `@dataclass(frozen=True) OpMinio(accion: Literal["SUBIR","ELIMINAR"], clave: str, file_id: str = "", negocio: str = "", dni: str = "", fecha: date | None = None)`; `planificar_reconciliacion(activos: IndiceDrive, vencidos: IndiceDrive, existentes: set[str], prefijo: str) -> tuple[list[OpMinio], list[CarneDrive]]` (ops, sin_fecha); `aplicar_reconciliacion(ops, almacen, descargar, aplicar_cambios, logger, hilos) -> list[tuple[OpMinio, str]]`.

Algoritmo: claves deseadas = cada `CarneDrive` con fecha → `clave_minio(..., vencido=<viene de índice vencidos>)` (deduplicar por clave, conservar el primero); `SUBIR` = deseadas − existentes; `ELIMINAR` = existentes − deseadas, solo claves que empiezan por `prefijo/`. Los "mal ubicados" se resuelven solos (subir nueva + eliminar vieja). Carnés sin fecha → lista `sin_fecha` al reporte.

`ejecutar_sync_minio`: exige `settings.minio_activo` (si no, error y código 2); carga árbol Drive (sin caché de corrida), arma ambos índices, `almacen.listar(prefijo)`, planifica, aplica (o simula), escribe reporte Excel en la carpeta de corrida (hojas: operaciones, sin_fecha, ignorados) y log con resumen `SUBIR n / ELIMINAR n / sin_fecha n`. CLI: `--sync-minio` (combinable con `--aplicar` y `--grupo`, que filtra por negocio).

- [ ] **Step 1: Write the failing tests**
  - `test_migracion_inicial`: 1 activo + 1 vencido, MinIO vacío → 2 SUBIR con claves activa/vencida correctas.
  - `test_idempotente`: existentes == deseadas → `[]`.
  - `test_mal_ubicado`: activo en Drive pero existe solo en `vencidos/` → SUBIR activa + ELIMINAR vencida.
  - `test_huerfano`: clave en MinIO sin PDF en Drive → ELIMINAR.
  - `test_no_toca_fuera_de_prefijo`: existente `sucamec/tramites/x.pdf` → ninguna op.
  - `test_duplicado_misma_clave`: dos `CarneDrive` mismo DNI/fecha → un solo SUBIR.
  - `test_sin_fecha`: carné con `fecha_emision=None` → no op, aparece en `sin_fecha`.
- [ ] **Step 2:** pytest → FAIL.
- [ ] **Step 3:** Implementar.
- [ ] **Step 4:** pytest completo → PASS.
- [ ] **Step 5:** Commit `feat(minio): reconciliacion y migracion inicial --sync-minio`.

### Task 6: Subflujo de ceses (`--solo-ceses`)

**Files:**
- Modify: `src/carnet_flow/domain.py` (función nueva), `src/carnet_flow/cli.py` (flags `--solo-ceses`, `--ceses`), `src/carnet_flow/orchestration_flow/runner.py` (`ejecutar_ceses(settings, opciones) -> int`)
- Create: `data/plantillas/ceses_PLANTILLA.xlsx` (copia de `bajas_PLANTILLA.xlsx`)
- Test: `tests/test_ceses.py`

**Interfaces:**
- Consumes: `leer_registros`, `resolver_excel` (prefijo `"CESES"`), T3 índices, T4 `aplicar(..., almacen=...)`.
- Produces: `operaciones_ceses(ceses: list[tuple[str, str | None]], activos: IndiceDrive, vencidos: IndiceDrive) -> tuple[list[Operacion], list[tuple[str, str | None]]]` → (ops `ELIMINAR` por cada PDF activo y vencido del DNI/negocio, con `motivo="cese"`; ceses sin PDF). Fila sin negocio aplica a JV y SELVA.

Para que el espejo borre la clave correcta de un PDF vencido, `Operacion` necesita saber si viene de VENCIDOS: añadir campo `vencido: bool = False` a `Operacion` (default mantiene todo lo existente) y en `replicar` usar `clave_minio(..., vencido=op.vencido)` para `ELIMINAR`.

`ejecutar_ceses`: resuelve Excel (`--ceses` > `CARNET_CESES_EXCEL` > `data/entrada/ceses*.xlsx`; si no hay → error código 2), carga Drive, índices, `operaciones_ceses`, `aplicar` con almacén, reporte Excel `RB_GADSOCarnetCeses_<run_id>.xlsx`, log de ceses sin PDF, y `enviar_resumen` como la corrida normal. No abre navegador.

- [ ] **Step 1: Write the failing tests**
  - `test_cese_borra_activos_y_vencidos`: DNI con 1 activo + 1 vencido JV → 2 ELIMINAR, uno con `vencido=True`.
  - `test_cese_sin_negocio_ambos`: fila sin negocio, PDFs en JV y SELVA → borra en ambos.
  - `test_cese_dni_inexistente`: → 0 ops, 1 en sin PDF.
  - `test_replicar_eliminar_vencido`: op ELIMINAR `vencido=True` → borra la clave `vencidos/...` en `AlmacenMemoria`.
- [ ] **Step 2:** pytest → FAIL.
- [ ] **Step 3:** Implementar.
- [ ] **Step 4:** pytest completo → PASS.
- [ ] **Step 5:** Commit `feat(ceses): subflujo --solo-ceses en Drive y MinIO`.

### Task 7: Verificación real y documentación

**Files:**
- Modify: `README.md` (sección MinIO: rutas, flags `--sync-minio`, `--solo-ceses`, variables)

- [ ] **Step 1:** Con credenciales reales en `.env`: `run_carnet.bat --sync-minio --grupo JV` (simulación) → revisar reporte: conteos coherentes con Drive, 0 claves fuera de `sucamec/carnets/`.
- [ ] **Step 2:** **Pedir confirmación al usuario** antes de escribir en MinIO; luego `--sync-minio --grupo JV --aplicar` y volver a correr la simulación → `SUBIR 0 / ELIMINAR 0` (idempotencia).
- [ ] **Step 3:** `--solo-ceses` en simulación con un Excel de 1–2 DNIs de prueba → revisar operaciones en el reporte (no aplicar sin confirmación del usuario).
- [ ] **Step 4:** Actualizar README; commit `docs: carnets en MinIO y subflujo de ceses`; push a `origin main` previa confirmación.
