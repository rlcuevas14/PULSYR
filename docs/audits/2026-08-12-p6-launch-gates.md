# Evidencia P6: gates y preparación de lanzamiento

- **Fecha:** 2026-08-12
- **Deploy/DNS/cuentas externas:** no ejecutados
- **Fase:** en curso hasta PR, CI y merge

## Implementado

- Build estático único reutilizado por seis gates independientes.
- Crawl SEO con reporte JSON, validación HTML, Chromium + axe y smoke adicional en
  Chromium/Firefox/WebKit.
- Lighthouse sobre homepage, producto y quickstart con umbrales de categorías, LCP,
  CLS y TBT explícitos.
- Caddy real en contenedor para probar cabeceras, caché, compresión y status 404.
- Enlaces/fragments internos y seis journeys GitHub críticos con retry y reporte.
- Registro de excepciones temporales validado (vacío al crear esta evidencia).
- Checklist de lanzamiento/rollback y revisión T+30/T+60/T+90.

## Evidencia local

- Astro build/check: 11 documentos, 0 errores, 0 warnings, 0 hints.
- HTML validation: 0 errores; corrigió un `aria-label` sin rol en la ilustración del hero.
- Crawl: 9 rutas indexables aprobadas.
- Links: 236 internos y 6 externos críticos aprobados.
- Browser: 57 pruebas profundas en Chromium y 9 smokes en Chromium/Firefox/WebKit.
- npm audit: 0 vulnerabilidades conocidas después de fijar Lighthouse 13.4.1.
- Lighthouse homepage: 1,00 en performance, accessibility, best-practices y SEO.

La ejecución local completa de Lighthouse quedó limitada por limpieza de temporales de
Chrome en Windows; generó el reporte de homepage antes del error. La matriz Linux de CI,
los tres reportes Lighthouse, WebKit/Firefox y Caddy son la evidencia autoritativa
pendiente. No se atribuye éxito a esos gates hasta que el PR quede verde.

## Evidencia externa pendiente

Redirects/TLS reales, validadores OG/schema, Search Console/Bing, CWV de campo,
monitores/alertas y ejercicio de rollback requieren una ventana autorizada. Están
enumerados en la checklist y permanecen pendientes por la restricción no-deploy.
