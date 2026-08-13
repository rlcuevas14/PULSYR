# ADR 0003: usar video ambiental como fondo del sitio público

- **Estado:** aceptada
- **Fecha:** 2026-08-13
- **Decisores:** mantenedor de Pulsyr + implementación Codex
- **Alcance:** portada pública en inglés y español

## Contexto

La portada necesita movimiento de marca sin presentar los clips como demostraciones del
producto. Los videos entregados son piezas ambientales: no contienen una narración ni una
interfaz que justifique un reproductor autónomo. Mostrarlos como tarjetas con controles
rompía la jerarquía editorial y añadía bloques sin contenido propio.

El sitio público también mantiene un contrato estricto: HTML estático indexable, sin
JavaScript ejecutable inesperado, presupuesto de peso explícito, accesibilidad y soporte
responsive desde 320 px.

## Decisión

1. El clip `pulsyr-hero.mp4` cubre el hero completo de `/` y `/es/` como fondo
   decorativo detrás del título, descripción y CTA.
2. El clip `pulsyr-signal.mp4` se usa una sola vez, como fondo de contraste de la sección
   de flujo de la portada. El texto, los cuatro pasos y los CTA permanecen por encima.
3. Producto y MCP no muestran videos autónomos. No se reutiliza el segundo clip en todas
   las páginas.
4. Los elementos `<video>` son decorativos: `aria-hidden`, sin controles, silenciados,
   en loop, `playsinline` y con reproducción automática nativa.
5. No se añade JavaScript para controlar la reproducción. Esto conserva el contrato SSG
   y evita que el auditor SEO detecte código ejecutable en las rutas indexables.
6. Los pósteres WebP entregan el primer cuadro. Con `prefers-reduced-motion: reduce` el
   video se oculta y queda el fondo estático del componente.
7. Degradados y capas oscuras aseguran contraste de lectura. El último carácter del hero
   español se prueba a 320, 1024 y 1440 px.
8. Los archivos permanecen locales bajo `site/public/media/`; no se depende de las páginas
   de Grok ni de un reproductor de terceros.
9. El validador HTML desactiva únicamente la regla `no-autoplay`: la excepción se limita a
   videos decorativos sin audio y las pruebas verifican que no tengan controles.

## Consecuencias

### Positivas

- El movimiento forma parte de la composición y no compite con el contenido.
- La portada conserva CTA y semántica HTML por encima de los fondos.
- No hay dependencia de JavaScript ni de un proveedor externo de video.
- Los clips sólo se usan donde aportan identidad y contraste.

### Costos y límites

- El navegador puede descargar el video secundario antes de que entre en pantalla; su
  tamaño se mantiene bajo el presupuesto de 6 MiB para limitar el impacto.
- La reproducción automática puede ser bloqueada por políticas excepcionales del
  navegador; el póster conserva una presentación completa en ese caso.
- Cualquier reemplazo de los MP4 debe regenerar los pósteres y volver a ejecutar los
  presupuestos, Playwright, HTML, SEO y Lighthouse.

## Evidencia de aceptación

- Implementación final en `main`: `968b0c0`.
- CI: run `31694685928`, todos los jobs aprobados.
- Cloudflare Worker: versión `99a2f4b2-9492-42df-8af9-42ecf93a36e8`.
- Producción validada en `https://pulsyr.dev`: dos fondos en portada, ningún reproductor
  autónomo en Producto o MCP y hero español contenido en móvil.
