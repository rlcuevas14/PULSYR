# Evidencia P5: operaciones, observabilidad, medición y supply chain

- **Fecha:** 2026-08-12
- **Deploy/DNS/proxy externo:** no ejecutado
- **Publicación de imagen:** no ejecutada

## Implementado y verificable en repositorio

- Compose de staging sin `build`, obligado a una imagen por digest, base/volumen,
  secretos, OAuth y Sentry separados; proxy de ejemplo con autenticación y noindex.
- Validador de digest y runbook de migración, promoción sin rebuild y rollback.
- `/health/live` y `/health/ready`; readiness limita tiempo y comprueba PostgreSQL sin
  filtrar errores internos.
- Sentry SDK opcional con ambiente/release, PII desactivada, scrub adicional de body,
  cookies, query, usuario y headers sensibles; captura de excepciones HTTP y jobs.
- SLO inicial y runbook de correlación por release/request ID.
- Plausible sin cookies sólo cuando el build declara producción; catálogo fijo de
  cinco eventos, exclusión de staging/app privada y metatags opcionales para Google y
  Bing webmaster tools.
- Dependabot semanal para pip/npm/Actions/Docker; CI con `pip-audit`, `npm audit`,
  Trivy HIGH/CRITICAL y SBOM CycloneDX descargable.

## Evidencia local

- `pip-audit`: 0 vulnerabilidades conocidas.
- `npm audit --audit-level=high`: 0 vulnerabilidades.
- ruff/mypy: sin hallazgos.
- pruebas unitarias nuevas: health, estados de readiness, scrub/init Sentry y digest.
- Astro: 0 errores, 0 warnings, 0 hints.
- Contrato de analítica: cero snippets en build default/staging y exactamente uno por
  documento en build público de producción; catálogo completo.
- Presupuestos de rendimiento: aprobados con analítica desactivada por defecto.

## Evidencia externa pendiente por la restricción no-deploy

DNS, proxy secrets, base de staging, monitores de uptime, entrega real de alerta,
propiedades Search Console/Bing/Plausible y un ejercicio real de promoción/rollback
requieren autoridad de operador. Los runbooks enumeran la evidencia que debe adjuntarse
cuando se ejecute. Ningún resultado externo se simula ni se declara completado aquí.
