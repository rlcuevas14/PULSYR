# Plan de profesionalización web, SEO y operación de Pulsyr

**Fecha:** 2026-08-12

**Base de evaluación:** `C:\Proyectos\guidelinesWebsites.txt`

**Alcance auditado:** aplicación FastAPI/Jinja/HTMX, plantillas, assets, configuración, CI/CD y configuración de Caddy versionada en este repositorio.
**Tipo de entregable:** plan de trabajo; este documento no implementa cambios.

## 1. Resultado ejecutivo

Pulsyr ya tiene una base de producto más madura que una SPA improvisada: renderiza HTML en servidor, usa URLs mayormente semánticas, tiene títulos por vista, idioma declarado, favicon/manifest, modo oscuro sin destello, manejo de sesión endurecido y una CI con pruebas, cobertura, lint, tipos y migraciones.

La mayor limitación para posicionamiento no es el framework actual. Es arquitectónica: `/` y casi toda la interfaz pertenecen a una aplicación autenticada. Un crawler recibe una redirección a login y no existe una superficie pública con contenido suficiente para responder búsquedas, generar enlaces o explicar el producto. Indexar el dashboard no sería útil y podría crear riesgos de privacidad.

La arquitectura objetivo recomendada es:

- `https://pulsyr.dev`: sitio público, rápido e indexable, generado estáticamente.
- `https://app.pulsyr.dev`: aplicación FastAPI privada, explícitamente `noindex`.
- Opcional a futuro: documentación pública bajo `https://pulsyr.dev/docs/` para concentrar autoridad en el dominio principal.

Para el sitio público se recomienda Astro en modo SSG dentro de este repositorio. Node se usaría sólo en build; el runtime de Pulsyr seguiría siendo Python. Si se decide mantener cero tooling Node, las mismas reglas pueden implementarse con Jinja y páginas estáticas, a costa de más trabajo manual para contenido, sitemap y metadatos.

No se puede prometer una mejora de ranking por cambios técnicos aislados. El plan crea las condiciones para indexar, medir y competir; la mejora orgánica dependerá además de contenido útil, demanda, autoridad y enlaces.

## 2. Evidencia y límites de la auditoría

### Evidencia observada

- SSR: `app/main.py`, `app/templates_config.py` y plantillas Jinja.
- Head común: `app/templates/partials/_head.html`.
- Layouts: `app/templates/base.html`, `app/templates/auth_base.html` y `app/templates/setup.html`.
- Rutas privadas: `app/ui/router.py`, `app/management/router.py`, `app/projects/router.py` y `app/accounts/router.py`.
- Seguridad de sesión: `app/main.py` y `app/config.py`.
- Despliegue: `.github/workflows/deploy.yml`, `.github/workflows/vm-caddy.yml` y `docker-compose.yml`.
- Calidad: `.github/workflows/ci.yml`, `pyproject.toml` y `requirements.lock`.
- Identidad visual: `docs/DESIGN-template.md` y assets de `app/static/brand/`.

### Límites

- El entorno de auditoría no pudo completar el handshake TLS con `app.pulsyr.dev`; por eso dominio, certificado, cabeceras y compresión deben volver a medirse desde una red externa antes de implementar.
- No se ejecutó una auditoría visual con navegadores reales ni datos de campo de Chrome UX Report.
- No se revisaron Search Console, analítica ni registros del servidor porque no están en el repositorio.
- Hay cambios locales preexistentes en `app/templates/threads.html` y `app/ui/router.py`; este plan no los modifica.
- `.env.local` existe como archivo no versionado y actualmente **no está cubierto por `.gitignore`**. No se inspeccionó su contenido.

## 3. Estado del checklist de 40 puntos

Leyenda: **Cumplido** = hay evidencia suficiente; **Parcial** = existe una base pero falta cerrar o medir; **Falta** = no se encontró implementación; **N/A actual** = no existe hoy el tipo de recurso afectado, pero se vuelve requisito al crear el sitio público.

