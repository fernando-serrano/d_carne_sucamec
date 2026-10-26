# DSSP > CARNÉ > BANDEJA DE IMPRESIÓN (docs/pdf/Flujo de trabajo - Carnet Sucamec.pdf, pasos 1–10).
# Los j_idt de PrimeFaces cambian entre despliegues: se prioriza id estable o texto visible.

MENU = {
    "menu_root": "#j_idt11\\:menuPrincipal, #j_idt11\\:menuprincipal",
}

TEXTO_DSSP = "DSSP"
TEXTO_CARNE = "CARNE"
TEXTO_BANDEJA = "BANDEJA DE IMPRESION"
TEXTO_BUSCAR_POR = "DNI/CE PERSONAL DE SEGURIDAD"

VISTA = {
    "buscar_por_widget": "#impresionForm\\:buscarPor",
    "buscar_por_label": "#impresionForm\\:buscarPor_label",
    "buscar_por_items": "#impresionForm\\:buscarPor_items",
    "buscar_por_opcion": (
        "#impresionForm\\:buscarPor_items li[data-label='DNI/CE PERSONAL DE SEGURIDAD'], "
        "#impresionForm\\:buscarPor_3"
    ),
    "filtro": "#impresionForm\\:filtroBusqueda",
    "boton_buscar": "#impresionForm button:has(span.ui-icon-search), #impresionForm button:has-text('Buscar')",
    "filas": "#impresionForm\\:dtResultados_data > tr",
    "fila_vacia": "#impresionForm\\:dtResultados_data > tr.ui-datatable-empty-message",
    "boton_pdf": "button[id$=':btnVerPdf']",
}
