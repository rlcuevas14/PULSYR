# Evidencia P7: auditoría final de cumplimiento

- **Fecha:** 2026-08-12
- **Deploy/DNS/cuentas externas:** no ejecutados
- **Estado:** PR #25 fusionado; CI de PR aprobado; baseline remoto capturado

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
- El primer run de PR detectó que el JavaScript extraído excedía el presupuesto de
  12 KiB. El generador ahora minifica sólo el asset emitido: 10.546 B, sin relajar
  el umbral; `check:budget` y las pruebas privadas aprobaron nuevamente.
- La suite Python completa no pudo iniciar los tests con PostgreSQL porque Docker Desktop no estaba activo; CI Linux con servicio PostgreSQL es la evidencia autoritativa.

## Evidencia de GitHub

- [PR #25](https://github.com/rlcuevas14/PULSYR/pull/25) fusionado en `main`
  (`ee7c0bbf6bc100b4dd633169e5a5e58bc70bb6a0`).
- [CI de PR 31654375190](https://github.com/rlcuevas14/PULSYR/actions/runs/31654375190):
  10/10 jobs aprobados, incluida la prueba privada restaurada.
- [Web baseline 31654667586](https://github.com/rlcuevas14/PULSYR/actions/runs/31654667586):
  workflow manual aprobado y artefacto JSON conservado 30 días. DNS resolvió
  `app.pulsyr.dev` a `161.153.193.32`; TLS devolvió `TLSV1_ALERT_INTERNAL_ERROR` y
  por ello las siete observaciones HTTP registraron error de conexión. Es evidencia
  operativa externa pendiente, no un fallo silenciado por el workflow.

## Auditoría no-deploy

- Ningún workflow `Deploy`, `VM Caddy site` o `VM env sync` fue ejecutado por estas
  fases. El último `Deploy` del repositorio fue el run 31543265493 del 11 de agosto,
  anterior al plan; Caddy tampoco registra runs posteriores a esa fecha.
- `Web baseline` sólo leyó DNS/TLS/HTTP y no contiene pasos de publicación.
- Resolver el hallazgo TLS requiere una ventana de infraestructura autorizada y queda
  fuera de este trabajo por la restricción explícita de no desplegar.