| # | Control | Estado actual | Evidencia / brecha | Trabajo |
|---:|---|---|---|---|
| 1 | Dominio personalizado | Parcial | El despliegue declara `app.pulsyr.dev`; falta validación externa y dominio público raíz. | P0, P1 |
| 2 | HTML visible sin JS | Cumplido | FastAPI + Jinja renderizan el contenido en servidor. | Mantener |
| 3 | 404 personalizada | Falta | No hay handler ni plantilla 404; varias rutas devuelven texto/JSON. | P2 |
| 4 | Herramienta adecuada al contenido | Cumplido | La app no es una SPA de Vite/React. | SSG sólo para marketing |
| 5 | Título único por página | Cumplido | Las vistas completas definen o heredan un `title`; falta contrato central para el sitio público. | P2 |
| 6 | Meta description | Falta | El head común no la declara. | P2 |
| 7 | Open Graph / social cards | Falta | No hay OG ni Twitter cards. | P2 |
| 8 | Datos estructurados | Falta | No existe JSON-LD. | P2 |
| 9 | Un solo `h1` | Parcial | Las páginas base están bien, pero Markdown de usuario puede renderizar `h1` adicional. | P4 |
| 10 | Presencia de `h1` | Parcial | `projects_settings.html` no tiene `h1`; el sitio público aún no existe. | P2, P4 |
| 11 | Canonical | Falta | No hay `rel="canonical"`. | P2 |
| 12 | `llms.txt` / archivo para agentes | Falta, baja prioridad | No existe. Es una convención experimental, no un factor de ranking demostrado. | P2 opcional |
| 13 | Política intencional en `robots.txt` | Falta | No existe `robots.txt` ni política diferenciada público/privado. | P1, P2 |
| 14 | Favicon | Cumplido | SVG, PNG, Apple touch icon y manifest. | Mantener |
| 15 | Sitemap | Falta | No existe `sitemap.xml`. | P2 |
| 16 | Atributo `lang` | Cumplido | `lang` se resuelve desde i18n en los layouts. | Añadir `hreflang` si marketing multilingüe |
| 17 | Alternativas de imágenes | N/A actual | No hay `<img>` de contenido; SVG decorativos usan `aria-hidden` o controles con etiqueta. | Requisito P4 |
| 18 | Source maps públicos | N/A actual | No existe bundle propio ni mapas. | Definir política al compilar frontend |
| 19 | Errores de consola | Parcial | No hay prueba de navegador que falle por errores de consola/red. | P4, P6 |
| 20 | Bundle JS y code splitting | Parcial | No hay bundle local gigante, pero Tailwind CDN compila en cliente y scripts globales no tienen presupuesto. | P3, P4 |
| 21 | Imágenes optimizadas | Cumplido en app / requisito futuro | Assets actuales son pequeños; marketing añadirá imágenes de contenido. | P4 |
| 22 | Lazy loading | N/A actual / requisito futuro | No hay imágenes de contenido; los previews privados son iframes visibles. | P4 |
| 23 | CLS controlado | Parcial | Hay dimensiones en SVG y `display=swap`, pero no medición Lighthouse/CWV. | P4, P6 |
| 24 | Fuentes no bloqueantes | Cumplido básico | Google Fonts usa `display=swap` y preconnect. | Mejorar con fuente local en P3/P4 |
| 25 | Cabeceras de seguridad | Falta | No hay middleware ni bloque Caddy versionado con CSP/HSTS/etc. | P0, P3 |
| 26 | Secretos fuera del frontend | Parcial | Claves de app viven en backend, pero `.env.local` no está ignorado y credenciales Sentry vuelven al DOM del owner. | P0, P3 |
| 27 | Manejo de errores | Parcial | Existe catch-all, pero una falla UI recibe JSON y no hay 500 HTML amigable. | P2, P3 |
| 28 | Staging | Falta | El flujo publicado despliega directo a producción por tag. | P5 |
| 29 | Contraste | Parcial | Hay tokens y cálculo de foreground; falta auditoría WCAG de todas las combinaciones/estados. | P4 |
| 30 | Navegación por teclado | Parcial | Inputs tienen foco; faltan estados globales, skip link y focus trap de modales. | P4 |
| 31 | Tap targets móviles | Parcial | `.p-btn` mide 44 px, pero `.p-btn-sm` mide 34 px y varios icon buttons rondan 36 px. | P4 |
| 32 | Feedback en formularios | Parcial | Hay toasts, errores de auth y algunos spinners; no existe patrón uniforme por campo/request. | P4 |
| 33 | Modo oscuro | Cumplido | Se aplica antes del paint, respeta sistema y usa tokens. | Prueba de regresión P6 |
| 34 | URLs semánticas | Parcial | La app usa nombres claros; falta arquitectura de información y slugs del sitio público. | P1 |
| 35 | Analítica confiable | Falta | No se encontró analítica web/producto. | P5 |
| 36 | Compresión | Falta / no verificable en vivo | App y bloque Caddy versionado no declaran compresión. | P3 |
| 37 | Dependencias mantenidas | Parcial | Runtime bloqueado con versiones exactas; faltan auditoría CVE y actualización automatizada. | P5 |
| 38 | Caché correcta | Falta | StaticFiles no define política larga y los nombres no llevan hash. | P3 |
| 39 | Sin scroll horizontal accidental | Parcial | Hay contenedores deliberados `overflow-x-auto`; falta matriz responsive real. | P4, P6 |
| 40 | Monitoreo de producción | Falta | Pulsyr integra Sentry como producto, pero no se instrumenta a sí mismo ni declara uptime checks. | P5 |

