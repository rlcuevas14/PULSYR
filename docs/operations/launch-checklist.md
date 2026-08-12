# Checklist de lanzamiento web

Esta checklist requiere una ventana de lanzamiento y autoridad de operador. Su creación
no autoriza deploy. A 2026-08-12 todos los ítems de producción permanecen **pendientes**;
no se simula evidencia externa.

## Antes de la ventana

- [ ] Registrar release/digest inmutable, owner, aprobador, horario y canal de incidente.
- [ ] Adjuntar CI verde y todos los artefactos descritos en `quality-gates.md`.
- [ ] Confirmar backup, migración ensayada y digest anterior para rollback.
- [ ] Validar TLS/DNS de `pulsyr.dev`, `www.pulsyr.dev`, `app.pulsyr.dev` y staging.
- [ ] Confirmar que secretos de producción/staging, DB y OAuth están separados.
- [ ] Revisar copy, legal, claims, contraste y teclado manualmente con owner competente.

## Validación posterior al cambio

- [ ] `http` y `www` redirigen una sola vez a la canonical HTTPS, preservando path/query.
- [ ] Canonical y hreflang (`en`, `x-default`) coinciden con la URL final, sin tracking.
- [ ] `robots.txt` y `sitemap.xml` responden 200; el sitemap sólo contiene las 9 URLs públicas.
- [ ] Login, setup, app privada, API y staging responden `noindex, nofollow` y no aparecen en sitemap.
- [ ] Homepage, producto y quickstart pasan Lighthouse móvil/escritorio en la URL pública.
- [ ] Organization/WebSite, SoftwareApplication y BreadcrumbList pasan Rich Results Test y Schema Markup Validator sin representar contenido inexistente.
- [ ] La tarjeta 1200×630 y el fallback sin imagen se prueban en WhatsApp, LinkedIn y X.
- [ ] Security Headers, CSP, compresión y caché coinciden con el contrato versionado.
- [ ] 404 y 500 mantienen status real, marca, `noindex` y request ID.
- [ ] Search Console inspecciona homepage/páginas pilar y recibe el sitemap.
- [ ] Bing Webmaster Tools recibe/verifica la misma propiedad.
- [ ] Plausible registra una sola fuente y los cinco eventos, sin staging/app/datos privados.
- [ ] Monitor externo detecta una prueba controlada y la alerta llega al canal esperado.
- [ ] Sentry recibe un error controlado con environment/release/request ID y sin PII.

## Criterio go/no-go

No-go ante migración fallida, salud `ready` negativa, 5xx sostenidos, pérdida de
autenticación, exposición/indexación privada, CSP que rompe journeys principales o
ausencia de rollback verificable. Un fallo cosmético sólo puede aceptarse con excepción
vigente, aprobada y enlazada.

## Rollback y cierre

- [ ] Reapuntar al digest anterior sin rebuild y ejecutar migración inversa sólo si su runbook lo permite.
- [ ] Verificar live/ready, login, ruta pública, 404, alertas y ausencia de errores nuevos.
- [ ] Registrar timestamps, digest, decisión, métricas antes/después y cualquier pérdida de datos.
- [ ] Adjuntar evidencia a la release y abrir acciones con owner/fecha.

Procedimiento detallado: `staging-promotion-rollback.md`. Esta checklist no ejecuta ni
automatiza un despliegue.
