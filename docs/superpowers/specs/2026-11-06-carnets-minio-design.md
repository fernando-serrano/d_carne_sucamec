# Carnés SUCAMEC en MinIO (espejo de Drive) + subflujo de ceses

Fecha: 2026-11-06 · Repo: `carnet_sucamec_gadso` (`d_carne_sucamec`)

## Intención

La plataforma **GADSO** debe mostrar los carnés SUCAMEC desde MinIO, el almacenamiento
que ya usa (bucket `mga-gadso`, un prefijo por módulo). El bot ya descarga los carnés de
la Bandeja de Impresión y los organiza en Google Drive. **Drive se mantiene en
paralelo**: es el repositorio operativo que hoy consulta el equipo.

## Objetivos

1. **Subida a MinIO** de cada carné procesado, con la misma lógica de negocio de hoy
   (vigencia de 3 años, renovaciones, vencidos y bajas).
2. **Migración ordenada**: cargar a MinIO, con la estructura limpia, todo lo que ya
   está en Drive.
3. **Subflujo de ceses**: a partir de un Excel con los mismos campos de ingesta que el
   padrón, eliminar los carnés de esos DNIs en Drive **y** en MinIO, sin consultar SUCAMEC.

## No objetivos

- Cambiar GADSO para que lea los carnés: eso es otro trabajo. Aquí solo se deja el
  almacenamiento listo y documentado.
- Dejar de usar Drive o cambiar su estructura actual.
- Cambiar la lógica de decisión de `domain.py`.

## Anti-objetivos (sería un fracaso aunque “funcione”)

- Que MinIO y Drive diverjan sin que nadie se entere.
- Que un cese deje copias del carné en algún almacenamiento, vigente o vencido.
- Que una caída de MinIO aborte la sincronización con Drive (ni al revés).
- Subir credenciales de MinIO al repositorio.

## Estructura en MinIO (decidida con el usuario)

Bucket `mga-gadso`, prefijo `sucamec/carnets/`, que convive con los ya existentes
`sucamec/tramites/` y `sucamec/contenido/` de GADSO.

```
sucamec/carnets/<NEG>/<aaaa>/<mm>/<dd>/<DNI>_<dd.mm.aaaa>.pdf
sucamec/carnets/vencidos/<NEG>/<aaaa>/<mm>/<dd>/<DNI>_<dd.mm.aaaa>.pdf
```
- `<NEG>` = `JV` | `SELVA`. La fecha es la **fecha de emisión**.
- El nombre del archivo es el mismo de Drive (`domain.nombre_pdf`).
- Cada objeto lleva metadatos `x-amz-meta-dni`, `-negocio`, `-fecha-emision` y `-origen=carnet_sucamec_gadso`.

## Diseño

### Fuente de verdad: Drive; MinIO = espejo de solo almacenamiento
Drive sigue siendo **la base**. El plan (`construir_plan`) se decide sobre el índice de
Drive, como hoy. MinIO solo **guarda una copia** para que GADSO la consulte: nunca se lee
para decidir nada, y su contenido debe ser siempre exactamente lo que hay en Drive. Ante
cualquier diferencia, gana Drive.

### 1. Espejo en línea (cada corrida)
En `drive_sync.aplicar`, cuando una operación queda `HECHO` en Drive, se replica en MinIO:

| Acción Drive | MinIO |
|---|---|
| `SUBIR` | `put` en la clave activa |
| `SUBIR_A_VENCIDOS` | `put` en la clave de `vencidos/` |
| `MOVER_A_VENCIDOS` | `copy` de la clave activa a la de vencidos y luego `remove` de la activa; si no existía la activa, se sube el PDF desde Drive |
| `ELIMINAR` | `remove` de la clave que corresponde al mismo PDF borrado en Drive |

- La replicación es independiente: si MinIO falla, la operación de Drive sigue `HECHO`
  y el espejo se marca `MINIO_ERROR` en el reporte. La próxima reconciliación (punto 2)
  lo corrige.
- Con `--aplicar` desactivado (simulación), MinIO tampoco se escribe; solo se reporta.
- La replicación se reanuda con el mismo registro de la corrida que ya usa Drive
  (`minio_hechas.txt`), para no repetir subidas.
- Para `MOVER_A_VENCIDOS` y `ELIMINAR` hace falta la clave MinIO del archivo. Se deduce
  de (negocio, DNI, fecha de emisión), sin consultar MinIO.

### 2. Reconciliación / migración ordenada (`--sync-minio`)
Comando nuevo, sin SUCAMEC:
1. Lista Drive (índice existente) y lista `sucamec/carnets/` en MinIO.
2. Calcula la clave canónica de cada PDF de Drive: activo o vencido según su carpeta y su
   fecha de emisión real (la misma regla de `saneamiento.py`).