## 4. Principios de implementación

1. **Separar indexación de autenticación.** El contenido público debe ser indexable; dashboards, login, setup, API, webhooks y páginas con datos de clientes deben llevar `X-Robots-Tag: noindex, nofollow` o permanecer fuera del host público.
2. **No usar `robots.txt` como control de acceso.** Sólo guía crawling; la seguridad sigue dependiendo de autenticación y autorización.
3. **Mantener SSR/SSG.** No migrar la aplicación a React/Next sólo por SEO.
4. **Contenido antes que ornamento SEO.** Metadata y schema deben describir contenido visible y verdadero.
5. **Presupuesto de rendimiento desde el primer PR.** Evitar corregir un sitio público pesado al final.
6. **CSP como objetivo de diseño.** Retirar Tailwind CDN e inline handlers antes de activar una CSP estricta.
7. **Medir producción y staging por separado.** Los scores de Lighthouse de laboratorio no reemplazan datos de campo.
8. **No indexar staging.** Debe estar autenticado o restringido y además llevar `noindex`.

## 5. Plan por fases

Las estimaciones son días-persona ideales y no incluyen esperas por DNS, aprobación de copy ni recolección de datos de campo. Total orientativo: **22–34 días de ingeniería + 5–10 días de contenido/diseño**, ejecutables en 4–6 semanas con solapamiento.

### P0 — Contención y línea base (1–2 días)

#### P0.1 Proteger archivos locales y automatizar detección de secretos

- Añadir `.env.*` a `.gitignore`, preservando explícitamente `!.env.example`.
- Verificar que ningún `.env.local`, clave privada, token o dump esté en el historial Git.
- Incorporar Gitleaks o equivalente en pre-commit/CI.
- Rotar cualquier credencial sólo si el escaneo demuestra exposición; no hacerlo por suposición.

**Aceptación:** `.env.local` queda ignorado; CI falla ante un secreto de prueba; no se imprimen valores sensibles en logs.

#### P0.2 Capturar línea base externa

- Desde una red externa, registrar para `/`, `/login`, un asset y una ruta inexistente: status, redirecciones, TLS, DNS, `Content-Encoding`, `Cache-Control` y cabeceras de seguridad.
- Ejecutar Lighthouse móvil/escritorio y WebPageTest sobre las URLs públicas disponibles.
- Registrar errores de consola y requests fallidos con navegador limpio.
- Crear una ficha de baseline fechada en `docs/audits/`.

**Aceptación:** existe evidencia repetible, con URL, fecha, ubicación y configuración; ningún objetivo posterior se evalúa sólo “a ojo”.

#### P0.3 Decisión de dominio e indexación

- Confirmar `pulsyr.dev` como canonical público y `app.pulsyr.dev` como producto.
- Elegir idioma principal y estrategia de idiomas: una sola lengua inicial o rutas `/es/`, `/en/`, `/fr/` con `hreflang` y `x-default`.
- Definir la única variante canónica (`https`, con o sin `www`) y redirecciones 301.

**Aceptación:** decisión registrada y aprobada antes de producir URLs o copy.

### P1 — Superficie pública indexable (5–8 días de ingeniería, 4–8 de contenido/diseño)

#### P1.1 Crear el sitio público SSG

- Crear `site/` con Astro en modo static output, CSS compilado y JavaScript sólo donde sea necesario.
- Reutilizar los tokens de marca de `docs/DESIGN-template.md`, sin copiar la identidad de la marca analizada en ese documento; los assets finales deben ser propios de Pulsyr.
- Desplegar el artefacto estático separado de la app y enrutarlo desde Caddy/CDN.
- Mantener `app.pulsyr.dev` apuntando a FastAPI.

**Alternativa sin Node:** páginas Jinja públicas en un servicio/host separado, con generador propio de sitemap y metadata. Elegir una sola alternativa; no mantener dos stacks de marketing.

**Aceptación:** `view-source:` de cada página pública contiene navegación, `h1`, copy principal, enlaces y metadata sin ejecutar JavaScript.

#### P1.2 Arquitectura de información y páginas mínimas

Publicar inicialmente:

- `/`: propuesta de valor, problema, flujo, beneficios, prueba visual del producto y CTA.
- `/producto/`: funcionalidades y casos de uso reales.
- `/integraciones/mcp/`: qué es la integración y cómo conectar un agente.
- `/open-source/`: licencia, self-hosting, repositorio y límites de marca.
- `/docs/primeros-pasos/`: guía verificable basada en README.
- `/seguridad/`: modelo de aislamiento, tokens, despliegue y canal de reporte.
- `/privacidad/` y `/terminos/`: textos legales validados por responsable competente.
- `/contacto/` o CTA equivalente; no crear formularios si no habrá un proceso de respuesta.

