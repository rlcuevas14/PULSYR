# Estado de ejecución: profesionalización web y SEO

Este registro acompaña a
`2026-08-12-profesionalizacion-web-seo.md`. Cada fase se desarrolla en una rama, pasa
sus verificaciones y termina en PR + merge a `main`. Ninguna fase autoriza deploy.

| Fase | Estado | Evidencia |
|---|---|---|
| P0 — Contención y línea base | Completada | [PR #8](https://github.com/rlcuevas14/PULSYR/pull/8): ADR 0001, auditor reproducible, baseline y secret scan. |
| P1 — Superficie pública | Pendiente | — |
| P2 — SEO técnico | Pendiente | — |
| P3 — Frontend/seguridad/caché | Pendiente | — |
| P4 — Performance/a11y/UX | Pendiente | — |
| P5 — Staging/analítica/observabilidad | Pendiente | — |
| P6 — Automatización/lanzamiento | Pendiente | — |

## Regla operativa

- No comenzar una fase sobre una rama anterior sin fusionar.
- No incluir cambios locales ajenos en los commits de fase.
- No ejecutar `.github/workflows/deploy.yml`, `vm-caddy.yml` ni `vm-env.yml`.
- Los workflows de CI y observación de sólo lectura sí forman parte de la validación.
