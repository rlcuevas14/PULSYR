# Línea base web antes de la profesionalización

- **Captura:** 2026-08-12, America/Santiago
- **Origen esperado:** `https://app.pulsyr.dev`
- **Ubicación:** estación de desarrollo en Santiago, Chile
- **Motivo:** fase P0 del plan de profesionalización web/SEO
- **Herramientas:** DNS de Windows, curl 8.16.0/Schannel, Chrome/Lighthouse 13.4.1 y
  revisión estática del commit `ed092fb`

## Resultado de red

| Control | Resultado observado |
|---|---|
| DNS A | `app.pulsyr.dev` resolvió a `161.153.193.32`. |
| HTTPS HEAD `/` | Una solicitud alcanzó Cloudflare y devolvió `200 OK`. |
| Proxy/CDN | `Server: cloudflare`, `CF-Cache-Status: HIT`. |
| Caché observada | `Cache-Control: public, max-age=0, must-revalidate`. |
| Tipo observado | `Content-Type: text/html`. |
| Seguridad | La respuesta alcanzada no incluyó CSP, HSTS, X-Frame-Options, X-Content-Type-Options, Referrer-Policy ni Permissions-Policy. |
| Compresión | La respuesta HEAD alcanzada no declaró `Content-Encoding`. |
| TLS/GET posteriores | Schannel y Python/OpenSSL devolvieron `TLSV1_ALERT_INTERNAL_ERROR`; el acceso fue intermitente. |
| Lighthouse móvil | No terminó de cargar `/login` tras más de 80 segundos y se canceló; no existe un score válido. |
| PageSpeed Insights | API pública respondió `429 Too Many Requests`; no se usó como evidencia. |

El `200` de HEAD no demuestra el contenido final ni reemplaza una captura GET. La
intermitencia TLS y el timeout son hallazgos operativos, no se convierten artificialmente
en un score de rendimiento.

## Línea base del código

| Área | Estado antes de P1 |
|---|---|
| Render | HTML en servidor con FastAPI/Jinja. |
| Sitio público | No existe; `/` es dashboard autenticado. |
| Metadata | Titles por vista; sin description, canonical, OG/Twitter ni JSON-LD. |
| Crawling | Sin `robots.txt`, sitemap ni política `noindex` explícita. |
| Errores | Sin plantilla 404/500 HTML. |
| Frontend | Tailwind CDN en producción, HTMX por unpkg y scripts/handlers inline. |
| Headers | No hay middleware de headers; el bloque Caddy versionado sólo declara `reverse_proxy`. |
| Caché/compresión | No hay política explícita en app/repositorio. |
| Observabilidad | Sin SDK propio ni health endpoints externos. |
| QA navegador | Sin Playwright, axe ni Lighthouse CI. |

## Reproducción

Local:

```powershell
python scripts/web_baseline.py `
  --base-url https://app.pulsyr.dev `
  --location local/santiago `
  --label pre-p1
```

Después de fusionar esta fase, ejecutar manualmente el workflow **Web baseline**. El
runner `ubuntu-latest` usa una red y pila TLS independientes y conserva el JSON por 30
días. Este workflow sólo observa: no contiene pasos de despliegue ni escritura remota.

## Interpretación

La primera medición fiable posterior al merge será el artefacto de GitHub Actions. Si
TLS funciona allí, será la referencia externa P0. Si falla también, la disponibilidad/TLS
se convierte en incidente P0 antes de iniciar cualquier despliegue futuro. En ambos
casos, P1 puede desarrollarse y fusionarse sin desplegar, como exige el plan.