Blog y casos de clientes quedan fuera del lanzamiento salvo que exista responsable y calendario editorial. Un blog vacío perjudica más la percepción que no tenerlo.

**Aceptación:** cada página responde a una intención distinta, tiene copy original, enlaces internos y un CTA medible; no hay claims que el producto no pueda demostrar.

#### P1.3 Política público/privado

- En el sitio público: `index,follow` por defecto.
- En la app: middleware con `X-Robots-Tag: noindex, nofollow` para HTML privado, login y setup.
- APIs, MCP, webhooks, downloads y errores nunca se incluyen en sitemap.
- Revisar `/docs` y `/redoc` de FastAPI: deshabilitarlos en producción o protegerlos según la estrategia de API pública.

**Aceptación:** una prueba automatizada recorre rutas representativas y verifica la política correcta sin depender de `robots.txt` para privacidad.

### P2 — SEO técnico y semántica (3–5 días)

#### P2.1 Contrato único de metadata

Crear un componente/layout que exija por página:

- `title` descriptivo y único.
- `description` específica.
- URL canonical absoluta, sin parámetros de tracking.
- `robots`.
- `og:title`, `og:description`, `og:type`, `og:url`, `og:image`, dimensiones y alt.
- `twitter:card` y campos equivalentes.
- Idioma y alternates `hreflang` cuando correspondan.

Generar una imagen social de marca 1200×630 y variantes sólo cuando una página lo justifique.

**Aceptación:** build falla si una página indexable no entrega metadata obligatoria; no hay títulos/descripciones duplicados en el crawl de CI.

#### P2.2 Datos estructurados veraces

- Homepage: `Organization` y `WebSite`.
- Producto: `SoftwareApplication` sólo con propiedades verificables.
- Páginas internas: `BreadcrumbList` cuando el breadcrumb también sea visible.
- Documentación/contenido: `TechArticle` o `Article` sólo cuando corresponda.
- No añadir ratings, precios, FAQ o testimonios inventados.
- Validar JSON-LD con Rich Results Test y Schema Markup Validator.

**Aceptación:** cero errores estructurales; el contenido marcado es visible y coincide con la página. La presencia de JSON-LD no se considera garantía de rich result ni ranking.

#### P2.3 Crawling, sitemap y archivos raíz

- Generar `/sitemap.xml` automáticamente con sólo URLs canonical indexables y fecha real de modificación cuando esté disponible.
- Publicar `/robots.txt` con referencia al sitemap y política explícita; no bloquear CSS/JS requeridos para render.
- Publicar `/llms.txt` únicamente como índice conciso de documentación y producto, marcado internamente como experimental y sin atribuirle impacto SEO.
- No implementar `ai.txt` salvo que se adopte una especificación concreta y haya una política legal/estratégica aprobada.

**Aceptación:** sitemap válido, URLs absolutas y 200, sin redirecciones/noindex/privadas; robots accesible; pruebas verifican MIME y contenido.

#### P2.4 Jerarquía de headings y errores

- Garantizar exactamente un `h1` visible por documento completo.
- Añadir `h1` a Settings y revisar layouts compartidos.
- Transformar o restringir `h1` proveniente de Markdown de usuario a `h2` en páginas que ya tienen título principal.
- Implementar 404 y 500 HTML con marca, navegación de retorno, `noindex` y request ID; conservar JSON para APIs mediante content negotiation o handlers separados.

**Aceptación:** crawler de CI no detecta páginas sin `h1` ni con múltiples `h1`; `/ruta-inexistente` devuelve 404 real, no 200 ni redirect genérico.

### P3 — Entrega frontend, seguridad y caché (5–8 días)

#### P3.1 Retirar dependencias CDN de producción

- Sustituir `https://cdn.tailwindcss.com` por CSS compilado y minificado.
- Vendorizar o empaquetar HTMX y Sortable con versiones fijas; cargar Sortable sólo en backlog.
- Extraer scripts inline y handlers `onclick`/`onchange`/`onsubmit` a módulos propios.
- Mantener el script mínimo de tema como hash/nonce permitido o reemplazarlo por una estrategia CSP-compatible.
- Generar assets con hash de contenido; no publicar source maps, o subirlos de forma privada a monitoreo y excluirlos del artefacto público.

**Aceptación:** ninguna dependencia de ejecución depende de Google Fonts/CDNJS/unpkg/jsDelivr salvo excepción documentada; la UI funciona con CSP en modo enforcement.

