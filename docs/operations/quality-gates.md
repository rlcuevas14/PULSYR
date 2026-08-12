# Gates de calidad web

Los gates de `CI` validan el artefacto estático una sola vez y lo reutilizan sin
recompilar. Ningún job publica imágenes, modifica DNS ni despliega. El artefacto
`public-site-dist` se conserva 7 días; las evidencias de cada gate, 30 días.

| Gate | Umbral de bloqueo | Evidencia |
|---|---|---|
| SEO crawl | 100 % de 9 rutas con title, description, canonical, `h1`, hreflang y política de indexación válidos; 0 URLs privadas en sitemap; 0 enlaces internos rotos | `seo-crawl-report` |
| HTML | 0 errores de `html-validate` | `html-validation-report` |
| Browser/axe | 0 errores de consola/red; 0 violaciones axe serious/critical; 0 overflow en 5 anchos; smoke de `/`, `/producto/` y quickstart en Chromium, Firefox y WebKit | `browser-axe-report` |
| Lighthouse | Performance ≥ 0,90; Accessibility ≥ 0,95; Best Practices ≥ 0,95; SEO = 1,00; LCP ≤ 2.500 ms; CLS ≤ 0,10; TBT ≤ 200 ms en 3 rutas | `lighthouse-report` |
| Delivery | CSP y cabeceras base presentes; HTML con revalidación a 5 min; assets hashed con caché immutable por un año; gzip activo; 404 conserva status | `delivery-contract-report` |
| Links | 0 destinos internos/fragments rotos; 0 fallos en 6 journeys externos críticos, con 3 intentos | `link-integrity-report` |

Los resultados Lighthouse son de laboratorio. No sustituyen LCP/INP/CLS p75 de campo.
TBT se usa como señal de laboratorio, no se presenta como si fuera INP.

## Excepciones temporales

Una falla no se ignora ni se marca `continue-on-error`. Si un responsable acepta el
riesgo, el mismo PR debe:

1. añadir una entrada a `docs/quality-gate-exceptions.json`;
2. identificar gate, owner, aprobador, motivo y ticket de remediación;
3. vencer en 30 días como máximo;
4. ajustar de forma explícita el test/umbral afectado y enlazar la excepción.

CI rechaza registros incompletos, gates desconocidos, vencimientos pasados o ventanas
mayores a 30 días. Al vencer, el cambio debe revertirse o renovarse mediante una nueva
aprobación y evidencia; no hay renovaciones silenciosas.

Ejemplo (no activo):

```json
{
  "id": "QG-2026-001",
  "gate": "lighthouse",
  "owner": "web-owner",
  "approved_by": "engineering-lead",
  "reason": "Regresión acotada mientras se optimiza el hero",
  "tracking": "https://github.com/owner/repo/issues/123",
  "expires": "2026-08-30"
}
```

## Diagnóstico

- Descargar primero el artefacto del gate fallido y conservarlo en el ticket.
- Reproducir contra el mismo commit; no comparar scores de builds distintos.
- Tratar como flaky sólo un fallo demostrado en al menos tres ejecuciones y abrir un
  ticket con owner. Mientras tanto el gate sigue bloqueando.
- Un cambio de umbral requiere explicación del impacto, no sólo “hacer pasar CI”.
