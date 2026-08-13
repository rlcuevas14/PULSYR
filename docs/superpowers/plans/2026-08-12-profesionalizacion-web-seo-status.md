# Estado de ejecución: profesionalización web y SEO

Este registro acompaña a
`2026-08-12-profesionalizacion-web-seo.md`. Cada fase se desarrolló en una rama, pasó
sus verificaciones y terminó fusionada en `main`. La restricción de no desplegar aplicó
durante P0–P7; los despliegues posteriores fueron autorizados explícitamente por el owner.

| Fase | Estado | Evidencia |
|---|---|---|
| P0 — Contención y línea base | Completada | [PR #8](https://github.com/rlcuevas14/PULSYR/pull/8): ADR 0001, auditor reproducible, baseline y secret scan. |
| P1 — Superficie pública | Completada | [PR #9](https://github.com/rlcuevas14/PULSYR/pull/9): 9 páginas Astro SSG, contrato de HTML público y política `noindex` del origen privado. |
| P2 — SEO técnico | Completada | [PR #10](https://github.com/rlcuevas14/PULSYR/pull/10): metadata, schema, crawling, tarjeta social, semántica y errores con request ID. |
| P3 — Frontend/seguridad/caché | Completada | [PR #11](https://github.com/rlcuevas14/PULSYR/pull/11): assets locales con hash, CSRF, cabeceras/CSP, compresión, caché y secretos write-only. |
| P4 — Performance/a11y/UX | Completada | [PR #12](https://github.com/rlcuevas14/PULSYR/pull/12): presupuestos, matriz Playwright/axe y patrones accesibles compartidos. |
| P5 — Staging/analítica/observabilidad | Completada | [PR #13](https://github.com/rlcuevas14/PULSYR/pull/13): staging por digest, health/Sentry, medición pública, supply-chain y CI aprobados. |
| P6 — Automatización/lanzamiento | Completada | [PR #24](https://github.com/rlcuevas14/PULSYR/pull/24): 10 jobs CI aprobados, gates con artefactos, checklist no-deploy y revisión 30/60/90. |
| P7 — Auditoría final | Completada | Auditoría de cierre, evidencia final, minificación de JavaScript privado y estabilización del gate Lighthouse, todo fusionado en `main`. |
| Publicación bilingüe y plan gratuito | Desplegada | Sitio EN/ES con selección regional y manual; flujo de registro gratuito documentado y aplicación publicada. |
| Videos ambientales | Desplegada | [ADR 0003](../../decisions/0003-public-site-ambient-video.md): hero y franja de contraste con fondos locales; CI `31694685928`; Worker `99a2f4b2-9492-42df-8af9-42ecf93a36e8`. |

## Regla operativa vigente

- No comenzar una fase sobre una rama anterior sin fusionar.
- No incluir cambios locales ajenos en los commits de fase.
- La aplicación privada se despliega por tag; el sitio público se despliega desde
  `site/` mediante Wrangler sólo con autorización explícita y después de CI verde.
- Los workflows de CI y observación de sólo lectura forman parte de la validación.
- Los cambios visuales del sitio público deben conservar los contratos SEO, HTML,
  performance, accesibilidad y navegador definidos por CI.

## Último estado de producción

- `main`: `968b0c0` (2026-08-13).
- CI final: `31694685928`, aprobado.
- Sitio público: Cloudflare Worker `99a2f4b2-9492-42df-8af9-42ecf93a36e8`.
- Hero español verificado a 320, 1024 y 1440 px.
- Videos locales optimizados: hero de 306 KiB y señal secundaria de 5,19 MiB.
