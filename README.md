# CARNET-SUCAMEC-GADSO

Mantiene en Google Drive (`CARNÉS ELECTRONICO`) el PDF del carné SUCAMEC de cada vigilante de
J&V RESGUARDO y SELVA: descarga los nuevos y los renovados desde DSSP > CARNÉ > BANDEJA DE
IMPRESIÓN, mueve a `VENCIDOS` los de 3 años o más y elimina los de bajas o renuncias.
Spec: [docs/spec-carnet-sucamec.md](docs/spec-carnet-sucamec.md).

## Instalación

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium
```

`.env` (no versionado): credenciales SUCAMEC `TIPO_DOC`, `NUMERO_DOCUMENTO`, `USUARIO_SEL`,
`CLAVE_SEL` y sus equivalentes `SELVA_*`; Graph `MS_GRAPH_*`; la carpeta raíz `CARNET_DRIVE_ROOT_ID` (también como propiedad `ROOT_ID` del Apps Script); y opcionalmente `CARNET_*` (ver `src/carnet_flow/config.py`).
`CARNET_DRIVE_HILOS` (por defecto 8): subidas simultáneas a Drive. Medido el 27/10/2026: 8 hilos sostienen
~1,5 subidas/s sin errores; con 16 aparecen cortes de conexión de la red local (no límites de Google).
`secrets/`: llave de la cuenta de servicio y, para escribir en Drive, `drive_oauth_token.json` (ver `autorizar_drive.py`).

## Entradas

`data/entrada/padron*.xlsx` y `data/entrada/bajas*.xlsx` (se toma el más reciente), o `--padron` / `--bajas`.
Columnas: `DNI` (o `NRO DOCUMENTO`) y `RAZON SOCIAL` (o `NEGOCIO`). Una fila sin negocio aplica a ambos.

## Ejecución

```bat
run_carnet.bat --solo-login                 :: prueba login + bandeja (JV y SELVA)
run_carnet.bat --solo-plan                  :: índice de Drive + plan, sin SUCAMEC
run_carnet.bat                              :: corrida completa en SIMULACIÓN (no escribe en Drive)
run_carnet.bat --aplicar                    :: corrida completa aplicando cambios en Drive
run_carnet_aplicar.bat                      :: lo mismo, para el usuario final (doble clic; deja la ventana abierta al terminar)
run_carnet.bat --grupo JV --dni 12345678    :: solo esos DNIs
run_carnet.bat --reanudar 20261021_134001   :: retoma una corrida cortada
```

Cada corrida queda en `corridas/<id>/`: `logs/`, `manifest/` (checkpoint por DNI), `staging/` (PDFs),
`drive_listado.json`, `drive_hechas.txt` y el reporte `RB_GADSOCarnetSUCAMEC_<id>.xlsx`.

Programador de tareas (ejemplo, diario 06:00):

```powershell
schtasks /Create /TN "CARNET-SUCAMEC-GADSO" /SC DAILY /ST 06:00 /TR "\"C:\Users\fserrano\Desktop\BOTS-SUCAMEC\carnet_sucamec_gadso\run_carnet.bat\" --aplicar"
```

## MinIO (copia para GADSO)

Drive sigue siendo la base: MinIO solo guarda una copia para que GADSO la consulte y nunca se usa para decidir.
Se activa con `MINIO_ENABLED=1` y las credenciales en `.env` (ver `.env.example`). Con `MINIO_ENABLED=0` el bot
funciona exactamente igual que antes.

Rutas (bucket `mga-gadso`):

```
sucamec/carnets/<JV|SELVA>/<aaaa>/<mm>/<dd>/<DNI>_<dd.mm.aaaa>.pdf
sucamec/carnets/vencidos/<JV|SELVA>/<aaaa>/<mm>/<dd>/<DNI>_<dd.mm.aaaa>.pdf
```

- En cada corrida, toda operación aplicada en Drive (subir, pasar a vencidos, eliminar) se replica en MinIO.
  Si MinIO falla, Drive sigue y el reporte marca `MINIO_ERROR` en la columna `MinIO`.
- `--sync-minio` deja MinIO igual a Drive (activos + VENCIDOS): la primera vez es la migración inicial;
  después repara diferencias. Nunca toca nada fuera de `sucamec/carnets/`. Los PDFs sin fecha legible en Drive
  no se migran y salen en el reporte `RB_GADSOCarnetMinIO_<id>.xlsx`. Conviene sanear Drive antes.

## Ceses

`--solo-ceses` elimina en Drive y MinIO **todos** los carnés (vigentes y vencidos) de los DNIs del Excel de
ceses, sin abrir SUCAMEC. Excel: `--ceses <ruta>`, `CARNET_CESES_EXCEL` o el más reciente
`data/entrada/ceses*.xlsx`, con las mismas columnas que el padrón (plantilla `data/plantillas/ceses_PLANTILLA.xlsx`).
Una fila sin negocio aplica a los negocios de `--grupo`. Reporte: `RB_GADSOCarnetCeses_<id>.xlsx`.

```bat
run_carnet.bat --sync-minio --grupo JV               :: simulación de la migración/reconciliación
run_carnet.bat --sync-minio --grupo JV --aplicar     :: escribe en MinIO
run_carnet.bat --solo-ceses                          :: simulación de ceses
run_carnet.bat --solo-ceses --aplicar                :: elimina en Drive (papelera) y MinIO (definitivo)
```

## Pruebas

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```