#### P3.2 Cabeceras de seguridad y CSRF

- Definir en Caddy o middleware, con pruebas:
  - `Strict-Transport-Security` sólo después de validar HTTPS de todos los subdominios.
  - `Content-Security-Policy` restrictiva, preferentemente sin `unsafe-inline`.
  - `frame-ancestors 'none'` o política explícita; `X-Frame-Options: DENY` como compatibilidad.
  - `X-Content-Type-Options: nosniff`.
  - `Referrer-Policy: strict-origin-when-cross-origin`.
  - `Permissions-Policy` mínima.
- Añadir tokens CSRF a todos los POST/PATCH/DELETE de sesión. `SameSite=Strict` se mantiene como defensa adicional, no como sustituto.
- Revisar CORS, trusted hosts y cabeceras forwarded detrás de Caddy.

**Aceptación:** suite automática comprueba cabeceras en HTML, assets y API; formularios sin CSRF son rechazados; OAuth y HTMX siguen funcionando.

#### P3.3 Higiene de credenciales

- No devolver `client_secret` ni `api_token` guardados al DOM; mostrar sólo estado configurado y permitir reemplazo write-only.
- Evaluar cifrado de credenciales de integración en reposo con clave separada y rotación documentada.
- Sanitizar logs y eventos de observabilidad.
- Documentar qué variables son públicas y cuáles jamás pueden llegar al navegador.

**Aceptación:** búsqueda del HTML renderizado y logs no encuentra secretos; las pruebas cubren guardar, mantener sin cambio, reemplazar y borrar una credencial.

#### P3.4 Compresión y caché

- Habilitar compresión en el proxy para HTML, CSS, JS, JSON, XML, SVG y texto. En Caddy estándar, preferir `encode zstd gzip`; usar Brotli sólo si el build del servidor lo soporta y se prueba.
- Assets con hash: `Cache-Control: public, max-age=31536000, immutable`.
- HTML público: caché corta/revalidable según despliegue.
- HTML privado y respuestas con sesión: `Cache-Control: private, no-store` cuando contengan datos sensibles.
- `robots.txt`, sitemap y manifest: política corta y revalidable.

**Aceptación:** pruebas con `Accept-Encoding` verifican compresión; headers distinguen correctamente assets públicos y contenido privado; un deploy nuevo no sirve assets obsoletos.

### P4 — Rendimiento, accesibilidad y UX (5–8 días)

#### P4.1 Presupuestos de rendimiento

Objetivos de campo al percentil 75:

- LCP ≤ 2,5 s.
- INP ≤ 200 ms.
- CLS ≤ 0,1.

Presupuestos iniciales de laboratorio para páginas públicas:

- Lighthouse Performance ≥ 90 en móvil como gate orientativo.
- JavaScript inicial propio ≤ 75 KB comprimido; idealmente cero en páginas puramente informativas.
- CSS inicial ≤ 50 KB comprimido.
- Ninguna imagen individual > 250 KB sin justificación.

**Aceptación:** Lighthouse CI evita regresiones; tras obtener tráfico suficiente se evalúan CWV de campo, no sólo laboratorio.

#### P4.2 Imágenes, fuentes y estabilidad visual

- Producir AVIF/WebP con fallback cuando corresponda y `srcset`/`sizes` responsivos.
- Declarar `width`/`height` o `aspect-ratio` en toda imagen/iframe.
- `loading="lazy"` y `decoding="async"` bajo el primer viewport; el LCP no debe ser lazy.
- Alt descriptivo para imágenes informativas y alt vacío para decoración.
- Self-host de Inter WOFF2, subset por idioma, `font-display: swap`; preload sólo de la fuente crítica realmente usada.

**Aceptación:** auditoría no detecta imágenes sobredimensionadas, sin dimensiones ni alt; CLS cumple el presupuesto.

#### P4.3 WCAG 2.2 AA y teclado

- Añadir skip link y landmarks coherentes.
- Estilo `:focus-visible` global de al menos 2 px y contraste perceptible para enlaces, botones, summaries, chips y controles personalizados.
- Modales con `role="dialog"`, `aria-modal`, título asociado, focus trap, cierre con Escape y restauración de foco.
- Tablas con captions/headers adecuados y alternativa responsive cuando no baste scroll.
- Validar zoom 200 %, reflow a 320 CSS px y preferencias `prefers-reduced-motion`.
- Auditar contraste normal (4.5:1), texto grande y componentes/estados (3:1), incluidos dark mode y colores elegidos por proyecto.

