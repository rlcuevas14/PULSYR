# Evidencia P7: auditoría final de cumplimiento

- **Fecha:** 2026-08-12
- **Deploy/DNS/cuentas externas:** no ejecutados
- **Estado al crear la evidencia:** candidato validado localmente; CI y baseline remoto pendientes

## Brechas cerradas

- Los scripts de página y handlers Jinja se extrajeron al asset JavaScript privado con hash de contenido.
- La app privada y el sitio público ya no requieren `unsafe-inline` en `script-src`; la excepción de estilos se conserva y queda explicada en ADR 0002.
- Analítica usa un bootstrap propio externo y el contrato de build comprueba su presencia sólo en producción habilitada.
- La prueba Playwright de modales, foco, validación, confirmación y doble submit vuelve a ser un gate explícito de CI.
- El baseline manual incluye `/favicon.svg` además de rutas HTML, indexación y 404.
- La evidencia P6 registra el merge de PR #24 y el run verde de `main`.

## Evidencia local

- 18 pruebas focales Python aprobadas (`frontend_assets`, `web_security`, `web_baseline`).
- 2 pruebas Playwright de comportamiento privado aprobadas.
- Astro check: 0 errores, 0 warnings, 0 hints; HTML validation aprobada.
- 55 pruebas públicas Chromium y 9 smokes Chromium/Firefox/WebKit aprobados.
- Builds de analítica habilitada y deshabilitada aprobaron su contrato.
- La suite Python completa no pudo iniciar los tests con PostgreSQL porque Docker Desktop no estaba activo; CI Linux con servicio PostgreSQL es la evidencia autoritativa.

## Evidencia pendiente de GitHub

Esta sección se completa después del merge con la PR, el run de CI en `main` y el run manual `Web baseline`. El workflow de baseline es de observación HTTP/DNS/TLS exclusivamente y no despliega.
