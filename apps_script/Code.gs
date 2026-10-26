/**
 * Puente de escritura para CARNET-SUCAMEC-GADSO.
 *
 * Corre como la cuenta dueña de "CARNÉS ELECTRONICO" (Implementar > Aplicación web >
 * Ejecutar como: Yo). Hace solo lo que la cuenta de servicio no puede: subir PDFs
 * (cuota del dueño) y enviar a la papelera.
 *
 * Seguridad: cada petición trae `clave`, que debe coincidir con la propiedad de script
 * CLAVE_BOT (Configuración del proyecto > Propiedades de la secuencia de comandos).
 * Solo opera dentro de la carpeta raíz ROOT_ID (propiedad de script ROOT_ID).
 */

var ROOT_ID = PropertiesService.getScriptProperties().getProperty('ROOT_ID');

function doPost(e) {
  try {
    var req = JSON.parse(e.postData.contents);
    var clave = PropertiesService.getScriptProperties().getProperty('CLAVE_BOT');
    if (!clave || req.clave !== clave) return json_({ ok: false, error: 'no autorizado' });

    if (req.accion === 'ping') {
      return json_({ ok: true, usuario: Session.getEffectiveUser().getEmail() });
    }
    if (req.accion === 'subir') {
      var carpeta = DriveApp.getFolderById(req.carpeta_id);
      if (!dentroDeRaiz_(carpeta)) return json_({ ok: false, error: 'carpeta fuera de la raíz' });
      var blob = Utilities.newBlob(Utilities.base64Decode(req.contenido_b64), 'application/pdf', req.nombre);
      var archivo = carpeta.createFile(blob);
      if (req.descripcion) archivo.setDescription(req.descripcion);
      return json_({ ok: true, id: archivo.getId() });
    }
    if (req.accion === 'eliminar') {
      var f = DriveApp.getFileById(req.file_id);
      var padres = f.getParents();
      if (!padres.hasNext() || !dentroDeRaiz_(padres.next())) return json_({ ok: false, error: 'archivo fuera de la raíz' });
      f.setTrashed(true); // papelera: recuperable 30 días
      return json_({ ok: true });
    }
    if (req.accion === 'eliminar_carpeta') {
      // Solo carpetas VACÍAS dentro de la raíz (saneamiento de carpetas duplicadas).
      var c = DriveApp.getFolderById(req.carpeta_id);
      if (c.getId() === ROOT_ID || !dentroDeRaiz_(c)) return json_({ ok: false, error: 'carpeta fuera de la raíz' });
      if (c.getFiles().hasNext() || c.getFolders().hasNext()) return json_({ ok: false, error: 'carpeta no vacía' });
      c.setTrashed(true);
      return json_({ ok: true });
    }
    if (req.accion === 'restaurar') {
      // Saca de la papelera un archivo o carpeta de la raíz (reversión de la migración).
      var item;
      try { item = DriveApp.getFileById(req.file_id); } catch (e1) { item = DriveApp.getFolderById(req.file_id); }
      var ps = item.getParents();
      if (!ps.hasNext() || !dentroDeRaiz_(ps.next())) return json_({ ok: false, error: 'fuera de la raíz' });
      item.setTrashed(false);
      return json_({ ok: true });
    }
    return json_({ ok: false, error: 'acción desconocida' });
  } catch (err) {
    return json_({ ok: false, error: String(err) });
  }
}

function dentroDeRaiz_(carpeta) {
  for (var i = 0; i < 12 && carpeta; i++) {
    if (carpeta.getId() === ROOT_ID) return true;
    var padres = carpeta.getParents();
    carpeta = padres.hasNext() ? padres.next() : null;
  }
  return false;
}

function json_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}