3. **Sube** lo que falta en MinIO, **mueve** lo que está en la ruta equivocada y
   **elimina** de MinIO lo que ya no existe en Drive (solo dentro de `sucamec/carnets/`).
4. Con `--aplicar` escribe; sin él, solo reporta el plan.

La primera ejecución es la **migración inicial**. Las siguientes sirven para auditar y
reparar la divergencia. Se recomienda correr antes el saneamiento de Drive, ya existente,
para que Drive quede ordenado. Los PDFs que Drive no puede fechar (`indice.ignorados`)
**no se suben** y salen en el reporte.

### 3. Subflujo de ceses (`--solo-ceses`)
- Entrada: Excel `data/entrada/ceses*.xlsx` (o `--ceses <ruta>`), leído con
  `leer_registros`, que acepta las mismas columnas del padrón (DNI y negocio/razón social).
  Si una fila no indica negocio, aplica a JV y SELVA (regla existente de bajas).
- No abre SUCAMEC. Construye el plan solo con bajas y ejecuta `ELIMINAR` en Drive y en
  MinIO para **todos** los PDFs de ese DNI y negocio, activos y vencidos.
- Hoy `construir_plan` solo elimina los PDFs **activos** de Drive. Para ceses se eliminan
  también los de `VENCIDOS`, porque la persona ya no está en la empresa. **Esto se aplica
  solo en `--solo-ceses`.** Las bajas de la corrida normal siguen como hoy.
- Reporte Excel y correo de resumen, igual que la corrida normal. Sin `--aplicar` es simulación.
- `bajas*.xlsx` sigue funcionando como hoy en la corrida normal.

### Configuración (`.env`, no versionado)
```
MINIO_ENABLED=1
MINIO_ENDPOINT=s3.liderman.net.pe
MINIO_ACCESS_KEY=
MINIO_SECRET_KEY=
MINIO_BUCKET=mga-gadso
MINIO_USE_SSL=1
MINIO_PREFIX=sucamec/carnets
CARNET_CESES_EXCEL=
```
`MINIO_ENABLED=0` (o credenciales vacías) desactiva todo lo de MinIO y el bot se comporta
exactamente como hoy. Se agrega la dependencia `minio` a `requirements.txt` (la misma
librería que usa GADSO).

### Reporte
El Excel de la corrida agrega una columna o hoja con el estado MinIO de cada operación:
`HECHO | SIMULADO | MINIO_ERROR | OMITIDO`.

## Restricciones

- Python y estructura existentes (`src/carnet_flow/...`). Módulo nuevo `minio_sync/`
  junto a `drive_sync/`.
- Una falla en un almacenamiento no detiene el otro ni el resto de operaciones.
- Tests unitarios del cálculo de claves y de la reconciliación con un MinIO falso (sin red).

## Decisiones mías (revisar)

- MinIO es un **espejo de Drive**, no una segunda fuente de decisión.
- Fecha en la ruta con meses y días numéricos (`11/06`), no `11.NOVIEMBRE`: rutas
  uniformes para una máquina.
- En `--solo-ceses` se borran también los vencidos, en ambos almacenamientos. En la
  corrida normal las bajas siguen como hoy (solo activos), y MinIO replica exactamente lo
  mismo que se borra en Drive.
- Los PDFs sin fecha legible en Drive no se migran; se reportan.
- Se crea `data/plantillas/ceses_PLANTILLA.xlsx` como copia de la plantilla de bajas.

## Brechas del código actual que hay que cubrir

- `DriveClient` no descarga archivos. Se agrega un método `descargar(file_id) -> bytes`,
  necesario para la migración y para `MOVER_A_VENCIDOS` cuando falta la clave activa.
- `construir_indice` excluye `VENCIDOS/`. La reconciliación (que debe espejar también los
  vencidos) y `--solo-ceses` (que borra vencidos) necesitan indexar esa rama aparte, con la
  misma lectura de negocio y fecha, sin alterar el índice activo que usa el plan.
- `ELIMINAR` en Drive manda a la papelera (existe `restaurar`). En MinIO el borrado es
  definitivo: Drive queda como respaldo recuperable.

## Para GADSO (fuera de alcance, solo referencia)

Para ubicar los carnés de un DNI, GADSO puede listar `sucamec/carnets/<NEG>/` y filtrar
por el nombre `<DNI>_*.pdf` o por el metadato `dni`. Si el volumen lo exige, se puede
agregar un índice después, sin cambiar las rutas.

## Queda para el implementador

- Nombres internos, forma exacta del reporte y del registro de reanudación.
- Paralelismo de subidas a MinIO (reusar `drive_hilos` o una variable propia).
