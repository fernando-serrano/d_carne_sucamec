LOGIN_SELECTORS = {
    "tab_tradicional": (
        '#tabViewLogin a[href^="#tabViewLogin:j_idt"]:has-text("Autenticacion Tradicional"), '
        '#tabViewLogin a[href^="#tabViewLogin:j_idt"]:has-text("Autenticación Tradicional"), '
        '#tabViewLogin a:has-text("Autenticacion Tradicional"), '
        '#tabViewLogin a:has-text("Autenticación Tradicional")'
    ),
    "tipo_doc_select": "#tabViewLogin\\:tradicionalForm\\:tipoDoc_input",
    "numero_documento": "#tabViewLogin\\:tradicionalForm\\:documento",
    "usuario": "#tabViewLogin\\:tradicionalForm\\:usuario",
    "clave": "#tabViewLogin\\:tradicionalForm\\:clave",
    "captcha_img": "#tabViewLogin\\:tradicionalForm\\:imgCaptcha",
    "captcha_input": "#tabViewLogin\\:tradicionalForm\\:textoCaptcha",
    "boton_refresh": "#tabViewLogin\\:tradicionalForm\\:botonCaptcha",
    "ingresar": "#tabViewLogin\\:tradicionalForm\\:ingresar",
}

SUCCESS_SELECTORS = [
    '[id$=":menuPrincipal"]',
    '[id$=":menuprincipal"]',
    "form#gestionCitasForm",
]

ERROR_SELECTORS = [
    ".ui-messages-error",
    ".ui-message-error",
    ".ui-growl-message-error",
    ".mensajeError",
    "[class*='error']",
    "[class*='Error']",
]

MENU_SELECTORS = {
    "menu_root": '[id$=":menuPrincipal"], [id$=":menuprincipal"]',
    "consultas_button": 'button:has-text("Consultas"), a:has-text("Consultas"), span:has-text("Consultas")',
    "mis_vigilantes": 'a:has-text("Mis vigilantes"), span:has-text("Mis vigilantes")',
}