**Aceptación:** axe no reporta violaciones serious/critical en rutas objetivo; recorrido manual completo sólo con teclado; no hay pérdida de contenido a 200 %.

#### P4.4 Tap targets y formularios

- Llevar acciones táctiles frecuentes a 44×44 px; `.p-btn-sm` puede conservar apariencia compacta usando área interactiva/espaciado suficiente donde aplique.
- Asociar todos los labels con inputs; añadir `autocomplete`, `inputmode` y propósito correcto.
- Errores por campo con texto, `aria-invalid`, `aria-describedby` y resumen enfocable.
- Estado pendiente: deshabilitar submit, conservar label, mostrar indicador y evitar doble envío.
- Resultado: `aria-live` apropiado, valores conservados y foco enviado al primer error.
- Confirmaciones destructivas accesibles, no dependientes de `confirm()` inline.

**Aceptación:** patrón compartido aplicado a auth, setup, creación/edición de ítems, proyectos e integraciones; pruebas cubren error, éxito, red lenta y doble submit.

#### P4.5 Responsive y consola limpia

- Matriz de viewport: 320, 375, 768, 1024 y 1440 px; light/dark; ES/EN/FR.
- Corregir la causa de cualquier overflow; no ocultarlo globalmente con `overflow-x:hidden` salvo elemento decorativo probado.
- Prueba Playwright que falle por `console.error`, excepción no controlada o request esencial 4xx/5xx.

**Aceptación:** cero scroll horizontal en documento y cero error de consola en los recorridos críticos.

### P5 — Staging, analítica, dependencias y observabilidad (4–6 días)

#### P5.1 Staging reproducible

- Crear `staging.pulsyr.dev` y/o `app-staging.pulsyr.dev` con base de datos, secretos y OAuth separados.
- Proteger con autenticación en proxy o allowlist; sumar `noindex` como segunda barrera.
- Desplegar cada PR o rama aprobada; ejecutar migraciones y smoke tests antes de promover la misma imagen por digest a producción.
- Documentar rollback de app y migraciones.

**Aceptación:** un release pasa por staging y promoción sin rebuild; producción puede volver a la imagen anterior mediante procedimiento probado.

#### P5.2 Monitoreo propio

- Integrar `sentry-sdk` o alternativa en Pulsyr con ambientes, release/commit y filtrado de PII.
- Instrumentar errores backend, fallos de jobs y errores frontend esenciales.
- Añadir `/health/live` y `/health/ready`; readiness valida dependencias críticas con timeout.
- Monitor externo de uptime sobre landing, login y readiness; alertas con responsable y canal.
- Definir SLO inicial y runbook: disponibilidad, latencia, tasa 5xx, cola de jobs y DB.

**Aceptación:** una excepción controlada llega al entorno correcto y dispara alerta de prueba; el runbook permite localizar release y request ID.

#### P5.3 Analítica y medición SEO

- Instalar Google Search Console y Bing Webmaster Tools para el dominio público; enviar sitemap.
- Elegir una sola analítica: opción ligera sin cookies o GA4 con consentimiento cuando legalmente corresponda.
- Definir eventos mínimos: CTA a app, GitHub, documentación, quick start completado y contacto.
- Excluir staging, tráfico interno y datos sensibles; no instrumentar contenido privado del backlog.
- Añadir verificación que impida cargar el snippet dos veces.

**Aceptación:** cada evento tiene nombre, propósito, owner y retención; se valida en tiempo real sin duplicados.

#### P5.4 Dependencias y cadena de suministro

- Renovate/Dependabot semanal para Python, GitHub Actions y frontend.
- `pip-audit` (o equivalente), auditoría npm si se adopta Astro, y escaneo de imagen de contenedor en CI.
- Generar SBOM de release y firmar/atestiguar imagen cuando el pipeline lo permita.
- Política de actualización con SLA por severidad y excepción documentada.

**Aceptación:** CI bloquea vulnerabilidades críticas/altas explotables o exige excepción aprobada con vencimiento.

### P6 — Automatización, lanzamiento y mejora continua (2–3 días + seguimiento)

#### P6.1 Gates de CI

Añadir jobs separados y cacheados:

- Crawl SEO: status, canonical, title, description, `h1`, links, noindex y sitemap.
- HTML validation.
- Playwright smoke en navegadores principales.
- axe para accesibilidad.
- Lighthouse CI para páginas públicas representativas.
- Tests de cabeceras, caché y compresión.
- Link checker para enlaces internos/externos críticos.

**Aceptación:** cada gate tiene umbral explícito, artefacto descargable y excepción temporal documentada; evitar tests flaky que se ignoren por costumbre.

#### P6.2 Checklist de lanzamiento

