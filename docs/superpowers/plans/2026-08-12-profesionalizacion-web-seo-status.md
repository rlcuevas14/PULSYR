# Estado de ejecución: profesionalización web y SEO

Este registro acompaña a
`2026-08-12-profesionalizacion-web-seo.md`. Cada fase se desarrolla en una rama, pasa
sus verificaciones y termina en PR + merge a `main`. Ninguna fase autoriza deploy.

| Fase | Estado | Evidencia |
|---|---|---|
| P0 — Contención y línea base | Completada | [PR #8](https://github.com/rlcuevas14/PULSYR/pull/8): ADR 0001, auditor reproducible, baseline y secret scan. |
| P1 — Superficie pública | Completada | [PR #9](https://github.com/rlcuevas14/PULSYR/pull/9): 9 páginas Astro SSG, contrato de HTML público y política `noindex` del origen privado. |
| P2 — SEO técnico | Completada | [PR #10](https://github.com/rlcuevas14/PULSYR/pull/10): metadata, schema, crawling, tarjeta social, semántica y errores con request ID. |
| P3 — Frontend/seguridad/caché | Completada | [PR #11](https://github.com/rlcuevas14/PULSYR/pull/11): assets locales con hash, CSRF, cabeceras/CSP, compresión, caché y secretos write-only. |
| P4 — Performance/a11y/UX | Completada | [PR #12](https://github.com/rlcuevas14/PULSYR/pull/12): presupuestos, matriz Playwright/axe y patrones accesibles compartidos. |
| P5 — Staging/analítica/observabilidad | Pendiente | — |
| P6 — Automatización/lanzamiento | Pendiente | — |

## Regla operativa

- No comenzar una fase sobre una rama anterior sin fusionar.
- No incluir cambios locales ajenos en los commits de fase.
- No ejecutar `.github/workflows/deploy.yml`, `vm-caddy.yml` ni `vm-env.yml`.
- Los workflows de CI y observación de sólo lectura sí forman parte de la validación.