- Validar redirects, canonical, hreflang, robots y sitemap en producción.
- Probar OG en WhatsApp/LinkedIn/X y fallback sin imagen.
- Ejecutar Rich Results Test, Schema validator, Lighthouse y Security Headers.
- En Search Console: inspeccionar homepage y páginas pilares, solicitar indexación y enviar sitemap.
- Confirmar que app/staging/login/setup no se indexan.
- Probar 404/500, recuperación de rollback, alerta de uptime y captura de error.

#### P6.3 Revisión 30/60/90 días

- 30 días: cobertura de índice, errores de crawling, CWV y conversiones.
- 60 días: queries no branded, CTR por página, enlaces internos y contenido con impresiones sin clic.
- 90 días: actualizar/combinar/eliminar contenido débil; decidir expansión editorial con datos.

## 6. Backlog priorizado

| ID | Prioridad | Entregable | Depende de | Estimación |
|---|---|---|---|---:|
| WEB-001 | P0 | Ignorar `.env.*` + secret scanning | — | 0,5–1 d |
| WEB-002 | P0 | Baseline externo TLS/headers/Lighthouse/consola | acceso prod | 0,5–1 d |
| WEB-003 | P0 | ADR dominio, idiomas y canonical | negocio | 0,5 d |
| WEB-004 | P0 | Sitio público SSG + pipeline | WEB-003 | 3–5 d |
| WEB-005 | P0 | IA, copy y páginas pilares | WEB-003 | 4–8 d contenido |
| WEB-006 | P0 | Política index/noindex y OpenAPI prod | WEB-003 | 1–2 d |
| WEB-007 | P0 | Metadata, canonical, OG y hreflang | WEB-004 | 1–2 d |
| WEB-008 | P0 | Sitemap + robots | WEB-004, WEB-006 | 0,5–1 d |
| WEB-009 | P1 | JSON-LD validado | WEB-005, WEB-007 | 1 d |
| WEB-010 | P1 | 404/500 HTML y request ID | — | 1–2 d |
| WEB-011 | P0 | Build CSS/JS local y assets hashed | — | 2–4 d |
| WEB-012 | P0 | CSP y cabeceras | WEB-011 | 1–2 d |
| WEB-013 | P0 | CSRF para sesión | — | 1–2 d |
| WEB-014 | P0 | Secretos de integración write-only | WEB-001 | 1–2 d |
| WEB-015 | P1 | Compresión y políticas de caché | WEB-011 | 1–2 d |
| WEB-016 | P1 | Optimización de imágenes/fuentes/CLS | WEB-004 | 1–2 d |
| WEB-017 | P1 | Semántica `h1` y Markdown | — | 0,5–1 d |
| WEB-018 | P1 | Focus, modales y teclado | — | 2–3 d |
| WEB-019 | P1 | Tap targets y patrón de formularios | WEB-018 | 2–3 d |
| WEB-020 | P1 | Responsive + consola limpia | WEB-011 | 1–2 d |
| WEB-021 | P1 | Staging y promoción por digest | pipeline | 2–4 d |
| WEB-022 | P0 | Sentry propio + health + uptime | — | 2–3 d |
| WEB-023 | P1 | Search Console + analítica única | WEB-004, legal | 1–2 d |
| WEB-024 | P1 | Auditoría de dependencias/SBOM | — | 1–2 d |
| WEB-025 | P1 | Playwright + axe + Lighthouse CI + crawl SEO | WEB-004, WEB-011 | 3–5 d |
| WEB-026 | P2 | `/llms.txt` experimental | WEB-005 | 0,25 d |

## 7. Secuencia recomendada de PRs

1. **PR 1 — Contención:** WEB-001 y tests de secretos.
2. **PR 2 — Frontera SEO:** WEB-003 y WEB-006; noindex privado antes de publicar marketing.
3. **PR 3 — Frontend production-ready:** WEB-011 y base de WEB-015.
4. **PR 4 — Sitio público:** WEB-004 y estructura de WEB-005.
5. **PR 5 — SEO técnico:** WEB-007, WEB-008, WEB-009, WEB-010 y WEB-017.
6. **PR 6 — Seguridad:** WEB-012, WEB-013 y WEB-014.
7. **PR 7 — A11y/performance:** WEB-016, WEB-018, WEB-019 y WEB-020.
8. **PR 8 — Operación:** WEB-021, WEB-022, WEB-023 y WEB-024.
9. **PR 9 — Gates y lanzamiento:** WEB-025 y opcionalmente WEB-026.

Cada PR debe ser desplegable por sí mismo, incluir migración/rollback cuando corresponda y no mezclar copy masivo con refactors de seguridad.

## 8. Definition of Done global

El programa se considera terminado cuando:

- Existe una superficie pública útil y rastreable en el dominio canonical.
- Todo HTML público relevante está en el source inicial, con un `h1`, title, description y canonical únicos.
- Robots y sitemap sólo exponen URLs públicas válidas; app y staging están `noindex`.
- OG/social cards y JSON-LD pasan validadores y reflejan contenido visible.
- No se sirve Tailwind CDN ni JavaScript innecesario; existen presupuestos de peso y CWV.
- Cabeceras, CSRF, higiene de secretos, caché y compresión tienen tests automáticos.
- WCAG 2.2 AA se valida automática y manualmente en flujos críticos.
- CI falla ante errores de consola, enlaces rotos, regresión Lighthouse o metadata ausente.
- Staging, rollback, health checks, error tracking y uptime alerts están probados.
- Search Console y una sola solución analítica recogen datos sin duplicación ni contenido sensible.
- Hay owner para revisión 30/60/90 días y backlog basado en datos.

## 9. KPIs

### Técnicos

- 100 % de URLs públicas válidas con metadata completa.
- 0 URLs privadas en sitemap o índice.
- LCP ≤ 2,5 s, INP ≤ 200 ms y CLS ≤ 0,1 al p75 cuando existan datos de campo.
- 0 violaciones axe serious/critical y 0 errores de consola en smoke tests.
- 100 % de assets versionados con caché larga; HTML sensible `private/no-store`.
- Disponibilidad/SLO definido y alertas de prueba exitosas.

### Negocio/SEO

- Páginas indexadas frente a páginas enviadas.
- Impresiones y clics branded/no branded.
- CTR orgánico por página y query.
- Conversiones desde contenido público a GitHub, documentación, app o contacto.
- Backlinks/referring domains de calidad.
- Activación posterior al CTA, si puede medirse sin cruzar datos sensibles.

No usar “número de páginas publicadas”, score Lighthouse aislado ni presencia de `llms.txt` como KPI de éxito.

## 10. Riesgos y mitigaciones

| Riesgo | Mitigación |
|---|---|
| Crear marketing sin capacidad editorial | Lanzar pocas páginas pilares y asignar owner antes de abrir blog. |
| Indexar datos privados | Separación por host + `X-Robots-Tag` + pruebas; robots no es seguridad. |
| CSP rompe HTMX/OAuth/UI | Retirar inline scripts primero, activar Report-Only, revisar reportes y luego enforcement. |
| HSTS bloquea subdominios mal configurados | Validar TLS de todos los subdominios antes de `includeSubDomains`/preload. |
| Dos stacks elevan mantenimiento | Astro sólo para contenido público estático; FastAPI sigue siendo el producto. |
| Score de laboratorio se optimiza sin impacto real | Comparar Lighthouse con CWV de campo y conversiones. |
| Schema o copy exageran capacidades | Revisión de producto; sólo datos visibles, verificables y actuales. |
| Analítica invade privacidad | Minimización de eventos, sin contenido de backlog, retención definida y consentimiento cuando aplique. |
| Cambios de frontend pisan trabajo local | PRs pequeños y revisión explícita de los archivos actualmente modificados. |

## 11. Referencias normativas

- [Google: títulos descriptivos y únicos](https://developers.google.com/search/docs/appearance/title-link)
- [Google: meta descriptions y snippets](https://developers.google.com/search/docs/appearance/snippet)
- [Google: canonicalización](https://developers.google.com/search/docs/crawling-indexing/consolidate-duplicate-urls)
- [Google: crear y enviar sitemap](https://developers.google.com/search/docs/crawling-indexing/sitemaps/build-sitemap)
- [Google: datos estructurados y validación](https://developers.google.com/search/docs/appearance/structured-data/intro-structured-data)
- [web.dev: umbrales Core Web Vitals](https://web.dev/articles/defining-core-web-vitals-thresholds)
- [W3C: WCAG 2.2](https://www.w3.org/TR/WCAG22/)
- [OWASP: HTTP Security Response Headers](https://cheatsheetseries.owasp.org/cheatsheets/HTTP_Headers_Cheat_Sheet.html)
- [OWASP: Content Security Policy](https://cheatsheetseries.owasp.org/cheatsheets/Content_Security_Policy_Cheat_Sheet.html)

### Nota sobre `llms.txt`

El checklist de origen lo presenta como necesario para recomendaciones de IA. A agosto de 2026 sigue siendo una convención comunitaria, no un estándar W3C/IETF ni un requisito demostrado de Google. Se puede publicar por su bajo costo y utilidad como índice legible, pero el trabajo prioritario sigue siendo contenido público accesible, enlaces internos claros, sitemap, datos estructurados veraces y autorización intencional de crawlers.
